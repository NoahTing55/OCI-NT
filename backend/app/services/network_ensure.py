"""一键建网服务：OCI-Start buildSimpleAllNetWork 思路。

表单不再强制用户手填子网 OCID；抢机 / 批量创建 worker 在 launch 之前
调这里，按「有则复用、无则创建」自动备好 VCN→Internet Gateway→路由表
→子网。API 层（api/oci_options.py 的 POST /ensure-network）只是薄封装。
"""
import asyncio

import httpx
from fastapi import HTTPException

from app.core.oci_client import OciClient
from app.core.oci_factory import build_client_for_account
from app.models.models import Account

# 我们自己创建的资源用的固定名字：重复调用先按名字找，找不到才复用
# 用户已有的，实在没有才创建，保证幂等不建一堆。
MANAGED_VCN_NAME = "oci-panel-vcn"
MANAGED_IG_NAME = "oci-panel-ig"
MANAGED_SUBNET_NAME = "oci-panel-subnet"
# 子网 CIDR 候选：第一个被占用就顺延
SUBNET_CIDR_CANDIDATES = ("10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24")

# 同一（租户，区域，compartment）的建网操作串行化：批量创建多 item 并发时
# 避免同时看到"没有"而建出重复 VCN。
_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


def _network_lock(tenancy_ocid: str, region: str, compartment_id: str) -> asyncio.Lock:
    key = (tenancy_ocid, region or "", compartment_id or "")
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _check_ok(resp: httpx.Response, what: str) -> None:
    """OCI 返回非 2xx 时转成 400 中文错误（不暴露 500）。"""
    if resp.status_code >= 300:
        detail = ""
        try:
            detail = resp.json().get("message", "") or resp.text[:200]
        except Exception:
            detail = resp.text[:200]
        raise HTTPException(
            status_code=400,
            detail=f"建网{what}失败：OCI 返回 {resp.status_code}（{detail}）",
        )


def _available(items: list[dict]) -> list[dict]:
    """只保留 AVAILABLE 状态的资源。"""
    return [i for i in items if i.get("lifecycleState") == "AVAILABLE"]


def _prefer_managed(items: list[dict], name: str) -> dict | None:
    """优先找我们自己建的（按固定名字），没有再取第一个可用的。"""
    avail = _available(items)
    for i in avail:
        if i.get("displayName") == name:
            return i
    return avail[0] if avail else None


async def _wait_vcn_available(client: OciClient, vcn_id: str, timeout: float = 60) -> dict:
    """轮询等 VCN 到 AVAILABLE（创建是异步的），超时抛 400 中文错。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        resp = await client.get_vcn(vcn_id)
        if resp.status_code < 300:
            vcn = resp.json()
            if vcn.get("lifecycleState") == "AVAILABLE":
                return vcn
        if loop.time() >= deadline:
            raise HTTPException(status_code=400, detail="VCN 创建超时：60 秒内未到 AVAILABLE 状态")
        await asyncio.sleep(3)


async def _ensure_network(
    client: OciClient,
    tenancy_ocid: str,
    availability_domain: str = "",
    compartment_id: str = "",
) -> dict:
    """建网编排本体：client 为鸭子类型，测试可用 fake 注入。

    返回 {"subnet_ocid","vcn_ocid","vcn_name","availability_domain","created"}，
    created 标明 vcn / ig / subnet 哪些是本次新建的。
    """
    comp = (compartment_id or "").strip() or tenancy_ocid
    created = {"vcn": False, "ig": False, "subnet": False}

    # 1. 可用域：没传就取该区域第一个
    ad = (availability_domain or "").strip()
    if not ad:
        resp = await client.request(
            "GET", "identity", f"/20160918/availabilityDomains?compartmentId={tenancy_ocid}"
        )
        _check_ok(resp, "查询可用域")
        ads = resp.json()
        if not ads or not ads[0].get("name"):
            raise HTTPException(status_code=400, detail="该区域未返回可用域")
        ad = ads[0]["name"]

    # 2. VCN：优先复用我们建过的，其次复用用户已有的，最后才创建
    try:
        vcns = await client.list_vcns(comp)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    vcn = _prefer_managed(vcns, MANAGED_VCN_NAME)
    if vcn is None:
        resp = await client.create_vcn(comp, display_name=MANAGED_VCN_NAME)
        _check_ok(resp, "创建 VCN")
        vcn_id = resp.json().get("id", "")
        if not vcn_id:
            raise HTTPException(status_code=400, detail="创建 VCN 后未返回 OCID")
        vcn = await _wait_vcn_available(client, vcn_id)
        created["vcn"] = True
    vcn_id = vcn.get("id", "")
    vcn_name = vcn.get("displayName", "")

    # 3. Internet Gateway：有可用的复用，没有就建
    try:
        igs = await client.list_internet_gateways(comp, vcn_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ig = _prefer_managed(igs, MANAGED_IG_NAME)
    if ig is None:
        resp = await client.create_internet_gateway(comp, vcn_id, display_name=MANAGED_IG_NAME)
        _check_ok(resp, "创建 Internet Gateway")
        ig = resp.json()
        created["ig"] = True
    ig_id = ig.get("id", "")

    # 4. 路由表：取第一个可用的，确保有 0.0.0.0/0 → IG 的默认公网路由
    try:
        rts = await client.list_route_tables(comp, vcn_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    avail_rts = _available(rts)
    rt = avail_rts[0] if avail_rts else None
    if rt is None:
        raise HTTPException(status_code=400, detail="该 VCN 下没有可用的路由表")
    rt_id = rt.get("id", "")
    rules = list(rt.get("routeRules") or [])
    has_default_route = any(
        r.get("destination") == "0.0.0.0/0" and r.get("networkEntityId") == ig_id
        for r in rules
    )
    if not has_default_route:
        rules.append({
            "destination": "0.0.0.0/0",
            "destinationType": "CIDR_BLOCK",
            "networkEntityId": ig_id,
            "description": "oci-panel 一键建网：默认公网路由",
        })
        resp = await client.update_route_table(rt_id, rules)
        _check_ok(resp, "更新路由表")

    # 5. 子网：该 VCN + 该 AD 下优先复用我们建过的，其次复用已有的；
    # 目标 AD 无可用子网时，复用 VCN 内其他 AD 的可用子网（并切换 AD）；
    # VCN 内完全没有可用子网时才尝试创建（避免与已有大 CIDR 如 10.0.0.0/16 冲突）
    try:
        subnets = await client.list_subnets_of_vcn(comp, vcn_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    in_ad = [s for s in subnets if s.get("availabilityDomain") == ad]
    subnet = _prefer_managed(in_ad, MANAGED_SUBNET_NAME)
    if subnet is None:
        # 目标 AD 没有可用子网，看 VCN 内其他 AD 有没有
        other = _prefer_managed(
            [s for s in subnets if s.get("availabilityDomain") != ad],
            MANAGED_SUBNET_NAME,
        )
        if other is not None:
            subnet = other
            ad = other.get("availabilityDomain", ad)  # 切换到子网所在的 AD
    if subnet is None:
        last_err = ""
        for cidr in SUBNET_CIDR_CANDIDATES:
            resp = await client.create_subnet(
                comp, vcn_id, ad, cidr_block=cidr,
                display_name=MANAGED_SUBNET_NAME, route_table_id=rt_id,
            )
            if resp.status_code < 300 and resp.json().get("id"):
                subnet = resp.json()
                created["subnet"] = True
                break
            try:
                last_err = resp.json().get("message", "") or resp.text[:200]
            except Exception:
                last_err = resp.text[:200]
        if subnet is None:
            raise HTTPException(
                status_code=400,
                detail=f"创建子网失败（{', '.join(SUBNET_CIDR_CANDIDATES)} 都被占用或无权限）：{last_err}",
            )

    return {
        "subnet_ocid": subnet.get("id", ""),
        "vcn_ocid": vcn_id,
        "vcn_name": vcn_name,
        "availability_domain": ad,
        "created": created,
    }


async def ensure_network(
    account: Account,
    region: str = "",
    availability_domain: str = "",
    compartment_id: str = "",
) -> dict:
    """一键建网入口（API / worker 共用）：按账号构建带代理的 client。

    account 需已加载 proxy（worker 里用 joinedload）。返回同 _ensure_network。
    """
    client = build_client_for_account(account)
    if region and region.strip():
        client.region = region.strip()
    try:
        async with _network_lock(account.tenancy_ocid, client.region, compartment_id or ""):
            return await _ensure_network(
                client, account.tenancy_ocid,
                availability_domain=availability_domain,
                compartment_id=compartment_id,
            )
    finally:
        await client.aclose()


async def ensure_subnet(
    account: Account,
    region: str = "",
    availability_domain: str = "",
) -> str:
    """worker 用的便捷入口：只返回可用的 subnet ocid。"""
    result = await ensure_network(
        account, region=region, availability_domain=availability_domain
    )
    return result["subnet_ocid"]

"""OCI 选项查询：给抢机/批量创建表单用的级联下拉数据。

按账号（走该账号绑定的代理）实时查询 OCI，返回可用域、平台镜像、
子网、compartment 列表，前端用 el-select allow-create 展示，保留手动填 OCID。
"""
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.oci_client import OciClient
from app.core.oci_factory import build_client_for_account
from app.models.models import Account
from app.schemas.schemas import EnsureNetworkIn
from app.services import network_ensure

router = APIRouter()


def _get_account(db: Session, account_id: int) -> Account:
    """取账号，不存在抛 404。"""
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


def _build_client(account: Account, region: str) -> OciClient:
    """按账号构建带代理的 client；region 参数非空时覆盖账号默认区域。"""
    try:
        client = build_client_for_account(account)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"构建 OCI 客户端失败：{e}")
    if region:
        client.region = region.strip()
    return client


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
            detail=f"查询{what}失败：OCI 返回 {resp.status_code}（{detail}）",
        )


@router.get("/availability-domains")
async def list_availability_domains(
    account_id: int = Query(...),
    region: str = Query(""),
    db: Session = Depends(get_db),
):
    """可用域列表：GET identity /20160918/availabilityDomains。"""
    account = _get_account(db, account_id)
    client = _build_client(account, region)
    try:
        resp = await client.request(
            "GET", "identity",
            f"/20160918/availabilityDomains?compartmentId={account.tenancy_ocid}",
        )
        _check_ok(resp, "可用域")
        return [{"name": ad.get("name", "")} for ad in resp.json()]
    finally:
        await client.aclose()


@router.get("/images")
async def list_platform_images(
    account_id: int = Query(...),
    region: str = Query(""),
    arch: str = Query("", description="按架构过滤：arm / amd，空则不过滤"),
    db: Session = Depends(get_db),
):
    """平台镜像列表（OCI-Start listImagesByShape 思路）：服务端按 operatingSystem
    精确过滤，从 displayName 判断架构（含 aarch64/ampere → ARM，否则 AMD），
    按 os+版本+架构去重保留 timeCreated 最新的，arch 非空则按架构过滤。
    返回 [{ocid, operating_system, operating_system_version, architecture, display_name}]，
    按 os、版本排序，前端做「操作系统 → 系统版本」两级下拉。"""
    account = _get_account(db, account_id)
    client = _build_client(account, region)
    try:
        # (os, version, arch) -> item；查询已按 timeCreated 倒序，首次出现即最新
        seen: dict[tuple[str, str, str], dict] = {}
        for os_name in ("Canonical Ubuntu", "Oracle Linux"):
            qs = (
                f"compartmentId={account.tenancy_ocid}"
                f"&operatingSystem={quote(os_name, safe='')}"
                "&sortBy=TIMECREATED&sortOrder=DESC&limit=10"
            )
            resp = await client.request("GET", "iaas", f"/20160918/images?{qs}")
            _check_ok(resp, "镜像")
            for i in resp.json():
                # 官方平台镜像：compartmentId 为 null
                if i.get("compartmentId"):
                    continue
                disp = i.get("displayName", "") or ""
                low = disp.lower()
                architecture = "ARM" if ("aarch64" in low or "ampere" in low) else "AMD"
                key = (i.get("operatingSystem", "") or "",
                       i.get("operatingSystemVersion", "") or "",
                       architecture)
                if key not in seen:
                    seen[key] = {
                        "ocid": i.get("id", ""),
                        "operating_system": i.get("operatingSystem", ""),
                        "operating_system_version": i.get("operatingSystemVersion", ""),
                        "architecture": architecture,
                        "display_name": disp,
                    }
        arch_filter = (arch or "").strip().lower()
        result = [
            item for item in seen.values()
            if arch_filter not in ("arm", "amd") or item["architecture"].lower() == arch_filter
        ]
        result.sort(key=lambda x: (x["operating_system"], x["operating_system_version"]))
        return result
    finally:
        await client.aclose()


@router.get("/compartments")
async def list_compartments(
    account_id: int = Query(...),
    region: str = Query(""),
    db: Session = Depends(get_db),
):
    """Compartment 列表：identity 服务 /20160918/compartments（含子树），
    根 tenancy 手动放在第一位，方便前端默认选中。"""
    account = _get_account(db, account_id)
    client = _build_client(account, region)
    try:
        resp = await client.request(
            "GET", "identity",
            f"/20160918/compartments?compartmentId={account.tenancy_ocid}"
            "&compartmentIdInSubtree=true&accessLevel=ACCESSIBLE&limit=100",
        )
        _check_ok(resp, "Compartment")
        result = [{"ocid": account.tenancy_ocid, "name": "根 compartment（tenancy）"}]
        for c in resp.json():
            # 去重：根 tenancy 可能已在子树结果里
            if c.get("id") == account.tenancy_ocid:
                continue
            result.append({
                "ocid": c.get("id", ""),
                "name": c.get("displayName", "") or c.get("name", ""),
            })
        return result
    finally:
        await client.aclose()


@router.get("/subnets")
async def list_subnets(
    account_id: int = Query(...),
    region: str = Query(""),
    compartment_id: str = Query(""),
    db: Session = Depends(get_db),
):
    """子网列表：compartment_id 传了就只查该 compartment；为空则自动搜整个
    tenancy 树（用户 VCN 经常建在子 compartment 里），结果带 compartment 名、
    VCN 名和 CIDR 方便辨认。单个 compartment 查不到就跳过，不中断整体。"""
    account = _get_account(db, account_id)
    client = _build_client(account, region)
    try:
        # 确定要搜索的 compartment 范围
        if compartment_id.strip():
            comp_ids = [(compartment_id.strip(), "")]
        else:
            comp_ids = [(account.tenancy_ocid, "根 compartment")]
            try:
                comp_resp = await client.request(
                    "GET", "identity",
                    f"/20160918/compartments?compartmentId={account.tenancy_ocid}"
                    "&compartmentIdInSubtree=true&accessLevel=ACCESSIBLE&limit=100",
                )
                if comp_resp.status_code < 300:
                    for c in comp_resp.json():
                        cid = c.get("id", "")
                        if cid and cid != account.tenancy_ocid:
                            comp_ids.append((cid, c.get("displayName", "") or c.get("name", "")))
            except Exception:
                pass  # compartment 列表查不到就只搜根，不影响主流程
        result = []
        for comp, comp_name in comp_ids:
            try:
                vcn_resp = await client.request(
                    "GET", "iaas", f"/20160918/vcns?compartmentId={comp}&limit=50"
                )
                if vcn_resp.status_code >= 300:
                    continue
                for vcn in vcn_resp.json():
                    vcn_id = vcn.get("id", "")
                    vcn_name = vcn.get("displayName", "")
                    sub_resp = await client.request(
                        "GET", "iaas",
                        f"/20160918/subnets?compartmentId={comp}&vcnId={vcn_id}&limit=100",
                    )
                    if sub_resp.status_code >= 300:
                        continue
                    for s in sub_resp.json():
                        result.append({
                            "ocid": s.get("id", ""),
                            "display_name": s.get("displayName", ""),
                            "vcn_name": vcn_name,
                            "cidr": s.get("cidrBlock", ""),
                            "compartment_name": comp_name,
                        })
            except Exception:
                continue  # 某个 compartment 出错就跳过
        return result
    finally:
        await client.aclose()


@router.post("/ensure-network")
async def ensure_network(data: EnsureNetworkIn, db: Session = Depends(get_db)):
    """一键建网：按「有则复用、无则创建」备好 VCN→IG→路由→子网，返回子网 OCID。

    核心逻辑在 services/network_ensure.py（worker 直接调函数，不走 HTTP），
    这里只是薄封装。JWT 鉴权由路由注册统一处理。
    创建资源较慢，前端/调用方请把超时设到 120 秒。
    """
    account = _get_account(db, data.account_id)
    return await network_ensure.ensure_network(
        account,
        region=data.region,
        availability_domain=data.availability_domain,
        compartment_id=data.compartment_id,
    )

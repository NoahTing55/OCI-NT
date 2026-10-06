"""更换 IP：预留 IP 标准流程；成功后自动触发 CF DNS 同步。

主流程（官方支持）：
1. 查实例主 VNIC → 旧公网 IP；查主私网 IP id；查旧公网 IP 记录（判断是否为预留）；
2. 操作前把旧 IP 写入审计日志（便于回滚排查）；
3. 创建新的预留 IP（lifetime=RESERVED）；
4. 把新预留 IP 绑定到主私网 IP（同一私网 IP 同时只能绑一个公网 IP，
   新绑定会自动顶掉旧绑定，无需手动解绑）；
5. 可选：释放旧的预留 IP（仅当旧的是 RESERVED 且用户勾选）；
6. 回查 VNIC 确认新 IP 生效；
7. 自动触发该实例所有 auto_sync 域名绑定的 CF DNS 同步；
8. TG 推送新 IP。

临时公网 IP：OCI 未提供直接更换接口，见 /ephemeral-ip/probe 的探针说明。
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import telegram
from app.core.deps import get_db
from app.core.oci_factory import build_client_for_account, compartment_of
from app.models.models import Account
from app.schemas.schemas import ChangeIpIn, ChangeIpOut, OpenPortsIn
from app.services.security_rules import ensure_allow_all_ingress
from app.services import cloudflare as cf_service
from app.services.instances import invalidate_instance_cache

router = APIRouter()

@router.post("/change-ip", response_model=ChangeIpOut)
async def change_ip(request: Request, data: ChangeIpIn, db: Session = Depends(get_db)):
    """预留 IP 换 IP。"""
    account = db.get(Account, data.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    client = build_client_for_account(account)
    try:
        compartment = compartment_of(account)

        # 1. 主 VNIC 与旧公网 IP
        atts = await client.list_vnic_attachments(compartment, data.instance_id)
        if not atts:
            raise HTTPException(status_code=400, detail="该实例没有 VNIC 附件")
        vnic_id = atts[0]["vnicId"]
        vnic = (await client.get_vnic(vnic_id)).json()
        old_public_ip = vnic.get("publicIp")

        # 2. 主私网 IP
        privates = await client.list_private_ips(vnic_id)
        primary = next((p for p in privates if p.get("isPrimary")), None)
        if not primary:
            raise HTTPException(status_code=400, detail="找不到主私网 IP")
        private_ip_id = primary["id"]

        # 3. 旧公网 IP 记录（判断 lifetime 是否为 RESERVED，决定能否释放）
        old_pubs = await client.list_public_ips(compartment, private_ip_id=private_ip_id)
        old_pub = old_pubs[0] if old_pubs else None
        old_pub_id = old_pub["id"] if old_pub else None
        old_lifetime = old_pub.get("lifetime") if old_pub else None

        # 4. 旧 IP 记到审计摘要（中间件统一落库，便于回滚排查）

        # 5. 创建新的预留 IP
        r = await client.create_public_ip(compartment, display_name=f"panel-{int(time.time())}")
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"创建预留 IP 失败：HTTP {r.status_code} {r.text[:200]}")
        new_pub = r.json()
        new_pub_id = new_pub["id"]

        # 6. 绑定到主私网 IP（自动顶掉旧绑定）
        r2 = await client.update_public_ip(new_pub_id, private_ip_id)
        if r2.status_code != 200:
            raise HTTPException(status_code=502, detail=f"绑定预留 IP 失败：HTTP {r2.status_code} {r2.text[:200]}")

        # 7. 可选释放旧的预留 IP（临时 IP 无记录可删，直接跳过）
        released_old = None
        if data.release_old and old_pub_id and old_lifetime == "RESERVED" and old_pub_id != new_pub_id:
            r3 = await client.delete_public_ip(old_pub_id)
            if r3.status_code in (200, 202, 204):
                released_old = old_pub_id

        # 8. 回查确认新 IP 生效
        vnic2 = (await client.get_vnic(vnic_id)).json()
        effective_ip = vnic2.get("publicIp") or new_pub.get("ipAddress")

        # 9. CF DNS 自动同步（该实例 auto_sync 的域名）
        cf_results = await cf_service.sync_instance_domains(db, data.instance_id, effective_ip)

        # 10. TG 推送
        cf_text = ""
        if cf_results:
            ok_n = len([x for x in cf_results if x["ok"]])
            cf_text = f"\nCF 同步：{ok_n}/{len(cf_results)} 个域名成功"
        await telegram.send_message(
            f"【换 IP 成功】账号 {account.name}\n实例 {data.instance_id}\n"
            f"{old_public_ip} → {effective_ip}{cf_text}"
        )

        # 审计摘要覆写：旧 IP → 新 IP 映射是回滚关键信息，中间件统一落库
        request.state.audit_detail = (
            f"实例 {data.instance_id} 换 IP：{old_public_ip} → {effective_ip}，"
            f"释放旧预留IP={released_old}"
        )
        request.state.audit_account_id = account.id
        # 换 IP 成功后失效实例列表缓存
        invalidate_instance_cache()
        return ChangeIpOut(
            old_ip=old_public_ip,
            new_ip=effective_ip,
            new_public_ip_id=new_pub_id,
            released_old_public_ip_id=released_old,
            cf_synced=cf_results,
        )
    finally:
        await client.aclose()

@router.post("/open-all-ports")
async def open_all_ports(request: Request, data: OpenPortsIn, db: Session = Depends(get_db)):
    """给指定实例的子网安全列表放行所有端口（protocol=all, 0.0.0.0/0）。"""
    account = db.get(Account, data.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    client = build_client_for_account(account)
    compartment = compartment_of(account)
    # 查实例主 VNIC → 子网
    atts = await client.list_vnic_attachments(compartment, data.instance_id)
    if not atts:
        raise HTTPException(status_code=400, detail="该实例没有 VNIC 附件")
    vnic = (await client.get_vnic(atts[0]["vnicId"])).json()
    subnet_id = vnic.get("subnetId")
    if not subnet_id:
        raise HTTPException(status_code=400, detail="未找到实例子网")
    ok = await ensure_allow_all_ingress(client, subnet_id)
    request.state.audit_detail = "安全组放行所有端口：账号 %s 实例 %s（%s）" % (account.name, data.instance_id, "成功" if ok else "失败")
    request.state.audit_account_id = account.id
    return {"ok": ok, "subnet_id": subnet_id,
            "message": "已放行所有端口" if ok else "操作失败，请检查日志"}


@router.post("/ephemeral-ip/probe")
async def ephemeral_ip_probe():
    """临时公网 IP 更换探针（未实现，待真实账号验证）。

    背景：OCI 没有提供直接更换临时公网 IP 的 API（UpdateVnic 不支持 public IP 字段）。
    探针计划（有真实账号后按序验证）：
    1. 解绑临时 IP 后是否会自动分配新的临时 IP；
    2. 若不自动分配，是否可通过「先解绑、再重建 VNIC」路线实现；
    3. 若以上皆不可行，则临时 IP 更换只能走重建实例路线。

    在探针结论出来前请使用预留 IP 流程（POST /api/network/change-ip）。
    注意：不要硬编码未经验证的字段，本接口仅返回探针计划。
    """
    return {
        "implemented": False,
        "message": "临时公网 IP 更换待 API 探针验证，当前仅支持预留 IP 流程",
        "probe_plan": [
            "1. 用真实账号调用 UpdateVnic，确认是否支持 public IP 相关字段",
            "2. 测试解绑临时 IP 后是否自动分配新临时 IP",
            "3. 若不自动分配，评估重建 VNIC 路线的可行性",
        ],
    }

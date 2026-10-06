"""Cloudflare：Token 管理、域名绑定、手动同步、一致性巡检。

- Token 明文只在创建请求的瞬间出现，Fernet 加密入库，任何接口都不返回；
- 删除 Token 前检查是否有域名绑定在用；
- 新建绑定时先到 CF 解析 zone_id，Token 权限不足会在这里直接暴露出来。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import decrypt_text, encrypt_text
from app.models.models import Account, CloudflareToken, DomainBinding
from app.schemas.schemas import (
    CfTokenCreate,
    CfTokenOut,
    DomainBindingCreate,
    DomainBindingOut,
)
from app.services import cloudflare as cf_service
from app.services.instances import get_instance_public_ip

router = APIRouter()

def _decrypt_token(db: Session, token_id: int) -> str:
    token = db.get(CloudflareToken, token_id)
    if not token:
        raise HTTPException(status_code=404, detail="CF Token 不存在")
    try:
        return decrypt_text(token.token_enc)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

def _binding_out(b: DomainBinding) -> DomainBindingOut:
    return DomainBindingOut.model_validate(b)

# ---------------- Token 管理 ----------------
@router.get("/tokens", response_model=list[CfTokenOut])
def list_tokens(db: Session = Depends(get_db)):
    """Token 列表（只返回元信息，不返回 Token 明文）。"""
    return [
        CfTokenOut(id=t.id, name=t.name, remark=t.remark, created_at=t.created_at)
        for t in db.query(CloudflareToken).order_by(CloudflareToken.id.desc()).all()
    ]

@router.post("/tokens", response_model=CfTokenOut)
def create_token(data: CfTokenCreate, db: Session = Depends(get_db)):
    if len(data.token.strip()) < 10:
        raise HTTPException(status_code=400, detail="Token 看起来太短，请检查是否复制完整")
    t = CloudflareToken(name=data.name.strip(), token_enc=encrypt_text(data.token.strip()), remark=data.remark)
    db.add(t)
    db.commit()
    db.refresh(t)
    return CfTokenOut(id=t.id, name=t.name, remark=t.remark, created_at=t.created_at)

@router.delete("/tokens/{token_id}")
def delete_token(token_id: int, db: Session = Depends(get_db)):
    t = db.get(CloudflareToken, token_id)
    if not t:
        raise HTTPException(status_code=404, detail="CF Token 不存在")
    used = db.query(DomainBinding).filter(DomainBinding.cf_token_id == token_id).count()
    if used:
        raise HTTPException(status_code=400, detail=f"该 Token 正被 {used} 个域名绑定使用，请先删除绑定")
    name = t.name
    db.delete(t)
    db.commit()
    return {"ok": True}

# ---------------- 域名绑定 ----------------
@router.get("/bindings", response_model=list[DomainBindingOut])
def list_bindings(db: Session = Depends(get_db)):
    return [
        _binding_out(b)
        for b in db.query(DomainBinding).order_by(DomainBinding.id.desc()).all()
    ]

@router.post("/bindings", response_model=DomainBindingOut)
async def create_binding(data: DomainBindingCreate, db: Session = Depends(get_db)):
    """新建域名绑定：先到 CF 解析 zone_id，Token 权限不足会在这里直接报错。"""
    if data.record_type not in ("A", "AAAA"):
        raise HTTPException(status_code=400, detail="record_type 只支持 A / AAAA")
    token_plain = _decrypt_token(db, data.cf_token_id)
    client = cf_service.CloudflareClient(token_plain)
    try:
        zone_id = await client.find_zone_id(data.domain)
    except cf_service.CloudflareError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        # 网络层异常（连接超时、DNS 失败等）也转为 502，避免裸 500
        raise HTTPException(status_code=502, detail=f"连接 CF API 失败：{type(e).__name__} {str(e)[:150]}")
    finally:
        await client.aclose()
    if not zone_id:
        raise HTTPException(status_code=400, detail=f"CF 上找不到域名 {data.domain} 的 zone（Token 权限或域名有误）")

    b = DomainBinding(
        cf_token_id=data.cf_token_id,
        account_id=data.account_id,
        instance_ocid=data.instance_ocid.strip(),
        instance_name=data.instance_name or "",
        domain=data.domain.strip().lower(),
        record_type=data.record_type,
        zone_id=zone_id,
        proxied=data.proxied,
        ttl=data.ttl,
        auto_sync=data.auto_sync,
    )
    db.add(b)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="该域名 + 记录类型已存在绑定")
    db.refresh(b)
    return _binding_out(b)

@router.delete("/bindings/{binding_id}")
def delete_binding(binding_id: int, db: Session = Depends(get_db)):
    b = db.get(DomainBinding, binding_id)
    if not b:
        raise HTTPException(status_code=404, detail="绑定不存在")
    domain = b.domain
    db.delete(b)
    db.commit()
    return {"ok": True}

@router.post("/bindings/{binding_id}/sync")
async def sync_binding(binding_id: int, db: Session = Depends(get_db)):
    """手动同步：查实例当前公网 IP → 更新 CF 记录（有则更新、无则创建）。"""
    b = db.get(DomainBinding, binding_id)
    if not b:
        raise HTTPException(status_code=404, detail="绑定不存在")
    if not b.account_id:
        raise HTTPException(status_code=400, detail="该绑定未关联账号，无法查询实例实时 IP")
    account = db.get(Account, b.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="绑定的账号不存在")
    ip = await get_instance_public_ip(account, b.instance_ocid)
    if not ip:
        raise HTTPException(status_code=400, detail="查不到实例当前公网 IP（实例可能已终止或无公网 IP）")

    token_plain = _decrypt_token(db, b.cf_token_id)
    client = cf_service.CloudflareClient(token_plain)
    try:
        info = await client.ensure_record(b.domain, ip, b.record_type, ttl=b.ttl, proxied=b.proxied)
    except cf_service.CloudflareError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"连接 CF API 失败：{type(e).__name__} {str(e)[:150]}")
    finally:
        await client.aclose()

    b.record_id = info["record_id"]
    b.last_ip = ip
    b.last_sync_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True, "domain": b.domain, "ip": ip,
        "record_id": info["record_id"], "created": info["created"],
    }

@router.get("/check")
async def consistency_check(db: Session = Depends(get_db)):
    """一致性巡检：比对 CF 记录内容与本地 last_ip，返回不一致列表。

    last_ip 为空（从未同步过）的不算不一致，跳过。
    """
    mismatches = []
    bindings = db.query(DomainBinding).filter(DomainBinding.record_id != "").all()
    for b in bindings:
        if not b.last_ip:
            continue
        try:
            token_plain = _decrypt_token(db, b.cf_token_id)
            client = cf_service.CloudflareClient(token_plain)
            try:
                records = await client.list_records(b.zone_id, name=b.domain, record_type=b.record_type)
            finally:
                await client.aclose()
            rec = next((r for r in records if r["id"] == b.record_id), records[0] if records else None)
            cf_ip = rec["content"] if rec else None
            if cf_ip != b.last_ip:
                mismatches.append({
                    "binding_id": b.id, "domain": b.domain,
                    "cf_ip": cf_ip, "last_ip": b.last_ip,
                })
        except Exception as e:
            mismatches.append({"binding_id": b.id, "domain": b.domain, "error": str(e)[:150]})
    return {"mismatches": mismatches, "checked": len(bindings)}

@router.post("/sync-all")
async def sync_all(db: Session = Depends(get_db)):
    """一键同步全部 auto_sync 绑定。单个失败不影响其他。"""
    results = []
    bindings = db.query(DomainBinding).filter(DomainBinding.auto_sync == True).all()
    for b in bindings:
        try:
            r = await sync_binding(b.id, db)
            results.append({"domain": b.domain, "ok": True, "ip": r["ip"]})
        except HTTPException as e:
            results.append({"domain": b.domain, "ok": False, "error": e.detail})
        except Exception as e:
            results.append({"domain": b.domain, "ok": False, "error": str(e)[:150]})
    return {"results": results}

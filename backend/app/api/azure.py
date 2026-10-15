"""Azure 账号管理：CRUD + 订阅/Tier/消费查询（只读）。

认证：服务主体 client_credentials，client_secret Fernet 加密入库，永不返回前端。
Tier：Microsoft.CognitiveServices/quotaTiers → value[0].properties.currentTierName。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import azure_client
from app.core.deps import get_db
from app.core.security import decrypt_text, encrypt_text
from app.models.models import AzureAccount, Proxy
from app.schemas.schemas import (
    AzureAccountCreate, AzureAccountDetailOut, AzureAccountOut, AzureSubscriptionOut,
    AzureBatchImportRequest, AzureBatchImportResponse, BatchImportFail,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _proxy_url_for(acc: AzureAccount, db) -> str | None:
    """按账号绑定的代理拼 httpx proxy URL（socks5 用 socks5h）。"""
    if not acc.proxy_id:
        return None
    p = db.get(Proxy, acc.proxy_id)
    if not p:
        return None
    auth = ""
    if p.username:
        pwd = decrypt_text(p.password_enc) if p.password_enc else ""
        auth = f"{p.username}:{pwd}@"
    scheme = "socks5h" if p.scheme == "socks5" else "http"
    return f"{scheme}://{auth}{p.host}:{p.port}"


def _proxy_name_for(acc: AzureAccount, db) -> str | None:
    if not acc.proxy_id:
        return None
    p = db.get(Proxy, acc.proxy_id)
    return p.name if p else None


def _parse_cached(a: AzureAccount) -> list:
    if not a.cached_subscriptions:
        return []
    try:
        import json
        v = json.loads(a.cached_subscriptions)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _to_out(a: AzureAccount, db=None) -> AzureAccountOut:
    out = AzureAccountOut.model_validate(a)
    out.has_secret = bool(a.client_secret_enc)
    out.cached_subscriptions = _parse_cached(a)
    if db is not None:
        out.proxy_id = a.proxy_id
        out.proxy_name = _proxy_name_for(a, db)
    return out




@router.post("/batch-import", response_model=AzureBatchImportResponse)
def batch_import_azure_accounts(data: AzureBatchImportRequest, db: Session = Depends(get_db)):
    """批量导入 Azure 账号（JSON 数组）。单个失败不影响其他。"""
    created: list[int] = []
    failed: list[dict] = []
    for idx, item in enumerate(data.accounts):
        name = (item.name or "").strip() or f"Azure-{idx + 1}"
        try:
            if not item.tenant_id.strip() or not item.client_id.strip() or not item.client_secret:
                raise ValueError("tenant_id / client_id / client_secret 均不能为空")
            try:
                secret_enc = encrypt_text(item.client_secret)
            except RuntimeError as e:
                raise ValueError(str(e))
            acc = AzureAccount(
                name=name,
                tenant_id=item.tenant_id.strip(),
                client_id=item.client_id.strip(),
                client_secret_enc=secret_enc,
                remark=item.remark or "",
            )
            db.add(acc)
            db.commit()
            db.refresh(acc)
            created.append(acc.id)
        except ValueError as e:
            db.rollback()
            failed.append({"name": name, "error": str(e)})
        except Exception as e:
            db.rollback()
            logger.warning("Azure 批量导入第 %d 个异常：%s", idx + 1, e)
            failed.append({"name": name, "error": f"未知错误：{e}"})
    return AzureBatchImportResponse(
        created=created,
        failed=[BatchImportFail(**f) for f in failed],
    )

@router.get("", response_model=list[AzureAccountOut])
def list_azure_accounts(db: Session = Depends(get_db)):
    return [_to_out(a, db) for a in db.query(AzureAccount).order_by(AzureAccount.id).all()]


@router.post("", response_model=AzureAccountOut)
def create_azure_account(data: AzureAccountCreate, db: Session = Depends(get_db)):
    if not data.client_secret:
        raise HTTPException(status_code=400, detail="客户端密钥不能为空")
    try:
        secret_enc = encrypt_text(data.client_secret)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    proxy_id = getattr(data, "proxy_id", None)
    if proxy_id:
        _check_proxy_available(db, proxy_id)
    acc = AzureAccount(
        name=data.name.strip(),
        tenant_id=data.tenant_id.strip(),
        client_id=data.client_id.strip(),
        client_secret_enc=secret_enc,
        remark=data.remark or "",
        proxy_id=proxy_id,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _to_out(acc, db)


@router.put("/{account_id}", response_model=AzureAccountOut)
def update_azure_account(account_id: int, data: AzureAccountCreate, db: Session = Depends(get_db)):
    acc = db.get(AzureAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Azure 账号不存在")
    acc.name = data.name.strip()
    acc.tenant_id = data.tenant_id.strip()
    acc.client_id = data.client_id.strip()
    # 密钥留空则不修改
    if data.client_secret:
        try:
            acc.client_secret_enc = encrypt_text(data.client_secret)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        azure_client.invalidate_token(acc.tenant_id, acc.client_id)
    acc.remark = data.remark or ""
    new_proxy_id = getattr(data, "proxy_id", None)
    if new_proxy_id != acc.proxy_id:
        if new_proxy_id:
            _check_proxy_available(db, new_proxy_id, exclude_azure_id=acc.id)
        acc.proxy_id = new_proxy_id
    db.commit()
    db.refresh(acc)
    return _to_out(acc, db)


@router.delete("/{account_id}")
def delete_azure_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(AzureAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Azure 账号不存在")
    name = acc.name
    db.delete(acc)
    db.commit()
    return {"ok": True, "name": name}


def _check_proxy_available(db, proxy_id: int, exclude_azure_id: int | None = None):
    """校验代理存在且未被其他 AZ 账号占用。允许与 OCI 账号共用同一代理。"""
    from fastapi import HTTPException
    p = db.get(Proxy, proxy_id)
    if not p:
        raise HTTPException(status_code=400, detail="代理不存在")
    q = db.query(AzureAccount).filter(AzureAccount.proxy_id == proxy_id)
    if exclude_azure_id:
        q = q.filter(AzureAccount.id != exclude_azure_id)
    other = q.first()
    if other:
        raise HTTPException(status_code=400, detail=f"代理已被 Azure 账号「{other.name}」绑定")


def _query_account_detail(acc: AzureAccount, db) -> AzureAccountDetailOut:
    """查单个账号的订阅 + Tier + 消费。出错时 error 字段说明，不抛异常。"""
    out = AzureAccountDetailOut.model_validate(acc)
    out.has_secret = bool(acc.client_secret_enc)
    out.proxy_id = acc.proxy_id
    out.proxy_name = _proxy_name_for(acc, db)
    proxy_url = _proxy_url_for(acc, db)
    try:
        secret = decrypt_text(acc.client_secret_enc)
    except Exception as e:
        out.error = f"密钥解密失败：{e}"
        return out

    try:
        token = azure_client.get_azure_token(acc.tenant_id, acc.client_id, secret, proxy_url)
    except RuntimeError as e:
        out.error = str(e)
        return out

    try:
        subs = azure_client.list_subscriptions(token, proxy_url)
    except RuntimeError as e:
        if "AUTH_EXPIRED" in str(e):
            azure_client.invalidate_token(acc.tenant_id, acc.client_id)
            try:
                token = azure_client.get_azure_token(acc.tenant_id, acc.client_id, secret, proxy_url)
                subs = azure_client.list_subscriptions(token, proxy_url)
            except RuntimeError as e2:
                out.error = str(e2)
                return out
        else:
            out.error = str(e)
            return out

    for s in subs:
        tier = azure_client.get_quota_tier(token, s["subscription_id"], proxy_url)["tier"]
        cost_info = azure_client.get_month_cost(token, s["subscription_id"], proxy_url)
        out.subscriptions.append(AzureSubscriptionOut(
            subscription_id=s["subscription_id"],
            display_name=s["display_name"],
            state=s["state"],
            tier=tier,
            cost=cost_info["cost"],
            currency=cost_info["currency"],
        ))
    # 写缓存：下次打开直接展示
    try:
        import json
        from datetime import datetime
        acc.cached_subscriptions = json.dumps([x.model_dump() for x in out.subscriptions])
        acc.cached_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        logger.warning("Azure 缓存写入失败 %s：%s", acc.name, e)
    return out


@router.post("/{account_id}/refresh", response_model=AzureAccountDetailOut)
def refresh_azure_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(AzureAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Azure 账号不存在")
    return _query_account_detail(acc, db)


@router.post("/refresh-all", response_model=list[AzureAccountDetailOut])
def refresh_all_azure_accounts(db: Session = Depends(get_db)):
    """批量刷新全部 Azure 账号（20 个左右，串行查，失败单个不影响其他）。"""
    result = []
    for acc in db.query(AzureAccount).order_by(AzureAccount.id).all():
        try:
            result.append(_query_account_detail(acc, db))
        except Exception as e:
            logger.exception("Azure 账号 %s 刷新异常", acc.name)
            out = AzureAccountDetailOut.model_validate(acc)
            out.has_secret = bool(acc.client_secret_enc)
            out.proxy_id = acc.proxy_id
            out.proxy_name = _proxy_name_for(acc, db)
            out.error = f"内部异常：{e}"
            result.append(out)
    return result

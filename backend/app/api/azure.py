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
from app.models.models import AzureAccount
from app.schemas.schemas import (
    AzureAccountCreate, AzureAccountDetailOut, AzureAccountOut, AzureSubscriptionOut,
    AzureBatchImportRequest, AzureBatchImportResponse, BatchImportFail,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_out(a: AzureAccount) -> AzureAccountOut:
    out = AzureAccountOut.model_validate(a)
    out.has_secret = bool(a.client_secret_enc)
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
    return [_to_out(a) for a in db.query(AzureAccount).order_by(AzureAccount.id).all()]


@router.post("", response_model=AzureAccountOut)
def create_azure_account(data: AzureAccountCreate, db: Session = Depends(get_db)):
    if not data.client_secret:
        raise HTTPException(status_code=400, detail="客户端密钥不能为空")
    try:
        secret_enc = encrypt_text(data.client_secret)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    acc = AzureAccount(
        name=data.name.strip(),
        tenant_id=data.tenant_id.strip(),
        client_id=data.client_id.strip(),
        client_secret_enc=secret_enc,
        remark=data.remark or "",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _to_out(acc)


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
    db.commit()
    db.refresh(acc)
    return _to_out(acc)


@router.delete("/{account_id}")
def delete_azure_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(AzureAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Azure 账号不存在")
    name = acc.name
    db.delete(acc)
    db.commit()
    return {"ok": True, "name": name}


def _query_account_detail(acc: AzureAccount) -> AzureAccountDetailOut:
    """查单个账号的订阅 + Tier + 消费。出错时 error 字段说明，不抛异常。"""
    out = AzureAccountDetailOut.model_validate(acc)
    out.has_secret = bool(acc.client_secret_enc)
    try:
        secret = decrypt_text(acc.client_secret_enc)
    except Exception as e:
        out.error = f"密钥解密失败：{e}"
        return out

    try:
        token = azure_client.get_azure_token(acc.tenant_id, acc.client_id, secret)
    except RuntimeError as e:
        out.error = str(e)
        return out

    try:
        subs = azure_client.list_subscriptions(token)
    except RuntimeError as e:
        if "AUTH_EXPIRED" in str(e):
            azure_client.invalidate_token(acc.tenant_id, acc.client_id)
            try:
                token = azure_client.get_azure_token(acc.tenant_id, acc.client_id, secret)
                subs = azure_client.list_subscriptions(token)
            except RuntimeError as e2:
                out.error = str(e2)
                return out
        else:
            out.error = str(e)
            return out

    for s in subs:
        tier = azure_client.get_quota_tier(token, s["subscription_id"])["tier"]
        cost_info = azure_client.get_month_cost(token, s["subscription_id"])
        out.subscriptions.append(AzureSubscriptionOut(
            subscription_id=s["subscription_id"],
            display_name=s["display_name"],
            state=s["state"],
            tier=tier,
            cost=cost_info["cost"],
            currency=cost_info["currency"],
        ))
    return out


@router.post("/{account_id}/refresh", response_model=AzureAccountDetailOut)
def refresh_azure_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(AzureAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Azure 账号不存在")
    return _query_account_detail(acc)


@router.post("/refresh-all", response_model=list[AzureAccountDetailOut])
def refresh_all_azure_accounts(db: Session = Depends(get_db)):
    """批量刷新全部 Azure 账号（20 个左右，串行查，失败单个不影响其他）。"""
    result = []
    for acc in db.query(AzureAccount).order_by(AzureAccount.id).all():
        try:
            result.append(_query_account_detail(acc))
        except Exception as e:
            logger.exception("Azure 账号 %s 刷新异常", acc.name)
            out = AzureAccountDetailOut.model_validate(acc)
            out.has_secret = bool(acc.client_secret_enc)
            out.error = f"内部异常：{e}"
            result.append(out)
    return result

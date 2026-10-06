"""账号管理：CRUD + 代理一对一绑定。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import encrypt_text
from app.models.models import Account, Proxy
from app.schemas.schemas import AccountCreate, AccountOut, AccountUpdate, BindProxyIn

router = APIRouter()

def _to_out(account: Account) -> AccountOut:
    return AccountOut.model_validate(account)

@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return [_to_out(a) for a in db.query(Account).order_by(Account.id.desc()).all()]

@router.post("", response_model=AccountOut)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    if "PRIVATE KEY" not in data.private_key:
        raise HTTPException(status_code=400, detail="private_key 看起来不是 PEM 私钥")
    try:
        private_key_enc = encrypt_text(data.private_key)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # 新建时直接绑定代理：检查代理存在且未被其他账号占用
    if data.proxy_id is not None:
        proxy = db.get(Proxy, data.proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        if proxy.account is not None:
            raise HTTPException(
                status_code=400,
                detail=f"该代理已被账号「{proxy.account.name}」绑定（单API单代理，一代理只能绑一个账号）",
            )
    account = Account(
        name=data.name,
        tenancy_ocid=data.tenancy_ocid.strip(),
        user_ocid=data.user_ocid.strip(),
        fingerprint=data.fingerprint.strip(),
        private_key_enc=private_key_enc,
        region=data.region.strip(),
        compartment_ocid=data.compartment_ocid.strip(),
        remark=data.remark,
        proxy_id=data.proxy_id,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return _to_out(account)

@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _to_out(account)

@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    if data.name is not None:
        account.name = data.name
    if data.region is not None:
        account.region = data.region.strip()
    if data.compartment_ocid is not None:
        account.compartment_ocid = data.compartment_ocid.strip()
    if data.remark is not None:
        account.remark = data.remark
    if data.private_key:
        if "PRIVATE KEY" not in data.private_key:
            raise HTTPException(status_code=400, detail="private_key 看起来不是 PEM 私钥")
        try:
            account.private_key_enc = encrypt_text(data.private_key)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
    db.commit()
    db.refresh(account)
    return _to_out(account)

@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    # TODO(M3)：删除前检查是否有运行中的抢机任务，有则拒绝
    name = account.name
    db.delete(account)
    db.commit()
    return {"ok": True}

@router.post("/{account_id}/bind-proxy", response_model=AccountOut)
def bind_proxy(account_id: int, data: BindProxyIn, db: Session = Depends(get_db)):
    """绑定代理（一账号一代理）。proxy_id 为 null 时解绑。"""
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    if data.proxy_id is not None:
        proxy = db.get(Proxy, data.proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        if proxy.account is not None and proxy.account.id != account.id:
            raise HTTPException(
                status_code=400,
                detail=f"该代理已被账号「{proxy.account.name}」绑定（单API单代理，一代理只能绑一个账号）",
            )
        account.proxy_id = proxy.id
        detail = f"账号 {account.name} 绑定代理 {proxy.name}"
    else:
        account.proxy_id = None
        detail = f"账号 {account.name} 解绑代理"
    db.commit()
    db.refresh(account)
    return _to_out(account)

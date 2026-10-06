"""账号管理：CRUD + 代理一对一绑定。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import json
import logging

from app.core.deps import get_db
from app.core.redis_client import get_sync_redis
from app.core.security import encrypt_text
from app.models.models import Account, Proxy, SnipeTask
from app.schemas.schemas import AccountCreate, AccountOut, AccountUpdate, BindProxyIn

logger = logging.getLogger(__name__)
# 与 app.services.instances.CACHE_PREFIX 保持一致（实例列表缓存 key 前缀）
_INSTANCE_CACHE_PREFIX = "instances:"

router = APIRouter()

def _to_out(account: Account) -> AccountOut:
    return AccountOut.model_validate(account)

def _instance_counts_from_cache() -> dict:
    """从 Redis 实例缓存统计各账号实例数。

    缓存 key 含不同 filter 会有多个，同一实例可能重复出现，用 instance_id 去重。
    Redis 不可用或无缓存时返回空 dict（前端显示 0），绝不抛异常。"""
    counts = {}
    try:
        r = get_sync_redis()
        if r is None:
            return counts
        seen = set()
        for key in r.scan_iter(match=_INSTANCE_CACHE_PREFIX + "*", count=200):
            try:
                raw = r.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                for item in data.get("items", []):
                    iid = item.get("instance_id")
                    aid = item.get("account_id")
                    if iid and aid and iid not in seen:
                        seen.add(iid)
                        counts[aid] = counts.get(aid, 0) + 1
            except Exception:
                continue
    except Exception as e:
        logger.warning("读取实例缓存统计失败：%s", e)
    return counts


def _snipe_status_map(db: Session) -> dict:
    """各账号抢机任务状态：running 优先于 paused，其余为 none。"""
    rows = (
        db.query(SnipeTask.account_id, SnipeTask.status)
        .filter(SnipeTask.status.in_(["running", "paused"]))
        .all()
    )
    status_map = {}
    for account_id, status in rows:
        if account_id not in status_map or status == "running":
            status_map[account_id] = status
    return status_map


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.query(Account).order_by(Account.id.desc()).all()
    counts = _instance_counts_from_cache()
    snipe_map = _snipe_status_map(db)
    result = []
    for a in accounts:
        out = _to_out(a)
        out.instance_count = counts.get(a.id, 0)
        out.snipe_task_status = snipe_map.get(a.id, "none")
        result.append(out)
    return result

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
    if data.cost is not None:
        account.cost = data.cost
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

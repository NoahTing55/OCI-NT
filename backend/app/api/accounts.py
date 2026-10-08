"""账号管理：CRUD + 代理一对一绑定。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import json
import logging

from app.core.deps import get_db
from app.core.redis_client import get_sync_redis
from app.core.security import encrypt_text
from app.models.models import Account, Proxy, SnipeTask
from app.schemas.schemas import (
    AccountCreate, AccountOut, AccountUpdate, BindProxyIn,
    BatchImportRequest, BatchImportResponse, BatchImportFail,
)

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

def _create_account_core(data: AccountCreate, db: Session) -> Account:
    """创建账号的核心逻辑（单账号和批量导入共用）。

    抛 HTTPException 表示失败，调用方负责捕获。
    """
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
    return account


@router.post("", response_model=AccountOut)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    account = _create_account_core(data, db)
    return _to_out(account)


def _parse_oci_config(config_text: str) -> dict:
    """解析 ~/.oci/config 文本，返回 {user, fingerprint, tenancy, region}。

    取 [DEFAULT] 段（无段头时整段视为 DEFAULT），抛 ValueError 表示解析失败。
    """
    import configparser
    import io

    text = config_text.strip()
    if not text:
        raise ValueError("config 内容为空")
    parser = configparser.ConfigParser()
    # 无段头时补一个 [DEFAULT] 段头
    if not text.lstrip().startswith("["):
        text = "[DEFAULT]\n" + text
    try:
        parser.read_file(io.StringIO(text))
    except Exception as e:
        raise ValueError(f"config 解析失败：{e}")
    # 注意：configparser 的 DEFAULT 是特殊段，has_section("DEFAULT") 恒为 False，
    # 但 parser.defaults() 可读到；其他命名段用 sections()[0]
    if parser.defaults():
        kv = {k.lower(): v.strip() for k, v in parser.defaults().items()}
    elif parser.sections():
        kv = {k.lower(): v.strip() for k, v in parser.items(parser.sections()[0])}
    else:
        raise ValueError("config 中没有有效段")
    missing = [k for k in ("user", "fingerprint", "tenancy") if not kv.get(k)]
    if missing:
        raise ValueError(f"config 缺少字段：{', '.join(missing)}")
    region = kv.get("region", "ap-seoul-1")
    # region 可能是 oc1.ap-seoul-1 格式，取最后一段
    region = region.split(".")[-1].strip()
    return {
        "user_ocid": kv["user"],
        "fingerprint": kv["fingerprint"],
        "tenancy_ocid": kv["tenancy"],
        "region": region or "ap-seoul-1",
    }


@router.post("/batch-import", response_model=BatchImportResponse)
def batch_import_accounts(data: BatchImportRequest, db: Session = Depends(get_db)):
    """批量导入账号：每个条目解析 config + 私钥，逐个创建。

    成功返回账号 id 列表，失败返回明细（不影响其他条目）。
    """
    created: list[int] = []
    failed: list[dict] = []
    for idx, item in enumerate(data.accounts):
        # 别名：为空则用 tenancy 后 6 位自动生成
        name = item.name.strip()
        try:
            cfg = _parse_oci_config(item.config_text)
            if not name:
                suffix = "".join(c for c in cfg["tenancy_ocid"] if c.isalnum())[-6:]
                name = f"oci-{suffix}"
            account_data = AccountCreate(
                name=name,
                tenancy_ocid=cfg["tenancy_ocid"],
                user_ocid=cfg["user_ocid"],
                fingerprint=cfg["fingerprint"],
                private_key=item.private_key.strip(),
                region=cfg["region"],
            )
            account = _create_account_core(account_data, db)
            created.append(account.id)
        except HTTPException as e:
            failed.append({"name": name or f"第{idx + 1}个", "error": e.detail})
        except ValueError as e:
            failed.append({"name": name or f"第{idx + 1}个", "error": str(e)})
        except Exception as e:
            logger.warning("批量导入第 %d 个账号异常：%s", idx + 1, e)
            failed.append({"name": name or f"第{idx + 1}个", "error": f"未知错误：{e}"})
    return BatchImportResponse(
        created=created,
        failed=[BatchImportFail(**f) for f in failed],
    )

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
    # registered_at 用 fields_set 区分"没传"和"显式传 null（清空）"
    if "registered_at" in data.model_fields_set:
        account.registered_at = data.registered_at
    if data.account_type is not None:
        account.account_type = data.account_type
        # 用户手动设置过账号类型：打上锁定标记，存活检查不再自动覆盖
        account.account_type_manual = True
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

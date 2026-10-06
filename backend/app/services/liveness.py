"""存活检查服务：错误分类 + 状态变更通知（TG）。

状态分类：
- healthy：200，账号正常
- key_invalid：401，密钥失效（指纹/私钥不对）
- forbidden：403，权限不足
- not_found：404，用户不存在
- network_error：超时 / 连接失败 / 代理故障
- unknown_error：其他异常
"""
import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core import telegram
from app.core.oci_factory import build_client_for_account, build_proxy_url
from app.models.models import Account

# build_proxy_url 从 oci_factory 重新导出，保持旧引用兼容
__all__ = ["build_proxy_url", "check_account_liveness", "check_all_accounts", "STATUS_TEXT"]

logger = logging.getLogger(__name__)

STATUS_TEXT = {
    "healthy": "正常",
    "key_invalid": "密钥失效",
    "forbidden": "权限不足",
    "not_found": "用户不存在",
    "network_error": "网络或代理异常",
    "unknown_error": "未知异常",
    "unchecked": "未检查",
}


def _classify(status_code, err):
    if status_code == 200:
        return "healthy", "账号正常"
    if status_code == 401:
        return "key_invalid", "密钥失效（请检查指纹/私钥是否匹配）"
    if status_code == 403:
        return "forbidden", "权限不足（用户策略不允许）"
    if status_code == 404:
        return "not_found", "用户不存在"
    if status_code is None:
        return "network_error", "网络或代理异常（%s）" % err
    return "unknown_error", "未知异常（HTTP %s）" % status_code


async def check_account_liveness(db: Session, account_id: int) -> dict:
    account = db.get(Account, account_id)
    if not account:
        return {
            "account_id": account_id,
            "name": "",
            "status": "unknown_error",
            "status_code": None,
            "message": "账号不存在",
        }
    old_status = account.status

    try:
        client = build_client_for_account(account)
    except ValueError as e:
        # 私钥解密失败（MASTER_KEY 不对或数据损坏）
        account.status = "unknown_error"
        account.last_check_at = datetime.utcnow()
        db.commit()
        return {
            "account_id": account.id,
            "name": account.name,
            "status": "unknown_error",
            "status_code": None,
            "message": "私钥解密失败：%s" % e,
        }

    status_code = None
    err = None
    sub_info = {"type": None, "start_time": None}
    try:
        resp = await client.get_user()
        status_code = resp.status_code
        # 存活检查顺带查订阅信息（类型+注册时间，失败不影响主流程）
        # 对标 OCI-Start：registerTime 取 subscription.getTimeStart()
        if status_code == 200:
            try:
                sub_info = await client.get_subscription_info()
            except Exception:
                logger.debug("账号 %s 查订阅信息异常", account.name, exc_info=True)
    except httpx.TimeoutException:
        err = "timeout"
    except (httpx.ConnectError, httpx.ProxyError):
        err = "connect"
    except Exception as e:
        err = "exception:" + type(e).__name__
        logger.exception("账号 %s 检查异常", account.name)
    finally:
        await client.aclose()

    new_status, message = _classify(status_code, err)
    account.status = new_status
    account.last_check_at = datetime.utcnow()
    if sub_info["type"]:
        account.account_type = sub_info["type"]
    # 订阅开始时间即账号注册时间（OCI-Start 同款逻辑）；用户手动填过的优先保留
    if sub_info["start_time"] and not account.registered_at:
        account.registered_at = sub_info["start_time"]
    db.commit()
    logger.info("账号「%s」存活检查：%s（%s）", account.name, STATUS_TEXT[new_status], message)

    # 状态变更 → TG 推送（通知渠道只保留 TG；调用点已留好）
    if old_status != new_status and old_status != "unchecked":
        await telegram.send_message(
            "【账号状态变更】" + account.name + "\n"
            + STATUS_TEXT.get(old_status, old_status) + " → " + STATUS_TEXT[new_status]
            + "\n" + message
        )

    return {
        "account_id": account.id,
        "name": account.name,
        "region": account.region,
        "account_type": account.account_type,
        "status": new_status,
        "status_code": status_code,
        "message": message,
    }


async def check_all_accounts(db: Session) -> list:
    """全量检查（定时任务与一键检查共用）。"""
    accounts = db.query(Account).order_by(Account.id).all()
    results = []
    for account in accounts:
        results.append(await check_account_liveness(db, account.id))
    return results

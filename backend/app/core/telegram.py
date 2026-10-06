"""Telegram Bot 通知模块。

通知渠道只保留 Telegram Bot，其他渠道（钉钉/Bark/企微/邮件/Webhook）不做。
Bot Token / Chat ID 改为动态读取（优先级：网页设置 DB > 环境变量），
每次发送时解析（带 60 秒内存缓存），网页改完即时生效，不重启。
各业务事件直接调用 send_message(text) 即可；未配置时只打日志不抛异常。
"""
import logging

import httpx

from app.services.settings import get_setting, invalidate_cache

logger = logging.getLogger(__name__)


def reload() -> None:
    """手动刷新 TG 配置缓存。

    正常经网页修改后 set_setting() 会自动失效缓存，这里是兜底接口
    （比如直接改了环境变量想立即生效时调用）。
    """
    invalidate_cache("TG_BOT_TOKEN")
    invalidate_cache("TG_CHAT_ID")


def get_tg_config() -> tuple[str, str]:
    """返回 (token, chat_id)，未配置时为空字符串。"""
    token = str(get_setting("TG_BOT_TOKEN") or "").strip()
    chat_id = str(get_setting("TG_CHAT_ID") or "").strip()
    return token, chat_id


async def _post_message(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """底层发送（trust_env=False，直连 TG API，忽略环境变量里的代理）。

    返回 (ok, reason)，reason 为空表示成功。
    """
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            if r.status_code != 200:
                return False, f"Telegram API 返回 {r.status_code}：{r.text[:200]}"
            return True, ""
    except Exception as e:
        return False, f"请求 Telegram API 异常：{e}"


async def send_message(text: str) -> bool:
    """发送 TG 消息。成功返回 True，未配置或失败返回 False（只打日志不抛异常）。"""
    token, chat_id = get_tg_config()
    if not token or not chat_id:
        logger.warning("TG 未配置（网页系统设置/TG_BOT_TOKEN·TG_CHAT_ID 为空），跳过推送：%s", text[:80])
        return False
    ok, reason = await _post_message(token, chat_id, text)
    if not ok:
        logger.error("TG 推送失败：%s", reason)
    return ok


async def send_test_message() -> tuple[bool, str]:
    """发一条测试消息。返回 (ok, reason)，reason 为空表示成功。"""
    token, chat_id = get_tg_config()
    if not token or not chat_id:
        return False, "TG_BOT_TOKEN / TG_CHAT_ID 未配置（请在网页系统设置或环境变量中填写）"
    return await _post_message(token, chat_id, "【OCI 面板】测试推送：Telegram 通知配置正常")

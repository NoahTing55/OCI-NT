"""Telegram Bot 通知模块。

通知渠道只保留 Telegram Bot，其他渠道（钉钉/Bark/企微/邮件/Webhook）不做。
各业务事件（账号状态变更、抢机成功/失败、IP 变更、DNS 同步结果等）
直接调用 send_message(text) 即可；未配置 Token 时只打日志不抛异常。
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_message(text: str) -> bool:
    """发送 TG 消息。成功返回 True，未配置或失败返回 False。"""
    token = settings.TG_BOT_TOKEN.strip()
    chat_id = settings.TG_CHAT_ID.strip()
    if not token or not chat_id:
        logger.warning("TG 未配置（TG_BOT_TOKEN/TG_CHAT_ID 为空），跳过推送：%s", text[:80])
        return False
    try:
        # trust_env=False：直连 TG API，忽略环境变量里的代理
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            if r.status_code != 200:
                logger.error("TG 推送失败：%s %s", r.status_code, r.text[:200])
                return False
            return True
    except Exception as e:
        logger.exception("TG 推送异常：%s", e)
        return False

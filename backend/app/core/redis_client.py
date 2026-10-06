"""Redis 客户端：实例列表缓存用。

连接失败时返回 None，调用方降级为直接查询/静默跳过，绝不因缓存报错
影响主流程。async 客户端给异步服务用，sync 客户端给 Celery worker 等
同步上下文的缓存失效用。
"""
import logging
import time

import redis.asyncio as aioredis
import redis as sync_redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_async_client = None
_sync_client = None
_last_fail_ts = 0.0
_FAIL_COOLDOWN = 30.0  # 连接失败后 30 秒内不再重试，避免刷屏


def _cooldown() -> bool:
    return (time.time() - _last_fail_ts) < _FAIL_COOLDOWN


def _mark_fail(e: Exception, what: str):
    global _last_fail_ts
    _last_fail_ts = time.time()
    logger.warning("Redis 不可用（%s），已降级：%s", what, e)


async def get_redis():
    """异步 Redis 客户端；不可用返回 None。"""
    global _async_client
    if _async_client is not None:
        return _async_client
    if _cooldown():
        return None
    try:
        c = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        await c.ping()
        _async_client = c
        return c
    except Exception as e:
        _mark_fail(e, "async")
        return None


def get_sync_redis():
    """同步 Redis 客户端；不可用返回 None。"""
    global _sync_client
    if _sync_client is not None:
        return _sync_client
    if _cooldown():
        return None
    try:
        c = sync_redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        c.ping()
        _sync_client = c
        return c
    except Exception as e:
        _mark_fail(e, "sync")
        return None

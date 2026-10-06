"""APScheduler 定时任务（api 进程内调度）。

- 全量存活检查：间隔 CHECK_INTERVAL_MINUTES；
- 代理测速：间隔 PROXY_SPEEDTEST_MINUTES，更新 Proxy 状态/延迟，
  失效只走 TG 告警，不自动换绑（避免 IP 跳变触发 Oracle 风控）。

耗时批量任务走 Celery worker（见 workers/celery_app.py），不在这里。
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core import telegram
from app.core.config import settings
from app.core.deps import SessionLocal
from app.models.models import Proxy
from app.services.liveness import check_all_accounts
from app.services.proxy_check import check_proxy

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _job_check_all():
    db = SessionLocal()
    try:
        results = await check_all_accounts(db)
        logger.info("定时存活检查完成：%d 个账号", len(results))
    except Exception:
        logger.exception("定时存活检查异常")
    finally:
        db.close()


async def _job_proxy_speedtest():
    """代理测速：更新 status / latency_ms，失效 TG 告警（不自动换绑）。"""
    db = SessionLocal()
    try:
        proxies = db.query(Proxy).order_by(Proxy.id).all()
        for proxy in proxies:
            old_status = proxy.status
            ok, latency = await check_proxy(proxy)
            proxy.status = "ok" if ok else "fail"
            proxy.latency_ms = latency
            db.commit()
            if not ok and old_status != "fail":
                await telegram.send_message(
                    f"【代理失效告警】{proxy.name}（{proxy.scheme}://{proxy.host}:{proxy.port}）"
                    "测速失败，请手动检查处理（不会自动换绑）"
                )
            elif ok and old_status == "fail":
                await telegram.send_message(
                    f"【代理恢复】{proxy.name} 测速恢复正常，延迟 {latency}ms"
                )
        logger.info("代理测速完成：%d 个代理", len(proxies))
    except Exception:
        logger.exception("代理测速任务异常")
    finally:
        db.close()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        _job_check_all,
        "interval",
        minutes=settings.CHECK_INTERVAL_MINUTES,
        id="check_all_accounts",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_proxy_speedtest,
        "interval",
        minutes=settings.PROXY_SPEEDTEST_MINUTES,
        id="proxy_speedtest",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "定时任务已启动：存活检查 %d 分钟 / 代理测速 %d 分钟",
        settings.CHECK_INTERVAL_MINUTES,
        settings.PROXY_SPEEDTEST_MINUTES,
    )

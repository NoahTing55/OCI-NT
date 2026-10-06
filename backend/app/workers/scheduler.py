"""APScheduler 定时任务（api 进程内调度）。

- 全量存活检查：间隔取网页设置 CHECK_INTERVAL_MINUTES（DB > 环境变量 > 默认 360）；
- 代理测速：间隔取网页设置 PROXY_SPEEDTEST_MINUTES（DB > 环境变量 > 默认 30），
  更新 Proxy 状态/延迟，失效只走 TG 告警，不自动换绑（避免 IP 跳变触发 Oracle 风控）。

网页修改间隔后调 reschedule_jobs() 实时重排，无需重启。
耗时批量任务走 Celery worker（见 workers/celery_app.py），不在这里。
"""
import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core import telegram
from app.core.deps import SessionLocal
from app.models.models import Proxy
from app.services.liveness import check_all_accounts
from app.services.proxy_check import check_proxy
from app.services.settings import get_setting

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# (job_id, 设置键) 映射：reschedule_jobs 按此表重排
SCHEDULED_JOBS = (
    ("check_all_accounts", "CHECK_INTERVAL_MINUTES"),
    ("proxy_speedtest", "PROXY_SPEEDTEST_MINUTES"),
)


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


_JOB_FUNCS = {
    "check_all_accounts": _job_check_all,
    "proxy_speedtest": _job_proxy_speedtest,
}


def start_scheduler() -> None:
    if scheduler.running:
        return
    for job_id, key in SCHEDULED_JOBS:
        minutes = int(get_setting(key))
        scheduler.add_job(
            _JOB_FUNCS[job_id],
            "interval",
            minutes=minutes,
            id=job_id,
            replace_existing=True,
        )
    scheduler.start()
    logger.info(
        "定时任务已启动：存活检查 %d 分钟 / 代理测速 %d 分钟",
        int(get_setting("CHECK_INTERVAL_MINUTES")),
        int(get_setting("PROXY_SPEEDTEST_MINUTES")),
    )


def reschedule_jobs() -> dict:
    """按当前网页设置重新排期（设置变更后由 settings API 调用，即时生效不重启）。

    scheduler 未运行时只返回将要使用的间隔，不做重排（测试/未启动场景）。
    返回 {job_id: minutes}。
    """
    result = {}
    for job_id, key in SCHEDULED_JOBS:
        minutes = int(get_setting(key))
        result[job_id] = minutes
        if not scheduler.running:
            continue
        try:
            scheduler.reschedule_job(job_id, trigger="interval", minutes=minutes)
        except JobLookupError:
            scheduler.add_job(
                _JOB_FUNCS[job_id], "interval", minutes=minutes,
                id=job_id, replace_existing=True,
            )
    logger.info("定时任务已重排：%s", result)
    return result

"""APScheduler 定时任务（api 进程内调度）。

- 全量存活检查：每天定时执行，时间取网页设置 CHECK_DAILY_AT（"HH:MM"，DB > 环境变量 > 默认 08:00），服务器本地时间；
- 代理测速：间隔取网页设置 PROXY_SPEEDTEST_MINUTES（DB > 环境变量 > 默认 30），
  更新 Proxy 状态/延迟，失效只走 TG 告警，不自动换绑（避免 IP 跳变触发 Oracle 风控）。

网页修改设置后调 reschedule_jobs() 实时重排，无需重启。
耗时批量任务走 Celery worker（见 workers/celery_app.py），不在这里。
"""
import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core import telegram
from app.core.deps import SessionLocal
from app.models.models import Proxy
from app.services.liveness import check_all_accounts
from app.services.proxy_check import check_proxy
from app.services.settings import _parse_hhmm, get_setting

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# 默认每天存活检查时间（CHECK_DAILY_AT 非法时的回退值）
_DEFAULT_DAILY_AT = (8, 0)

# (job_id, 设置键, 调度类型) 映射：reschedule_jobs 按此表重排
#   cron：每天定时（CHECK_DAILY_AT "HH:MM"）
#   interval：按分钟间隔
SCHEDULED_JOBS = (
    ("check_all_accounts", "CHECK_DAILY_AT", "cron"),
    ("proxy_speedtest", "PROXY_SPEEDTEST_MINUTES", "interval"),
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


def _parse_daily_at() -> tuple:
    """解析 CHECK_DAILY_AT（"HH:MM"），返回 (hour, minute)。

    非法时记 warning 并回退默认 08:00。
    """
    raw = get_setting("CHECK_DAILY_AT")
    try:
        return _parse_hhmm(raw)
    except ValueError:
        logger.warning("CHECK_DAILY_AT 值 %r 非法，回退默认 08:00", raw)
        return _DEFAULT_DAILY_AT


def _describe_job(trigger_type: str, key: str) -> str:
    """人类可读的调度描述（用于日志和 API 返回）。"""
    if trigger_type == "cron":
        hour, minute = _parse_daily_at()
        return f"每天 {hour:02d}:{minute:02d}"
    return f"每 {int(get_setting(key))} 分钟"


def start_scheduler() -> None:
    if scheduler.running:
        return
    for job_id, key, trigger_type in SCHEDULED_JOBS:
        if trigger_type == "cron":
            hour, minute = _parse_daily_at()
            scheduler.add_job(
                _JOB_FUNCS[job_id],
                CronTrigger(hour=hour, minute=minute),
                id=job_id,
                replace_existing=True,
            )
        else:
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
        "定时任务已启动：存活检查 %s / 代理测速 %s",
        _describe_job("cron", "CHECK_DAILY_AT"),
        _describe_job("interval", "PROXY_SPEEDTEST_MINUTES"),
    )


def reschedule_jobs() -> dict:
    """按当前网页设置重新排期（设置变更后由 settings API 调用，即时生效不重启）。

    scheduler 未运行时只返回将要使用的排期描述，不做重排（测试/未启动场景）。
    返回 {job_id: 描述}，如 {"check_all_accounts": "每天 08:00"}。
    """
    result = {}
    for job_id, key, trigger_type in SCHEDULED_JOBS:
        result[job_id] = _describe_job(trigger_type, key)
        if not scheduler.running:
            continue
        try:
            if trigger_type == "cron":
                hour, minute = _parse_daily_at()
                scheduler.reschedule_job(
                    job_id, trigger="cron", hour=hour, minute=minute
                )
            else:
                minutes = int(get_setting(key))
                scheduler.reschedule_job(job_id, trigger="interval", minutes=minutes)
        except JobLookupError:
            # 任务不存在则按当前配置新建
            if trigger_type == "cron":
                hour, minute = _parse_daily_at()
                scheduler.add_job(
                    _JOB_FUNCS[job_id],
                    CronTrigger(hour=hour, minute=minute),
                    id=job_id,
                    replace_existing=True,
                )
            else:
                minutes = int(get_setting(key))
                scheduler.add_job(
                    _JOB_FUNCS[job_id], "interval", minutes=minutes,
                    id=job_id, replace_existing=True,
                )
    logger.info("定时任务已重排：%s", result)
    return result

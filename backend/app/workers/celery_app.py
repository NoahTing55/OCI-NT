"""Celery 应用：Redis 做 broker。

- 批量开机 / 关机 / 重启 / 终止等耗时任务走这里（独立 worker 服务）；
- APScheduler 定时任务（全量存活检查、代理测速）仍在 api 进程内，保持不动。

worker 启动命令（见 docker-compose.yml）：
    celery -A app.workers.celery_app.celery worker --loglevel=info --concurrency=4
"""
import logging

from celery import Celery

from app.core.config import settings

# 关掉 noisy 的库日志：httpx 请求明细只留 WARNING 以上
logging.getLogger("httpx").setLevel(logging.WARNING)

celery = Celery("oci_panel", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# 放在末尾：注册任务模块。
# batch_tasks 反向 import 本模块的 celery 对象，这样写可避免循环导入。
from app.workers import batch_tasks  # noqa: E402,F401

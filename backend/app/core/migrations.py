"""启动时自动执行 Alembic 迁移。

背景：lifespan 曾只跑 Base.metadata.create_all——它从不给已存在的表加列，
导致 0006 这类"加列"迁移在生产库（create_all 建表、从未跑过 alembic）上
永远不生效，api 启动直接崩溃（sqlalchemy.exc.ProgrammingError:
column snipe_tasks.root_password does not exist）。

本模块在启动时跑 `alembic upgrade head`，并处理老库：
- 新库（无表）：create_all 先建表（调用方负责），upgrade 幂等无操作；
- 老库（有表但无 alembic_version 记录）：先 stamp base 打基线，再 upgrade，
  各迁移脚本本身都是幂等的（建表/加列前先检查是否存在），不会破坏已有数据。

以后新增字段只需写迁移脚本，不再需要手动补 SQL。
"""
import logging
import os

logger = logging.getLogger(__name__)


def run_db_migrations(engine) -> None:
    """幂等地把数据库升级到最新迁移版本。失败时抛异常，让启动显式失败。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    backend_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )  # backend/app/core/migrations.py -> backend/
    # alembic.ini 里 script_location 是相对路径，转成绝对路径后无论从哪启动都可用
    script_location = os.path.join(backend_dir, "alembic")
    if not os.path.isdir(script_location):
        raise RuntimeError(
            f"找不到 alembic 迁移目录：{script_location}，自动迁移已中止。"
            "请确认镜像构建时已 COPY backend/alembic"
        )
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", script_location)

    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        # 老库：表是 create_all 建的，从没走过 alembic——打基线后再升级
        command.stamp(cfg, "base")
        logger.info("数据库无迁移版本记录，已打基线 stamp(base)")

    command.upgrade(cfg, "head")
    logger.info("数据库迁移到最新版本完成")


if __name__ == "__main__":
    # 供 celery worker 容器在启动前执行：
    #   python -m app.core.migrations && celery -A app.workers.celery_app.celery worker ...
    # 避免 worker 在 api 的 lifespan 迁移完成前就连库查到缺列。
    logging.basicConfig(level=logging.INFO)
    from app.core.deps import engine as _engine

    run_db_migrations(_engine)

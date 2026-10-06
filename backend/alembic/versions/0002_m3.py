"""M3：抢机引擎表结构（幂等，可重复执行，不破坏已有表）。

- snipe_logs：缺失时才建表；
- snipe_tasks.display_name / instance_ocid / started_at / finished_at：缺失时才加列。
"""
import sqlalchemy as sa
from alembic import op

from app.models.models import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    # 1. 建缺失的表（与 models.py 当前定义保持一致）
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind=bind)

    # 2. snipe_tasks 新增字段（M3）
    if "snipe_tasks" in existing_tables:
        cols = {c["name"] for c in insp.get_columns("snipe_tasks")}
        if "display_name" not in cols:
            op.add_column("snipe_tasks", sa.Column("display_name", sa.String(128), server_default=""))
        if "instance_ocid" not in cols:
            op.add_column("snipe_tasks", sa.Column("instance_ocid", sa.String(255), server_default=""))
        if "started_at" not in cols:
            op.add_column("snipe_tasks", sa.Column("started_at", sa.DateTime(), nullable=True))
        if "finished_at" not in cols:
            op.add_column("snipe_tasks", sa.Column("finished_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # 不提供回滚：避免误删已有业务数据
    pass

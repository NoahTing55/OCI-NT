"""批量创建实例：3 张新表（幂等，可重复执行，不破坏已有表）。

- batch_create_tasks：任务（配置 + 计数）；
- batch_create_items：单个实例创建项（进度明细）；
- batch_create_templates：配置模板。
"""
import sqlalchemy as sa
from alembic import op

from app.models.models import Base

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    # 建缺失的表（与 models.py 当前定义保持一致）
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind=bind)


def downgrade() -> None:
    # 不提供回滚：避免误删已有业务数据
    pass

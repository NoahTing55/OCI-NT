"""系统设置表 system_settings（幂等，可重复执行，不破坏已有表）。

- system_settings：Web 可配项（key 主键、value_encrypted、is_secret）。
"""
import sqlalchemy as sa
from alembic import op

from app.models.models import Base

revision = "0005"
down_revision = "0004"
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

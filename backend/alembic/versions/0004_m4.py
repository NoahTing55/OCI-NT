"""M4：operators 登录账号表（幂等，可重复执行，不破坏已有表）。

- operators：面板登录账号（用户名唯一、密码哈希、TOTP 密钥加密、双因素开关）。
"""
import sqlalchemy as sa
from alembic import op

from app.models.models import Base

revision = "0004"
down_revision = "0003"
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

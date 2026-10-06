"""账号真实注册时间 + 账号类型：accounts.registered_at / accounts.account_type。

- registered_at：用户手动填写的 OCI 账号真实注册时间（可空），存活天数优先用它；
- account_type：free 免费 / paid 付费 / 空 未知，存活检查时调 Subscription API 自动更新。

幂等：列已存在则跳过，不破坏已有数据。
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# (表名, 列名, 列定义)
_NEW_COLUMNS = [
    ("accounts", "registered_at", sa.Column("registered_at", sa.DateTime(), nullable=True)),
    ("accounts", "account_type", sa.Column("account_type", sa.String(32), server_default="")),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())
    for table, col_name, column in _NEW_COLUMNS:
        if table not in existing_tables:
            continue
        existing_cols = {c["name"] for c in insp.get_columns(table)}
        if col_name not in existing_cols:
            op.add_column(table, column)


def downgrade() -> None:
    # 不提供回滚：避免误删已有业务数据
    pass

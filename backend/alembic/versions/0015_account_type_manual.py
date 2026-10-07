"""账号类型手动锁定：accounts.account_type_manual。

- account_type_manual：用户是否手动设置过账号类型（Boolean，默认 False）；
  为 True 时存活检查不再用自动识别结果覆盖 account_type。

幂等：列已存在则跳过，不破坏已有数据。
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# (表名, 列名, 列定义)
_NEW_COLUMNS = [
    ("accounts", "account_type_manual", sa.Column("account_type_manual", sa.Boolean(), nullable=True)),
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

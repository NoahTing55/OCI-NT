"""账号成本：accounts.cost。

幂等：列已存在则跳过，不破坏已有数据。
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# (表名, 列名, 列定义)
_NEW_COLUMNS = [
    ("accounts", "cost", sa.Column("cost", sa.Float(), server_default="0")),
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

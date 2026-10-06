"""root 密码字段：snipe_tasks.root_password、batch_create_items.root_password / public_ip。

幂等：列已存在则跳过，不破坏已有数据。
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# (表名, 列名, 列定义)
_NEW_COLUMNS = [
    ("snipe_tasks", "root_password", sa.Column("root_password", sa.String(128), server_default="")),
    ("batch_create_items", "root_password", sa.Column("root_password", sa.String(128), server_default="")),
    ("batch_create_items", "public_ip", sa.Column("public_ip", sa.String(64), server_default="")),
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

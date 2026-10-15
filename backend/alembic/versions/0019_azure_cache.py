"""Azure 订阅缓存：azure_accounts.cached_subscriptions / cached_at。

查一次 Tier 后存库，前端直接展示，不用每次点查询。

幂等：列已存在则跳过。
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_NEW_COLUMNS = [
    ("azure_accounts", "cached_subscriptions", sa.Column("cached_subscriptions", sa.Text(), nullable=True)),
    ("azure_accounts", "cached_at", sa.Column("cached_at", sa.DateTime(), nullable=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "azure_accounts" not in set(insp.get_table_names()):
        return
    existing_cols = {c["name"] for c in insp.get_columns("azure_accounts")}
    for table, col_name, column in _NEW_COLUMNS:
        if col_name not in existing_cols:
            op.add_column(table, column)


def downgrade() -> None:
    pass

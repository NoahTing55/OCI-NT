"""Azure 账号绑定代理：azure_accounts.proxy_id。

- proxy_id：可空，唯一（一代理一账号，与 OCI 账号一致）；
- 删除代理时 SET NULL。

幂等：列已存在则跳过。
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "azure_accounts" not in set(insp.get_table_names()):
        return
    existing_cols = {c["name"] for c in insp.get_columns("azure_accounts")}
    if "proxy_id" not in existing_cols:
        op.add_column(
            "azure_accounts",
            sa.Column("proxy_id", sa.Integer(),
                      sa.ForeignKey("proxies.id", ondelete="SET NULL"),
                      nullable=True, unique=True),
        )


def downgrade() -> None:
    pass

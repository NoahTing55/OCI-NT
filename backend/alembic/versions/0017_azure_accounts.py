"""Azure 账号表：azure_accounts。

- name：账号别名；
- tenant_id / client_id：明文；
- client_secret_enc：Fernet 加密的客户端密钥，永不返回给前端；
- remark：备注。

幂等：表已存在则跳过。
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "azure_accounts" in set(insp.get_table_names()):
        return

    op.create_table(
        "azure_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_secret_enc", sa.Text(), nullable=False),
        sa.Column("remark", sa.String(255), default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # 不提供回滚：避免误删已有业务数据
    pass

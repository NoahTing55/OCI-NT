"""M2：补齐表结构（幂等，可重复执行，不破坏已有表）。

- lifespan 的 Base.metadata.create_all 已建的表会跳过；
- accounts.compartment_ocid、domain_bindings.cf_token_id/ttl：缺失时才加列；
- cloudflare_tokens：缺失时才建表。
"""
import sqlalchemy as sa
from alembic import op

from app.models.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    # 1. 建缺失的表（与 models.py 当前定义保持一致）
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind=bind)

    # 2. accounts.compartment_ocid（M2 新增字段）
    if "accounts" in existing_tables:
        cols = {c["name"] for c in insp.get_columns("accounts")}
        if "compartment_ocid" not in cols:
            op.add_column("accounts", sa.Column("compartment_ocid", sa.String(255), server_default=""))

    # 3. domain_bindings.cf_token_id / ttl（M2 新增字段）
    if "domain_bindings" in existing_tables:
        cols = {c["name"] for c in insp.get_columns("domain_bindings")}
        if "cf_token_id" not in cols:
            op.add_column("domain_bindings", sa.Column("cf_token_id", sa.Integer(), nullable=True))
        if "ttl" not in cols:
            op.add_column("domain_bindings", sa.Column("ttl", sa.Integer(), server_default="120"))


def downgrade() -> None:
    # 不提供回滚：避免误删已有业务数据
    pass

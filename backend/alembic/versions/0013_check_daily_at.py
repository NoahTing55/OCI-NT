"""存活检查改为每天定时：system_settings 新增 CHECK_DAILY_AT（默认 08:00）。

幂等：key 已存在则跳过，不覆盖用户已设置的值。
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "system_settings" not in set(insp.get_table_names()):
        return
    # 已存在则跳过（幂等，不覆盖用户配置）
    existing = bind.execute(
        sa.text("SELECT key FROM system_settings WHERE key = 'CHECK_DAILY_AT'")
    ).fetchone()
    if existing:
        return
    bind.execute(
        sa.text(
            "INSERT INTO system_settings (key, value_encrypted, is_secret) "
            "VALUES ('CHECK_DAILY_AT', '08:00', false)"
        )
    )


def downgrade() -> None:
    # 不提供回滚：避免误删用户配置
    pass

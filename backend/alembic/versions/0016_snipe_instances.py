"""实例级记录：snipe_instances 表。

- 每个抢到的实例一行，sequence 永远从 1 开始连续编号；
- 删除中间项后后续序号前移（API 层事务内完成）；
- root_password 按实例 OCID 精确绑定，绝不错位。

数据迁移：遍历所有 SnipeTask，解析 instance_ocid 逗号分隔，为每个 OCID
建行，sequence 从 1 递增，root_password 取任务密码，display_name 取
任务 display_name + "-" + sequence。

幂等：表已存在则跳过。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "snipe_instances" in set(insp.get_table_names()):
        return

    op.create_table(
        "snipe_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("snipe_tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("instance_ocid", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("sequence", sa.Integer(), nullable=False, default=1),
        sa.Column("root_password", sa.String(255), default=""),
        sa.Column("display_name", sa.String(128), default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # 数据迁移：把旧的逗号分隔 OCID 拆成实例级行
    session = Session(bind=bind)
    try:
        tasks = session.execute(sa.text("SELECT id, instance_ocid, root_password, display_name FROM snipe_tasks")).fetchall()
        for task_id, instance_ocid, root_password, display_name in tasks:
            ocids = [o.strip() for o in (instance_ocid or "").split(",") if o.strip()]
            for idx, ocid in enumerate(ocids, start=1):
                # 去重：同一 OCID 只建一行
                exists = session.execute(
                    sa.text("SELECT 1 FROM snipe_instances WHERE instance_ocid = :ocid"),
                    {"ocid": ocid},
                ).fetchone()
                if exists:
                    continue
                session.execute(
                    sa.text(
                        "INSERT INTO snipe_instances (task_id, instance_ocid, sequence, root_password, display_name, created_at) "
                        "VALUES (:task_id, :ocid, :seq, :pwd, :dn, NOW())"
                    ),
                    {
                        "task_id": task_id,
                        "ocid": ocid,
                        "seq": idx,
                        "pwd": root_password or "",
                        "dn": f"{display_name}-{idx}" if display_name else "",
                    },
                )
        session.commit()
    finally:
        session.close()


def downgrade() -> None:
    op.drop_table("snipe_instances")

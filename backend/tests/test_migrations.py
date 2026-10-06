"""启动自动迁移测试：老库（create_all 建表、无 alembic 记录）自愈升级到 head。

模拟 2026-10-06 两次生产事故的根因：生产库是 create_all 建的、从未跑过
alembic，加列迁移（0006/0007/0008）永远不生效，api 启动直接崩溃。
run_db_migrations 应做到：无版本记录的老库先 stamp(base) 打基线，
再 upgrade 到最新 head，缺的列自动补上，且重复执行幂等。

运行：/tmp/ocitest3/bin/python backend/tests/test_migrations.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

DB_PATH = "/tmp/test_migrations.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from sqlalchemy import inspect, text  # noqa: E402

from app.core.deps import engine  # noqa: E402
from app.core.migrations import run_db_migrations  # noqa: E402
from app.models.models import Base  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra and not cond else ""))


NEW_COLS = ("root_password", "target_count", "success_count", "interval_seconds")


def cols_of(table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


# 1. 模拟老库：create_all 建表（当前模型），然后删掉 4 个新列
Base.metadata.create_all(bind=engine)
with engine.begin() as conn:
    for col in NEW_COLS:
        conn.execute(text(f"ALTER TABLE snipe_tasks DROP COLUMN {col}"))

tables = set(inspect(engine).get_table_names())
check("模拟老库无 alembic_version 记录", "alembic_version" not in tables)
check("模拟老库缺 4 个新列", not set(NEW_COLS) & cols_of("snipe_tasks"))

# 2. 跑自动迁移
run_db_migrations(engine)

check("4 个新列全部补上", set(NEW_COLS) <= cols_of("snipe_tasks"))
with engine.connect() as conn:
    version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
check("版本记录为 0009（当前 head）", version == "0009", str(version))
check("老库已有数据表未被破坏（accounts 表存在）", "accounts" in set(inspect(engine).get_table_names()))

# 3. 幂等：再跑一次无异常、无副作用
before = cols_of("snipe_tasks")
run_db_migrations(engine)
after = cols_of("snipe_tasks")
check("第二次运行幂等（列集合不变）", before == after)
with engine.connect() as conn:
    version2 = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
check("第二次运行版本仍为 0009", version2 == "0009", str(version2))

# 4. 全新空库：无表时 upgrade 应直接到 head（不抛异常）
engine.dispose()
os.remove(DB_PATH)
run_db_migrations(engine)
check("空库自动迁移到 head 不抛异常", True)
check("空库 snipe_tasks 表已建且含新列", set(NEW_COLS) <= cols_of("snipe_tasks"))

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
sys.exit(1 if FAIL else 0)

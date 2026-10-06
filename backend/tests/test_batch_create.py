"""批量创建实例测试：API 校验 + 模板 CRUD + cancel 流程。

运行：/tmp/ocitest/bin/python backend/tests/test_batch_create.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

# 测试环境变量必须在 import app 之前设置
DB_PATH = "/tmp/test_batch_create.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///%s" % DB_PATH
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # 占位，稍后覆盖

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# 用合法的 Fernet key 覆盖占位值
os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import SessionLocal  # noqa: E402
from app.core.security import encrypt_text  # noqa: E402
from app.models.models import Account, Base, BatchCreateItem, BatchCreateTask  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


# ---------- API 测试 ----------
from app.core.deps import engine  # noqa: E402
from app.main import app  # noqa: E402

Base.metadata.create_all(bind=engine)


def _make_account(name="测试账号"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    db = SessionLocal()
    try:
        acc = Account(
            name=name,
            tenancy_ocid="ocid1.tenancy.oc1..test",
            user_ocid="ocid1.user.oc1..test",
            fingerprint="aa:bb:cc:dd",
            private_key_enc=encrypt_text(pem),
            region="ap-seoul-1",
        )
        db.add(acc)
        db.commit()
        return acc.id
    finally:
        db.close()


def _body(account_id, **kw):
    b = {
        "name": "E5批量测试",
        "shape": "VM.Standard.E5.Flex",
        "ocpus": 2,
        "memory_gb": 16,
        "count_per_account": 2,
        "name_prefix": "e5test",
        "retry_mode": "direct",
        "image_ocid": "ocid1.image.oc1..test",
        "subnet_ocid": "ocid1.subnet.oc1..test",
        "availability_domain": "Uocm:AP-SEOUL-1-AD-1",
        "accounts": [{"account_id": account_id}],
    }
    b.update(kw)
    return b


with TestClient(app) as client:
    # M4：接口已要求登录，先建测试账号并带上 JWT
    from app.models.models import Operator
    from app.core.security import create_access_token, hash_password

    _db = SessionLocal()
    _db.add(Operator(username="tester", password_hash=hash_password("test123")))
    _db.commit()
    _db.close()
    client.headers.update({"Authorization": "Bearer " + create_access_token("tester")})

    # shape 预设接口
    r = client.get("/api/batch-create/shape-presets")
    check("GET /shape-presets 含 E5", r.status_code == 200 and any(
        p["shape"] == "VM.Standard.E5.Flex" for p in r.json()), r.text[:120])

    # 模板 CRUD
    tpl_body = {"name": "E5常用", "shape": "VM.Standard.E5.Flex", "ocpus": 2,
                "memory_gb": 16, "count_per_account": 2, "name_prefix": "e5",
                "retry_mode": "retry"}
    r = client.post("/api/batch-create/templates", json=tpl_body)
    check("模板创建", r.status_code == 200 and r.json()["name"] == "E5常用", r.text[:160])
    tpl_id = r.json()["id"]
    r = client.post("/api/batch-create/templates", json=tpl_body)
    check("模板重名 → 400", r.status_code == 400, r.text[:120])
    r = client.get("/api/batch-create/templates")
    check("模板列表", r.status_code == 200 and len(r.json()) >= 1)
    r = client.delete(f"/api/batch-create/templates/{tpl_id}")
    check("模板删除", r.status_code == 200, r.text[:120])
    r = client.delete("/api/batch-create/templates/99999")
    check("删不存在模板 → 404", r.status_code == 404)

    account_id = _make_account()

    # 缺必填：命名前缀空 → 400
    r = client.post("/api/batch-create", json=_body(account_id, name_prefix=""))
    check("缺命名前缀 → 400", r.status_code in (400, 422), r.text[:120])

    # 缺必填：子网空 → 400
    r = client.post("/api/batch-create", json=_body(account_id, subnet_ocid=""))
    check("缺子网 → 400", r.status_code == 400, r.text[:120])

    # 非法 retry_mode → 400
    r = client.post("/api/batch-create", json=_body(account_id, retry_mode="turbo"))
    check("非法 retry_mode → 400", r.status_code == 400, r.text[:120])

    # 账号不存在 → 404
    r = client.post("/api/batch-create", json=_body(99999))
    check("账号不存在 → 404", r.status_code == 404, r.text[:120])

    # 正常建任务（自动启动；沙箱出网被拦截，direct 模式 item 会快速失败）
    r = client.post("/api/batch-create", json=_body(account_id))
    ok_create = r.status_code == 200 and r.json()["total"] == 2
    check("建任务成功（2 台）", ok_create, r.text[:200])
    task1 = r.json()["id"]
    names = sorted(i["display_name"] for i in r.json()["items"])
    check("实例命名 前缀+序号", names == ["e5test-01", "e5test-02"], str(names))

    # 任务详情接口
    r = client.get(f"/api/batch-create/{task1}")
    check("任务详情含 items", r.status_code == 200 and len(r.json()["items"]) == 2, r.text[:160])

    # 列表接口
    r = client.get("/api/batch-create")
    check("任务列表", r.status_code == 200 and len(r.json()) >= 1)

    # cancel 流程：在 running 任务上取消
    #（direct 模式沙箱里 item 瞬间失败，任务可能已 done；用 SQL 把任务拨回 running 再测 cancel）
    db = SessionLocal()
    try:
        t = db.get(BatchCreateTask, task1)
        t.status = "running"
        db.query(BatchCreateItem).filter(BatchCreateItem.task_id == task1).update(
            {"status": "pending"}, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    r = client.post(f"/api/batch-create/{task1}/cancel")
    check("取消 running 任务 → cancelled", r.status_code == 200, r.text[:160])
    db = SessionLocal()
    try:
        t = db.get(BatchCreateTask, task1)
        pending_left = db.query(BatchCreateItem).filter(
            BatchCreateItem.task_id == task1, BatchCreateItem.status == "pending").count()
        check("取消后任务状态 cancelled", t.status == "cancelled", t.status)
        check("取消后无 pending item", pending_left == 0, str(pending_left))
    finally:
        db.close()
    # 已取消的任务再取消 → 400
    r = client.post(f"/api/batch-create/{task1}/cancel")
    check("重复取消 → 400", r.status_code == 400, r.text[:120])

    # 删除（非 running 可删）
    r = client.delete(f"/api/batch-create/{task1}")
    check("删除任务", r.status_code == 200, r.text[:120])

    # 每账号行覆盖：region 覆盖生效
    r = client.post("/api/batch-create", json=_body(
        account_id, name_prefix="e5cov",
        accounts=[{"account_id": account_id, "region": "us-phoenix-1"}]))
    check("建任务（region 覆盖）", r.status_code == 200, r.text[:160])
    task2 = r.json()["id"]
    r = client.get(f"/api/batch-create/{task2}")
    regions = {i["region"] for i in r.json()["items"]}
    check("region 覆盖生效", regions == {"us-phoenix-1"}, str(regions))

print()
print("共 %d 项：通过 %d，失败 %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL TESTS PASSED")

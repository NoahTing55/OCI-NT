"""M3 抢机引擎测试：错误分类单测 + 关键 API 测试。

运行：/tmp/ocitest/bin/python backend/tests/test_sniper.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

# 测试环境变量必须在 import app 之前设置
DB_PATH = "/tmp/test_sniper.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///%s" % DB_PATH
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # 占位 Fernet key（44 字节 base64）

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# 用合法的 Fernet key 覆盖占位值
os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import SessionLocal  # noqa: E402
from app.core.security import encrypt_text  # noqa: E402
from app.models.models import Account, Base  # noqa: E402
from app.workers.sniper import classify_launch_error  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


# ---------- 1. 错误分类单测（纯函数） ----------
check("classify: 200 -> success", classify_launch_error(200, "")[0] == "success")
check("classify: 201 -> success", classify_launch_error(201, "{}")[0] == "success")
check(
    "classify: 500 + Out of host capacity -> no_capacity",
    classify_launch_error(500, '{"code":"InternalError","message":"Out of host capacity"}')[0] == "no_capacity",
)
check(
    "classify: 500 + InternalError -> no_capacity",
    classify_launch_error(500, '{"code":"InternalError"}')[0] == "no_capacity",
)
check(
    "classify: 500 其他文本 -> unknown（不误判无货）",
    classify_launch_error(500, '{"code":"SomethingElse"}')[0] == "unknown",
)
check("classify: 429 -> rate_limited", classify_launch_error(429, "TooManyRequests")[0] == "rate_limited")
check("classify: 400 -> config_error", classify_launch_error(400, "bad request")[0] == "config_error")
check(
    "classify: 文本含 InvalidSubnet -> config_error",
    classify_launch_error(500, "InvalidSubnet")[0] == "config_error",
)
check("classify: 401 -> auth_error", classify_launch_error(401, "")[0] == "auth_error")
check(
    "classify: 文本 NotAuthenticated -> auth_error",
    classify_launch_error(500, "NotAuthenticated")[0] == "auth_error",
)
check("classify: 网络异常(None) -> unknown", classify_launch_error(None, "ConnectError")[0] == "unknown")
check("classify: 503 -> unknown", classify_launch_error(503, "")[0] == "unknown")
check(
    "classify: LimitExceeded -> config_error（配额超限停任务）",
    classify_launch_error(400, "LimitExceeded")[0] == "config_error",
)

# ---------- 2. API 测试 ----------
from app.core.deps import engine  # noqa: E402
from app.main import app  # noqa: E402

Base.metadata.create_all(bind=engine)


def _make_account() -> int:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    db = SessionLocal()
    try:
        acc = Account(
            name="测试账号",
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


TASK_BODY = {
    "account_id": 0,  # 运行时填入
    "region": "ap-seoul-1",
    "shape": "VM.Standard.A1.Flex",
    "ocpus": 4,
    "memory_gb": 24,
    "image_ocid": "ocid1.image.oc1..test",
    "subnet_ocid": "ocid1.subnet.oc1..test",
    "availability_domain": "Uocm:AP-SEOUL-1-AD-1",
}

with TestClient(app) as client:
    # M4：接口已要求登录，先建测试账号并带上 JWT
    from app.models.models import Operator
    from app.core.security import create_access_token, hash_password

    _db = SessionLocal()
    _db.add(Operator(username="tester", password_hash=hash_password("test123")))
    _db.commit()
    _db.close()
    client.headers.update({"Authorization": "Bearer " + create_access_token("tester")})

    # 模板接口
    r = client.get("/api/sniper/templates")
    check("GET /templates 200 且含 ARM 模板", r.status_code == 200 and any(
        t["shape"] == "VM.Standard.A1.Flex" and t["ocpus"] == 4 for t in r.json()))

    account_id = _make_account()
    body = dict(TASK_BODY, account_id=account_id)

    # 缺子网 → 400
    bad = dict(body, subnet_ocid="")
    r = client.post("/api/sniper", json=bad)
    check("建任务缺子网 → 400", r.status_code == 400, r.text[:120])

    # 账号不存在 → 404
    r = client.post("/api/sniper", json=dict(body, account_id=99999))
    check("建任务账号不存在 → 404", r.status_code == 404, r.text[:120])

    # 正常建任务
    r = client.post("/api/sniper", json=body)
    check("建任务成功", r.status_code == 200 and r.json()["status"] == "pending", r.text[:200])
    task1 = r.json()["id"]

    # 启动任务（worker 会尝试连 OCI，沙箱出网被拦截，走网络异常分支）
    r = client.post(f"/api/sniper/{task1}/start")
    check("启动任务 → running", r.status_code == 200 and r.json()["status"] == "running", r.text[:200])

    # 同一账号同一 shape 再建 → 400 被拒绝
    r = client.post("/api/sniper", json=body)
    check("重复建进行中任务 → 400", r.status_code == 400, r.text[:160])

    # 暂停任务（worker 的可中断 sleep 应快速唤醒退出）
    r = client.post(f"/api/sniper/{task1}/pause")
    check("暂停任务 → paused", r.status_code == 200, r.text[:200])

    # 日志接口
    r = client.get(f"/api/sniper/{task1}/logs", params={"size": 50})
    ok_logs = r.status_code == 200 and r.json()["total"] >= 1
    check("日志接口有记录", ok_logs, r.text[:160])

    # 列表接口
    r = client.get("/api/sniper")
    check("列表接口", r.status_code == 200 and len(r.json()) >= 1)

    # 删除（paused 可删）
    r = client.delete(f"/api/sniper/{task1}")
    check("删除任务", r.status_code == 200, r.text[:120])

print()
print("共 %d 项：通过 %d，失败 %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL TESTS PASSED")

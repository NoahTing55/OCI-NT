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

    # 模板接口：4 个新模板，顺序固定
    r = client.get("/api/sniper/templates")
    tpl = r.json()
    check("GET /templates 200 且有 4 个模板", r.status_code == 200 and len(tpl) == 4, r.text[:120])
    expected = [
        ("免费 AMD 1C1G", "VM.Standard.E2.1.Micro", 1, 1),
        ("ARM 1C6G", "VM.Standard.A1.Flex", 1, 6),
        ("ARM 2C12G", "VM.Standard.A1.Flex", 2, 12),
        ("E5 1C6G", "VM.Standard.E5.Flex", 1, 6),
    ]
    ok = all(
        tpl[i]["name"] == e[0] and tpl[i]["shape"] == e[1]
        and tpl[i]["ocpus"] == e[2] and tpl[i]["memory_gb"] == e[3]
        for i, e in enumerate(expected)
    )
    check("模板顺序/配置正确", ok, str([(t["name"], t["shape"], t["ocpus"], t["memory_gb"]) for t in tpl])[:200])

    account_id = _make_account()
    body = dict(TASK_BODY, account_id=account_id)

    # 缺子网 → 200（子网可留空，任务启动时自动建网）
    bad = dict(body, subnet_ocid="")
    r = client.post("/api/sniper", json=bad)
    check("建任务缺子网 → 200（启动时自动建网）", r.status_code == 200, r.text[:120])

    # 账号不存在 → 404
    r = client.post("/api/sniper", json=dict(body, account_id=99999))
    check("建任务账号不存在 → 404", r.status_code == 404, r.text[:120])

    # 正常建任务（默认 boot_volume_gb=50）
    r = client.post("/api/sniper", json=body)
    check("建任务成功", r.status_code == 200 and r.json()["status"] == "pending", r.text[:200])
    check("默认硬盘 50GB", r.json().get("boot_volume_gb") == 50, r.text[:200])
    task1 = r.json()["id"]

    # 自定义硬盘容量
    r = client.post("/api/sniper", json=dict(body, boot_volume_gb=100, shape="VM.Standard.E2.1.Micro"))
    check("自定义硬盘 100GB", r.status_code == 200 and r.json().get("boot_volume_gb") == 100, r.text[:200])

    # 硬盘容量越界 → 422
    r = client.post("/api/sniper", json=dict(body, boot_volume_gb=10, shape="VM.Standard.E5.Flex"))
    check("硬盘 <50GB → 422", r.status_code == 422, r.text[:120])

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

    # ---------- 3. 抢机数量（target_count） ----------
    import asyncio  # noqa: E402
    from unittest.mock import AsyncMock, patch  # noqa: E402

    from app.models.models import SnipeTask  # noqa: E402
    from app.workers.sniper import sniper_manager  # noqa: E402

    # API：target_count 透出
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, target_count=3))
    check("建任务 target_count=3 → 200 且透出",
          r.status_code == 200 and r.json().get("target_count") == 3, r.text[:120])
    task3 = r.json()["id"]
    check("新任务 success_count=0", r.json().get("success_count") == 0)

    # 非法值 → 422
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, target_count=0))
    check("target_count=0 → 422", r.status_code == 422)
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, target_count=101))
    check("target_count=101 → 422", r.status_code == 422)

    # Worker：_on_success 连续调 3 次，第 3 次才结束
    async def _run_multi():
        db = SessionLocal()
        try:
            t = db.get(SnipeTask, task3)
            t.status = "running"
            db.commit()
        finally:
            db.close()
        cfg = {"account_id": account_id, "account_name": "test", "region": "ap-seoul-1",
               "shape": "VM.Standard.A1.Flex", "ocpus": 4, "memory_gb": 24,
               "display_name": "snipe-test", "compartment": "ocid1.tenancy.oc1..test",
               "root_password": "pw123", "target_count": 3}
        with patch("app.workers.sniper.telegram.send_message", new=AsyncMock()), \
             patch.object(sniper_manager, "_get_public_ip", new=AsyncMock(return_value="1.2.3.4")), \
             patch("app.workers.sniper.sync_instance_domains", new=AsyncMock(return_value=[])):
            results = []
            for i in range(3):
                done, cnt = await sniper_manager._on_success(
                    task3, cfg, None, f"ocid1.instance.oc1..i{i}", "测试", display_name=f"snipe-test-{i+1}")
                results.append((done, cnt))
        return results

    results = asyncio.run(_run_multi())
    check("第1次 _on_success → (False, 1)", results[0] == (False, 1), str(results[0]))
    check("第2次 _on_success → (False, 2)", results[1] == (False, 2), str(results[1]))
    check("第3次 _on_success → (True, 3)", results[2] == (True, 3), str(results[2]))

    db = SessionLocal()
    try:
        t = db.get(SnipeTask, task3)
        check("DB success_count=3", t.success_count == 3, str(t.success_count))
        check("DB status=success（达目标才结束）", t.status == "success", t.status)
        check("DB instance_ocid 逗号分隔 3 个", len((t.instance_ocid or "").split(",")) == 3, t.instance_ocid)
    finally:
        db.close()

    # ---------- 4. 抢机间隔（interval_seconds） ----------
    # 默认值 60
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id))
    check("建任务不传 interval → 默认 60", r.status_code == 200 and r.json().get("interval_seconds") == 60, r.text[:120])
    task_iv = r.json()["id"]
    # 删除该任务以免影响后续（paused/pending 可删）
    client.delete(f"/api/sniper/{task_iv}")

    # 自定义 300
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, interval_seconds=300))
    check("建任务 interval_seconds=300 → 透出", r.status_code == 200 and r.json().get("interval_seconds") == 300, r.text[:120])
    client.delete(f"/api/sniper/{r.json()['id']}")

    # 非法值 → 422
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, interval_seconds=4))
    check("interval_seconds=4 → 422", r.status_code == 422)
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, interval_seconds=3601))
    check("interval_seconds=3601 → 422", r.status_code == 422)

    # ---------- 5. 编辑任务（PUT /{id}） ----------
    # 新建一个 pending 任务用于编辑（E5.Flex，避免与已有任务冲突）
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, shape="VM.Standard.E5.Flex"))
    check("编辑测试：建 pending 任务", r.status_code == 200, r.text[:120])
    task_edit = r.json()["id"]

    # 正常编辑：改多个字段
    r = client.put(f"/api/sniper/{task_edit}", json={
        "ocpus": 2, "memory_gb": 12, "boot_volume_gb": 100,
        "interval_seconds": 300, "display_name": "edited", "target_count": 2,
    })
    check("编辑 pending 任务 → 200", r.status_code == 200, r.text[:120])
    j = r.json()
    check("编辑后 ocpus=2", j.get("ocpus") == 2, str(j.get("ocpus")))
    check("编辑后 memory=12", j.get("memory_gb") == 12)
    check("编辑后硬盘 100GB", j.get("boot_volume_gb") == 100)
    check("编辑后间隔 300s", j.get("interval_seconds") == 300)
    check("编辑后显示名", j.get("display_name") == "edited")
    check("编辑后 status 仍 pending", j.get("status") == "pending", j.get("status"))
    check("编辑后 attempts 不重置", j.get("attempts") == 0)

    # 部分字段编辑：只传一个字段，其他不变
    r = client.put(f"/api/sniper/{task_edit}", json={"display_name": "v2"})
    check("部分字段编辑 → 200 且其他字段不变",
          r.status_code == 200 and r.json().get("display_name") == "v2"
          and r.json().get("ocpus") == 2, r.text[:160])

    # 非法值 → 422
    r = client.put(f"/api/sniper/{task_edit}", json={"boot_volume_gb": 10})
    check("编辑硬盘 <50GB → 422", r.status_code == 422)
    r = client.put(f"/api/sniper/{task_edit}", json={"interval_seconds": 4})
    check("编辑间隔 <5s → 422", r.status_code == 422)

    # 不存在的任务 → 404
    r = client.put("/api/sniper/99999", json={"ocpus": 1})
    check("编辑不存在的任务 → 404", r.status_code == 404)

    # 改到不存在的账号 → 404
    r = client.put(f"/api/sniper/{task_edit}", json={"account_id": 99999})
    check("编辑改到不存在的账号 → 404", r.status_code == 404)

    # running 任务拒绝编辑
    db = SessionLocal()
    try:
        t = db.get(SnipeTask, task_edit)
        t.status = "running"
        db.commit()
    finally:
        db.close()
    r = client.put(f"/api/sniper/{task_edit}", json={"ocpus": 1})
    check("编辑 running 任务 → 400", r.status_code == 400, r.text[:120])

    # success 任务拒绝编辑（task3 在第 3 节已达 success）
    r = client.put(f"/api/sniper/{task3}", json={"ocpus": 1})
    check("编辑 success 任务 → 400", r.status_code == 400, r.text[:120])

    # 唯一性冲突：另建一个 A1.Flex 任务并置 paused，再把 task_edit 改成 A1.Flex
    r = client.post("/api/sniper", json=dict(TASK_BODY, account_id=account_id, shape="VM.Standard.A1.Flex"))
    check("建另一个 A1 任务", r.status_code == 200, r.text[:120])
    other = r.json()["id"]
    db = SessionLocal()
    try:
        for tid in (other, task_edit):
            t = db.get(SnipeTask, tid)
            t.status = "paused"
        db.commit()
    finally:
        db.close()
    r = client.put(f"/api/sniper/{task_edit}", json={"shape": "VM.Standard.A1.Flex"})
    check("编辑导致唯一性冲突 → 400", r.status_code == 400, r.text[:160])
    # 改回不冲突的 shape → 200
    r = client.put(f"/api/sniper/{task_edit}", json={"shape": "VM.Standard.E5.Flex"})
    check("编辑为不冲突 shape → 200", r.status_code == 200, r.text[:120])
    # paused 任务可编辑
    r = client.put(f"/api/sniper/{other}", json={"target_count": 5})
    check("编辑 paused 任务 → 200", r.status_code == 200 and r.json().get("target_count") == 5, r.text[:120])

print()
print("共 %d 项：通过 %d，失败 %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL TESTS PASSED")

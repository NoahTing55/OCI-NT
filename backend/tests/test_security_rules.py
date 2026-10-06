"""安全规则（放行所有端口）测试：ensure_allow_all_ingress 编排逻辑单测。

运行：/tmp/ocitest/bin/python backend/tests/test_security_rules.py
（plain assert，无需 pytest；用 fake client 注入，不碰真实 OCI）
"""
import asyncio
import os
import sys

DB_PATH = "/tmp/test_security_rules.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from app.schemas.schemas import SnipeTaskCreate  # noqa: E402
from app.services.security_rules import _has_allow_all, ensure_allow_all_ingress  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


class FakeResp:
    def __init__(self, status=200):
        self.status_code = status
        self.text = "ok"


class FakeClient:
    """鸭子类型 fake client：预设子网/安全列表，记录 PUT 调用。"""

    def __init__(self, *, subnet=None, seclist=None, put_status=200, raise_on=None):
        self._subnet = subnet or {"securityListIds": ["ocid1.securitylist.test"]}
        self._seclist = seclist or {"ingressSecurityRules": []}
        self._put_status = put_status
        self._raise_on = raise_on or ()
        self.put_calls = []

    async def get_subnet(self, subnet_id):
        if "get_subnet" in self._raise_on:
            raise RuntimeError("subnet boom")
        return self._subnet

    async def get_security_list(self, seclist_id):
        if "get_security_list" in self._raise_on:
            raise RuntimeError("seclist boom")
        return self._seclist

    async def update_security_list(self, seclist_id, ingress_rules):
        self.put_calls.append((seclist_id, ingress_rules))
        return FakeResp(self._put_status)


def run(coro):
    return asyncio.run(coro)


# ---------- 1. _has_allow_all 纯函数 ----------
check("已有 all+0.0.0.0/0 返回 True",
      _has_allow_all([{"protocol": "all", "source": "0.0.0.0/0"}]) is True)
check("只有 tcp 规则返回 False",
      _has_allow_all([{"protocol": "6", "source": "0.0.0.0/0"}]) is False)
check("all 但来源受限返回 False",
      _has_allow_all([{"protocol": "all", "source": "10.0.0.0/8"}]) is False)
check("空列表返回 False", _has_allow_all([]) is False)
check("None 返回 False", _has_allow_all(None) is False)

# ---------- 2. 规则已存在：不 PUT，返回 True ----------
c1 = FakeClient(seclist={"ingressSecurityRules": [
    {"protocol": "6", "source": "0.0.0.0/0", "description": "ssh"},
    {"protocol": "all", "source": "0.0.0.0/0", "description": "old"},
]})
check("已存在：返回 True", run(ensure_allow_all_ingress(c1, "ocid1.subnet.x")) is True)
check("已存在：无 PUT 调用", c1.put_calls == [], c1.put_calls)

# ---------- 3. 规则不存在：追加后 PUT 全量，返回 True ----------
c2 = FakeClient(seclist={"ingressSecurityRules": [
    {"protocol": "6", "source": "0.0.0.0/0", "description": "ssh"},
]})
check("缺失：返回 True", run(ensure_allow_all_ingress(c2, "ocid1.subnet.x")) is True)
check("缺失：PUT 恰一次", len(c2.put_calls) == 1, c2.put_calls)
put_rules = c2.put_calls[0][1]
check("缺失：PUT 保留原有规则", any(r.get("description") == "ssh" for r in put_rules), put_rules)
check("缺失：PUT 追加 all 规则",
      any(r.get("protocol") == "all" and r.get("source") == "0.0.0.0/0" for r in put_rules),
      put_rules)

# ---------- 4. PUT 失败：返回 False，不抛异常 ----------
c3 = FakeClient(put_status=500)
try:
    r3 = run(ensure_allow_all_ingress(c3, "ocid1.subnet.x"))
    check("PUT 失败：返回 False", r3 is False, r3)
except Exception as e:
    check("PUT 失败：不抛异常", False, repr(e))

# ---------- 5. 子网无安全列表：返回 False ----------
c4 = FakeClient(subnet={"securityListIds": []})
check("无安全列表：返回 False",
      run(ensure_allow_all_ingress(c4, "ocid1.subnet.x")) is False)

# ---------- 6. GET 异常：返回 False，不抛异常 ----------
c5 = FakeClient(raise_on=("get_subnet",))
try:
    r5 = run(ensure_allow_all_ingress(c5, "ocid1.subnet.x"))
    check("GET 异常：返回 False", r5 is False, r5)
except Exception as e:
    check("GET 异常：不抛异常", False, repr(e))

# ---------- 7. Schema 默认值 ----------
s = SnipeTaskCreate(
    account_id=1, region="ap-seoul-1", image_ocid="ocid1.image.x",
    availability_domain="AD-1",
)
check("SnipeTaskCreate 默认 open_all_ports=True", s.open_all_ports is True, s.open_all_ports)
s2 = SnipeTaskCreate(
    account_id=1, region="ap-seoul-1", image_ocid="ocid1.image.x",
    availability_domain="AD-1", open_all_ports=False,
)
check("SnipeTaskCreate 可关闭", s2.open_all_ports is False, s2.open_all_ports)

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
sys.exit(1 if FAIL else 0)

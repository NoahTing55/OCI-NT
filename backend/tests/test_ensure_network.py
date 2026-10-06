"""一键建网（ensure-network）测试：编排逻辑单测，用 fake client 注入。

运行：/tmp/ocitest/bin/python backend/tests/test_ensure_network.py
（plain assert，无需 pytest；不碰真实 OCI，只测 _ensure_network 编排）
"""
import asyncio
import os
import sys

DB_PATH = "/tmp/test_ensure_network.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi import HTTPException  # noqa: E402

from app.services.network_ensure import _ensure_network  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    @property
    def text(self):
        return str(self._data)


class FakeClient:
    """鸭子类型 fake client：按构造参数返回预设资源，记录关键调用。"""

    def __init__(self, *, ads=None, vcns=None, igs=None, rts=None, subnets=None,
                 fail_subnet_cidrs=()):
        self.ads = ads if ads is not None else [{"name": "AD-1"}]
        self.vcns = vcns or []
        self.igs = igs or []
        self.rts = rts or []
        self.subnets = subnets or []
        self.fail_subnet_cidrs = set(fail_subnet_cidrs)
        self.calls = []

    async def request(self, method, service, path):
        self.calls.append(("request", service))
        return FakeResp(self.ads)

    async def list_vcns(self, comp):
        return self.vcns

    async def get_vcn(self, vcn_id):
        return FakeResp({"id": vcn_id, "displayName": "oci-panel-vcn", "lifecycleState": "AVAILABLE"})

    async def create_vcn(self, comp, display_name="oci-panel-vcn"):
        self.calls.append(("create_vcn", comp))
        vcn = {"id": "ocid1.vcn.new", "displayName": display_name, "lifecycleState": "AVAILABLE"}
        self.vcns.append(vcn)
        return FakeResp(vcn)

    async def list_internet_gateways(self, comp, vcn_id):
        return self.igs

    async def create_internet_gateway(self, comp, vcn_id, display_name="oci-panel-ig"):
        self.calls.append(("create_ig", vcn_id))
        ig = {"id": "ocid1.ig.new", "displayName": display_name, "lifecycleState": "AVAILABLE"}
        self.igs.append(ig)
        return FakeResp(ig)

    async def list_route_tables(self, comp, vcn_id):
        return self.rts

    async def update_route_table(self, rt_id, rules):
        self.calls.append(("update_rt", rt_id, len(rules)))
        for r in self.rts:
            if r["id"] == rt_id:
                r["routeRules"] = rules
        return FakeResp({"id": rt_id})

    async def list_subnets_of_vcn(self, comp, vcn_id):
        return self.subnets

    async def create_subnet(self, comp, vcn_id, ad, cidr_block="10.0.0.0/24",
                            display_name="oci-panel-subnet", route_table_id=""):
        self.calls.append(("create_subnet", cidr_block))
        if cidr_block in self.fail_subnet_cidrs:
            return FakeResp({"message": "CidrBlock already in use"}, 400)
        s = {"id": "ocid1.subnet." + cidr_block.replace(".", "-").replace("/", "-"),
             "displayName": display_name, "availabilityDomain": ad,
             "lifecycleState": "AVAILABLE"}
        self.subnets.append(s)
        return FakeResp(s)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


TENANCY = "ocid1.tenancy.oc1..fake"

# ---------- 1. 全部复用：created 全 False ----------
c1 = FakeClient(
    vcns=[{"id": "ocid1.vcn.user", "displayName": "user-vcn", "lifecycleState": "AVAILABLE"}],
    igs=[{"id": "ocid1.ig.user", "displayName": "user-ig", "lifecycleState": "AVAILABLE"}],
    rts=[{"id": "ocid1.rt.user", "lifecycleState": "AVAILABLE",
          "routeRules": [{"destination": "0.0.0.0/0", "networkEntityId": "ocid1.ig.user"}]}],
    subnets=[{"id": "ocid1.subnet.user", "displayName": "user-sub",
              "availabilityDomain": "AD-1", "lifecycleState": "AVAILABLE"}],
)
r1 = run(_ensure_network(c1, TENANCY, availability_domain="AD-1"))
check("复用：created 全 False", r1["created"] == {"vcn": False, "ig": False, "subnet": False}, r1["created"])
check("复用：返回已有子网", r1["subnet_ocid"] == "ocid1.subnet.user", r1["subnet_ocid"])
check("复用：无创建调用", not any(k[0].startswith("create_") for k in c1.calls), c1.calls)

# ---------- 2. 全新建：created 全 True，路由被追加 ----------
c2 = FakeClient(
    rts=[{"id": "ocid1.rt.new", "lifecycleState": "AVAILABLE", "routeRules": []}],
)
r2 = run(_ensure_network(c2, TENANCY))  # 不传 AD，取 fake 的 AD-1
check("新建：created 全 True", r2["created"] == {"vcn": True, "ig": True, "subnet": True}, r2["created"])
check("新建：AD 取第一个", r2["availability_domain"] == "AD-1", r2["availability_domain"])
check("新建：子网 ocid 非空", bool(r2["subnet_ocid"]), r2)
rt_updates = [k for k in c2.calls if k[0] == "update_rt"]
check("新建：路由表被追加规则", len(rt_updates) == 1 and rt_updates[0][2] == 1, c2.calls)
check("新建：VCN 名固定", r2["vcn_name"] == "oci-panel-vcn", r2["vcn_name"])

# ---------- 3. 路由已有默认路由：不调 update ----------
c3 = FakeClient(
    vcns=[{"id": "ocid1.vcn.u", "displayName": "u", "lifecycleState": "AVAILABLE"}],
    igs=[{"id": "ocid1.ig.u", "displayName": "u", "lifecycleState": "AVAILABLE"}],
    rts=[{"id": "ocid1.rt.u", "lifecycleState": "AVAILABLE",
          "routeRules": [{"destination": "0.0.0.0/0", "destinationType": "CIDR_BLOCK",
                          "networkEntityId": "ocid1.ig.u"}]}],
    subnets=[{"id": "ocid1.subnet.u", "displayName": "u",
              "availabilityDomain": "AD-1", "lifecycleState": "AVAILABLE"}],
)
run(_ensure_network(c3, TENANCY, availability_domain="AD-1"))
check("路由已存在：不调 update_route_table",
      not any(k[0] == "update_rt" for k in c3.calls), c3.calls)

# ---------- 4. CIDR 全冲突：400 中文错，试了 3 个 CIDR ----------
c4 = FakeClient(
    rts=[{"id": "ocid1.rt.4", "lifecycleState": "AVAILABLE", "routeRules": []}],
    fail_subnet_cidrs={"10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"},
)
try:
    run(_ensure_network(c4, TENANCY, availability_domain="AD-1"))
    check("CIDR 冲突：抛 400", False, "未抛异常")
except HTTPException as e:
    tried = [k[1] for k in c4.calls if k[0] == "create_subnet"]
    check("CIDR 冲突：抛 400", e.status_code == 400, e.status_code)
    check("CIDR 冲突：3 个 CIDR 都试过",
          tried == ["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"], tried)

# ---------- 5. 幂等：第二次调用复用第一次建的，不再创建 ----------
c5 = FakeClient(
    rts=[{"id": "ocid1.rt.5", "lifecycleState": "AVAILABLE", "routeRules": []}],
)
run(_ensure_network(c5, TENANCY, availability_domain="AD-1"))
c5.calls.clear()
r5 = run(_ensure_network(c5, TENANCY, availability_domain="AD-1"))
check("幂等：第二次 created 全 False", r5["created"] == {"vcn": False, "ig": False, "subnet": False}, r5["created"])
check("幂等：第二次无创建调用", not any(k[0].startswith("create_") for k in c5.calls), c5.calls)

# ---------- 6. 无可用域：400 ----------
c6 = FakeClient(ads=[])
try:
    run(_ensure_network(c6, TENANCY))
    check("无可用域：抛 400", False, "未抛异常")
except HTTPException as e:
    check("无可用域：抛 400", e.status_code == 400, e.status_code)

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)

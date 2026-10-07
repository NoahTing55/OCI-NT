"""账号信息识别（对标 OCI-Start OciClassLoader）：get_account_info。

逻辑：
- 注册时间：GET /20160918/compartments/{tenancyId} 取 timeCreated
- 账号类型：shapes 里有无 AMD E3/E4/E5（memoryInGBs > 1.0）+ 是否超 1 个月
  - 能开大内存 AMD + 超 1 个月 → upgraded
  - 能开大内存 AMD + 超 1 个月 → upgraded；能开 + 不超 → trial；不能开 → free（OCI-Start OciClassLoader）
  - 不能 → free

运行：python backend/tests/test_account_info.py（plain assert，无需 pytest）
"""
import asyncio
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/home/hatch/workspace/oci-panel/backend")

from app.core.oci_client import OciClient  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


# ---------- 1. _parse_ocid_time ----------
check("解析标准 ISO8601",
      OciClient._parse_ocid_time("2024-03-15T10:30:00.000Z") == datetime(2024, 3, 15, 10, 30))
check("解析无毫秒",
      OciClient._parse_ocid_time("2023-01-01T00:00:00Z") == datetime(2023, 1, 1))
check("空值 → None", OciClient._parse_ocid_time(None) is None)
check("非法值 → None", OciClient._parse_ocid_time("not-a-time") is None)


# ---------- 2. _is_older_than_one_month ----------
old = datetime.utcnow() - timedelta(days=60)
new = datetime.utcnow() - timedelta(days=10)
check("60 天前 → 超 1 个月", OciClient._is_older_than_one_month(old) is True)
check("10 天前 → 不超", OciClient._is_older_than_one_month(new) is False)
check("None → False", OciClient._is_older_than_one_month(None) is False)


# ---------- 3. _can_create_large_amd ----------
def shape(name, mem):
    return {"shape": name, "memoryInGBs": mem}


check("E5 大内存 → True",
      OciClient._can_create_large_amd([shape("VM.Standard.E5.Flex", 24.0)]) is True)
check("E4 大内存 → True",
      OciClient._can_create_large_amd([shape("VM.Standard.E4.Flex", 16.0)]) is True)
check("E3 大内存 → True",
      OciClient._can_create_large_amd([shape("VM.Standard3.Flex", 8.0)]) is True)
check("大小写不敏感",
      OciClient._can_create_large_amd([shape("vm.standard.e5.flex", 24.0)]) is True)
check("E5 但内存 1.0 → False（OCI-Start 要求 > 1.0）",
      OciClient._can_create_large_amd([shape("VM.Standard.E5.Flex", 1.0)]) is False)
check("只有 E2 → False",
      OciClient._can_create_large_amd([shape("VM.Standard.E2.1.Micro", 1.0)]) is False)
check("只有 A1 → False",
      OciClient._can_create_large_amd([shape("VM.Standard.A1.Flex", 24.0)]) is False)
check("空列表 → False", OciClient._can_create_large_amd([]) is False)
check("缺 memoryInGBs → False",
      OciClient._can_create_large_amd([{"shape": "VM.Standard.E5.Flex"}]) is False)


# ---------- 4. get_account_info 端到端（mock） ----------
class FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def make_client(time_created=None, shapes=None, comp_status=200, shape_status=200):
    c = OciClient.__new__(OciClient)
    c.tenancy_ocid = "ocid1.tenancy.oc1..x"
    c.region = "ap-seoul-1"

    async def fake_request(method, service, path, json_body=None):
        if "/compartments/" in path:
            return FakeResp(comp_status, {"timeCreated": time_created} if time_created else {})
        if "/shapes" in path:
            return FakeResp(shape_status, shapes or [])
        return FakeResp(404, {})

    c.request = fake_request
    return c


async def run_e2e():
    old_ts = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    new_ts = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    e5_shapes = [shape("VM.Standard.E5.Flex", 24.0)]
    e3_shapes = [shape("VM.Standard3.Flex", 16.0)]
    e4_shapes = [shape("VM.Standard.E4.Flex", 32.0)]
    free_shapes = [shape("VM.Standard.E2.1.Micro", 1.0), shape("VM.Standard.A1.Flex", 24.0)]

    # 有 E5 大内存 → upgraded（OCI-Start 原逻辑）
    r = await make_client(old_ts, e5_shapes).get_account_info()
    check("E5 大内存 → upgraded", r["account_type"] == "upgraded", r)

    # 有 E3 大内存 → upgraded
    r = await make_client(old_ts, e3_shapes).get_account_info()
    check("E3 大内存 → upgraded", r["account_type"] == "upgraded", r)

    # 有 E4 大内存 → upgraded
    r = await make_client(old_ts, e4_shapes).get_account_info()
    check("E4 大内存 → upgraded", r["account_type"] == "upgraded", r)

    # 新号有 AMD 也算 upgraded（无 trial 档，合并）
    r = await make_client(new_ts, e5_shapes).get_account_info()
    check("新号有 AMD → upgraded（无 trial）", r["account_type"] == "upgraded", r)

    # 无付费 AMD → free
    r = await make_client(old_ts, free_shapes).get_account_info()
    check("无付费 AMD → free", r["account_type"] == "free", r)

    # E5 但内存 <= 1 → free（OCI-Start 要求 memory > 1.0）
    r = await make_client(old_ts, [shape("VM.Standard.E5.Flex", 1.0)]).get_account_info()
    check("E5 内存<=1 → free", r["account_type"] == "free", r)

    # registered_at 解析
    r = await make_client(old_ts, e5_shapes).get_account_info()
    check("registered_at 解析", r["registered_at"] is not None and r["registered_at"].year == int(old_ts[:4]), r)

    # compartment 查不到 → registered_at None，但类型仍可判定
    r = await make_client(old_ts, e5_shapes, comp_status=404).get_account_info()
    check("compartment 404 → registered_at None + 类型 upgraded", r["registered_at"] is None and r["account_type"] == "upgraded", r)

    # shapes 查失败 → account_type None（不抛异常）
    r = await make_client(old_ts, [], shape_status=403).get_account_info()
    check("shapes 失败 → account_type None", r["account_type"] is None and r["registered_at"] is not None, r)

# 网络异常不抛
    c = make_client(old_ts, amd_shapes)

    async def boom(method, service, path, json_body=None):
        raise RuntimeError("boom")

    c.request = boom
    r = await c.get_account_info()
    check("异常 → None 不抛", r["account_type"] is None and r["registered_at"] is None, r)


asyncio.run(run_e2e())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

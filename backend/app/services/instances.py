"""实例聚合查询服务：多账号并发拉取实例并补全公网/私网 IP。

IP 查询是 N+1 的（实例 → 主 VNIC → IP），实例很多时会慢；
M4 已加 Redis 缓存（fetch_all_instances_cached，TTL 可配，默认 60 秒）。
写操作（批量开关机/换 IP/编辑/抢机成功/批量创建成功）后调
invalidate_instance_cache() 失效缓存；Redis 不可用时自动降级为直接查询。
"""
import asyncio
import hashlib
import json
import logging

from app.core.config import settings
from app.core.oci_factory import build_client_for_account, compartment_of
from app.core.redis_client import get_redis, get_sync_redis
from app.models.models import Account

logger = logging.getLogger(__name__)

CACHE_PREFIX = "instances:"


async def _fetch_one(account: Account) -> tuple:
    """查单个账号的实例。返回 (account, items, error)。"""
    client = build_client_for_account(account)
    try:
        compartment = compartment_of(account)
        raw_list = await client.list_instances(compartment)
        items = []
        for inst in raw_list:
            item = {
                "account_id": account.id,
                "account_name": account.name,
                "region": account.region,
                "instance_id": inst.get("id"),
                "display_name": inst.get("displayName"),
                "lifecycle_state": inst.get("lifecycleState"),
                "shape": inst.get("shape"),
                "availability_domain": inst.get("availabilityDomain"),
                "compartment_id": inst.get("compartmentId"),
                "time_created": inst.get("timeCreated"),
                "public_ip": None,
                "private_ip": None,
            }
            try:
                atts = await client.list_vnic_attachments(compartment, inst["id"])
                if atts:
                    vnic = (await client.get_vnic(atts[0]["vnicId"])).json()
                    item["public_ip"] = vnic.get("publicIp")
                    item["private_ip"] = vnic.get("privateIp")
            except Exception as e:
                item["ip_error"] = str(e)[:120]
                logger.warning("实例 %s 查 IP 失败：%s", inst.get("id"), e)
            items.append(item)
        return account, items, None
    except Exception as e:
        logger.warning("账号「%s」实例查询失败：%s", account.name, e)
        return account, [], str(e)[:200]
    finally:
        await client.aclose()


async def fetch_all_instances(accounts: list) -> tuple:
    """并发查多个账号。返回 (items, errors)。"""
    results = await asyncio.gather(*[_fetch_one(a) for a in accounts])
    items, errors = [], []
    for account, acc_items, err in results:
        if err:
            errors.append({"account_id": account.id, "name": account.name, "error": err})
        items.extend(acc_items)
    return items, errors


async def get_instance_public_ip(account: Account, instance_ocid: str):
    """查单个实例当前公网 IP（CF 手动同步用）。查不到返回 None。"""
    client = build_client_for_account(account)
    try:
        compartment = compartment_of(account)
        atts = await client.list_vnic_attachments(compartment, instance_ocid)
        if not atts:
            return None
        vnic = (await client.get_vnic(atts[0]["vnicId"])).json()
        return vnic.get("publicIp")
    finally:
        await client.aclose()


def _cache_key(account_ids: list, filters: dict) -> str:
    """缓存 key：账号 id 排序 + 筛选条件摘要。"""
    raw = json.dumps({"a": sorted(account_ids), "f": filters}, sort_keys=True)
    return CACHE_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


async def fetch_all_instances_cached(accounts: list, filters: dict | None = None) -> tuple:
    """带 Redis 缓存的聚合查询。

    filters 参与 key 计算（如 account_id/region/state）。TTL 取
    settings.INSTANCE_CACHE_TTL，<=0 则关闭缓存。Redis 不可用时降级为直接查询，
    绝不因缓存报错影响主流程。
    """
    filters = filters or {}
    ttl = settings.INSTANCE_CACHE_TTL
    if ttl <= 0 or not accounts:
        return await fetch_all_instances(accounts)

    key = _cache_key([a.id for a in accounts], filters)
    r = await get_redis()
    if r is not None:
        try:
            raw = await r.get(key)
            if raw:
                data = json.loads(raw)
                return data.get("items", []), data.get("errors", [])
        except Exception as e:
            logger.warning("实例缓存读取失败，降级直接查询：%s", e)

    items, errors = await fetch_all_instances(accounts)
    if r is not None:
        try:
            await r.set(
                key, json.dumps({"items": items, "errors": errors}, ensure_ascii=False), ex=ttl
            )
        except Exception as e:
            logger.warning("实例缓存写入失败：%s", e)
    return items, errors


def invalidate_instance_cache() -> None:
    """写操作成功后失效实例列表缓存。Redis 不可用时静默跳过。

    调用点：批量开关机/重启/终止完成、换 IP 成功、编辑实例成功、
    抢机成功、批量创建任务完成。
    """
    r = get_sync_redis()
    if r is None:
        return
    try:
        n = 0
        for key in r.scan_iter(match=CACHE_PREFIX + "*", count=200):
            r.delete(key)
            n += 1
        if n:
            logger.info("实例列表缓存已失效（%d 个 key）", n)
    except Exception as e:
        logger.warning("实例缓存失效失败：%s", e)

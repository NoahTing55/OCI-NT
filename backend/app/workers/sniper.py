"""抢机引擎：asyncio 常驻 worker（M3 完整实现）。

设计要点（来自实战经验总结）：
1. 错误分类不能只看 HTTP 状态码，必须看错误文本：
   "Out of host capacity" 实际返回的是 500 InternalError；
2. 429 必须指数退避，硬怼容易连带账号被风控；
3. 每次发起创建前先查存量，防重复创建；抢到即停；
4. 单账号全局限流 + 抖动，不用固定节奏；
5. 成功 / 失败都走 Telegram 推送（通知渠道只保留 TG）。

并发模型：SniperManager 常驻在 api 进程内（与 APScheduler 同进程），
每个 running 任务一个 asyncio worker，共享账号级限流器；
每个账号的 OciClient 独立（独立代理），互不干扰。
"""
import asyncio
import logging
import random
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from app.core import telegram
from app.core.audit import log_operation
from app.core.cloud_init import build_root_password_script
from app.services.settings import get_setting
from app.core.deps import SessionLocal
from app.core.oci_factory import build_client_for_account, compartment_of
from app.models.models import Account, SnipeLog, SnipeTask
from app.services.cloudflare import sync_instance_domains
from app.services.instances import invalidate_instance_cache
from app.services.network_ensure import ensure_subnet

logger = logging.getLogger(__name__)

# ---------------- 错误分类 ----------------

# 文本特征 → 配置错误（重试无意义，直接停任务告警）
CONFIG_ERROR_HINTS = (
    "InvalidParameter",  # 参数非法（子网 / 镜像 / AD / compartment 配错）
    "InvalidShape",  # shape 不存在或该区域不支持
    "InvalidImage",  # 镜像 OCID 无效
    "InvalidSubnet",  # 子网 OCID 无效
    "InvalidAvailabilityDomain",  # 可用域无效
    "InvalidCompartmentId",  # compartment 无效
    "NotAuthorizedOrNotFound",  # 资源不存在或无权限（常因配错 OCID）
    "LimitExceeded",  # 配额超限：涨配额前重试无意义，先停任务告警
)


def classify_launch_error(status_code: int | None, text: str) -> tuple[str, str]:
    """抢机错误分类（纯函数，可单测）。返回 (kind, 中文说明)。

    kind 取值：
    - success：2xx，创建成功
    - no_capacity：资源不足（Out of host capacity / 500 InternalError），短退避重试
    - rate_limited：429，指数退避 + 账号降频
    - config_error：400 参数错误，停任务告警
    - auth_error：401 密钥失效，停任务告警 + 账号置密钥失效
    - unknown：其他，先短退避重试，连续 10 次则停任务
    """
    t = (text or "")[:500]
    if status_code is not None and 200 <= status_code < 300:
        return "success", "实例创建成功"
    if status_code == 401 or "NotAuthenticated" in t:
        return "auth_error", "账号密钥失效（401），请检查指纹/私钥是否匹配"
    if status_code == 400 or any(h in t for h in CONFIG_ERROR_HINTS):
        return "config_error", "配置错误（400），重试无意义：" + t[:160]
    if status_code == 429 or "TooManyRequests" in t:
        return "rate_limited", "触发限流（429），指数退避"
    if "Out of host capacity" in t or (status_code == 500 and "InternalError" in t):
        return "no_capacity", "暂无可用容量（Out of host capacity），稍后重试"
    if status_code == 500:
        # 500 但文本不是 InternalError：按未知处理，
        # 避免把真正的服务端异常误判成"无货"进入死循环
        return "unknown", "服务端异常（500）：" + t[:160]
    if status_code is None:
        return "unknown", "网络异常：" + t[:160]
    return "unknown", "未知错误（HTTP %s）：%s" % (status_code, t[:160])


# ---------------- 单账号限流器 ----------------


class AccountRateLimiter:
    """单账号限流器：每分钟最多 N 次请求，带抖动；429 后自动降频。

    频率宁可保守：硬怼 429 容易连带账号被 Oracle 风控。
    """

    def __init__(self, max_per_minute: int = 12):
        self._base = max_per_minute
        self._max: dict[int, int] = defaultdict(lambda: max_per_minute)
        self._hits: dict[int, deque] = defaultdict(deque)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._recover_at: dict[int, float] = {}

    async def acquire(self, account_id: int):
        """取一个请求配额；超限则 sleep 到滑动窗口腾出位置（带抖动）。"""
        # 降频恢复：冷却 10 分钟后回到基线频率
        if self._recover_at.get(account_id, 0) < time.monotonic():
            self._max[account_id] = self._base
        async with self._locks[account_id]:
            hits = self._hits[account_id]
            while len(hits) >= self._max[account_id]:
                wait = 60 - (time.monotonic() - hits[0]) + random.uniform(0.5, 2.0)
                if wait > 0:
                    await asyncio.sleep(wait)
                now = time.monotonic()
                while hits and now - hits[0] >= 60:
                    hits.popleft()
            hits.append(time.monotonic())

    def on_rate_limited(self, account_id: int):
        """遇到 429：配额减半（最低 3/分钟），10 分钟后自动恢复。"""
        cur = self._max[account_id]
        self._max[account_id] = max(3, cur // 2)
        self._recover_at[account_id] = time.monotonic() + 600
        logger.warning("账号 %s 触发 429，限流降频至 %s 次/分钟", account_id, self._max[account_id])


# ---------------- 状态 CAS ----------------


def _cas_status(db, task_id: int, expected: set, new_status: str, **extra) -> bool:
    """CAS 更新任务状态：只有当前状态在 expected 中才更新。

    抢到即停 / 暂停 / 恢复都走这里，防止多 worker 或并发 API 重复操作。
    返回是否更新成功。
    """
    rows = (
        db.query(SnipeTask)
        .filter(SnipeTask.id == task_id, SnipeTask.status.in_(expected))
        .update({"status": new_status, "updated_at": datetime.utcnow(), **extra}, synchronize_session=False)
    )
    db.commit()
    return rows == 1


# 生命周期终态判断：PROVISIONING/RUNNING 等都算"已存在"，只有 TERMINATED 算已释放
ACTIVE_STATES = {"PROVISIONING", "RUNNING", "STARTING", "STOPPING", "STOPPED"}


class SniperManager:
    """管理所有抢机任务的 asyncio worker。常驻 api 进程，由 lifespan 启动/停止。"""

    def __init__(self):
        self._workers: dict[int, asyncio.Task] = {}
        self._stop_events: dict[int, asyncio.Event] = {}
        self._limiters = AccountRateLimiter()
        self._cleanup_task: asyncio.Task | None = None
        self._started = False

    # ---------- 生命周期 ----------

    async def start(self):
        """启动引擎：恢复 DB 中 running 的任务（服务重启场景）+ 启动日志清理。"""
        if self._started:
            return
        self._started = True
        db = SessionLocal()
        try:
            task_ids = [t.id for t in db.query(SnipeTask).filter(SnipeTask.status == "running").all()]
        finally:
            db.close()
        for tid in task_ids:
            await self._spawn(tid, resumed=True)
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("抢机引擎启动，恢复 %d 个 running 任务", len(task_ids))

    async def stop(self):
        """停止引擎：唤醒所有 worker 并等待退出。"""
        for ev in self._stop_events.values():
            ev.set()
        for t in list(self._workers.values()):
            t.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._stop_events.clear()
        self._started = False
        logger.info("抢机引擎已停止")

    # ---------- 任务控制（API 层调用） ----------

    async def start_task(self, task_id: int) -> bool:
        """CAS pending/paused/stopped/failed → running，并启动 worker。"""
        db = SessionLocal()
        try:
            ok = _cas_status(
                db, task_id, {"pending", "paused", "stopped", "failed"}, "running",
                started_at=datetime.utcnow(), last_error="",
            )
        finally:
            db.close()
        if ok:
            await self._spawn(task_id)
        return ok

    async def pause_task(self, task_id: int) -> bool:
        """CAS running → paused，并唤醒 worker 使其尽快退出（可中断 sleep）。"""
        db = SessionLocal()
        try:
            ok = _cas_status(db, task_id, {"running"}, "paused")
        finally:
            db.close()
        if ok:
            ev = self._stop_events.get(task_id)
            if ev:
                ev.set()
        return ok

    async def _spawn(self, task_id: int, resumed: bool = False):
        if task_id in self._workers and not self._workers[task_id].done():
            return
        self._stop_events[task_id] = asyncio.Event()
        self._workers[task_id] = asyncio.create_task(self._worker(task_id, resumed))

    # ---------- worker 主循环 ----------

    def _log(self, task_id: int, level: str, message: str):
        """写任务日志：独立短会话，不阻塞 worker。"""
        db = SessionLocal()
        try:
            db.add(SnipeLog(task_id=task_id, level=level, message=message[:2000]))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("写抢机日志失败 task=%s", task_id)
        finally:
            db.close()

    def _is_running(self, task_id: int) -> bool:
        db = SessionLocal()
        try:
            task = db.get(SnipeTask, task_id)
            return bool(task and task.status == "running")
        finally:
            db.close()

    def _bump_attempts(self, task_id: int):
        db = SessionLocal()
        try:
            db.query(SnipeTask).filter(SnipeTask.id == task_id).update(
                {SnipeTask.attempts: SnipeTask.attempts + 1, SnipeTask.updated_at: datetime.utcnow()},
                synchronize_session=False,
            )
            db.commit()
        finally:
            db.close()

    async def _sleep(self, task_id: int, seconds: float):
        """可中断的 sleep：pause/stop 时立刻唤醒。"""
        ev = self._stop_events.get(task_id)
        if ev is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(ev.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _worker(self, task_id: int, resumed: bool = False):
        try:
            await self._run(task_id, resumed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("抢机 worker 异常退出 task=%s", task_id)
            self._log(task_id, "error", "worker 异常退出，请检查服务端日志")
            db = SessionLocal()
            try:
                _cas_status(db, task_id, {"running"}, "failed",
                            last_error="worker 异常退出", finished_at=datetime.utcnow())
            finally:
                db.close()
        finally:
            self._workers.pop(task_id, None)
            self._stop_events.pop(task_id, None)

    async def _run(self, task_id: int, resumed: bool):
        # 快照任务配置（避免长持 DB session；proxy 用 joinedload 预加载，
        # 否则 session 关闭后访问 account.proxy 会报 DetachedInstanceError）
        db = SessionLocal()
        try:
            task = db.get(SnipeTask, task_id)
            if not task:
                return
            account = (
                db.query(Account).options(joinedload(Account.proxy))
                .filter(Account.id == task.account_id).first()
            )
            if not account:
                self._log(task_id, "error", "账号不存在，任务停止")
                _cas_status(db, task_id, {"running"}, "failed",
                            last_error="账号不存在", finished_at=datetime.utcnow())
                return
            cfg = {
                "account_id": account.id,
                "account_name": account.name,
                "region": task.region,
                "shape": task.shape,
                "ocpus": task.ocpus,
                "memory_gb": task.memory_gb,
                "image_ocid": task.image_ocid,
                "subnet_ocid": task.subnet_ocid,
                "ad": task.availability_domain,
                "display_name": task.display_name
                or "snipe-%d-%s" % (task.id, datetime.utcnow().strftime("%Y%m%d%H%M")),
                "target_count": max(1, task.target_count or 1),
                "interval_seconds": max(5, task.interval_seconds or 60),  # 无容量重试间隔（秒）
                "compartment": compartment_of(account),
                "root_password": task.root_password or "",
            }
            client = build_client_for_account(account)
        except ValueError as e:
            # 私钥解密失败（MASTER_KEY 不对或数据损坏）
            self._log(task_id, "error", "构建 OCI 客户端失败：%s" % e)
            _cas_status(db, task_id, {"running"}, "failed",
                        last_error="客户端构建失败：%s" % e, finished_at=datetime.utcnow())
            return
        finally:
            db.close()

        # 子网留空则任务启动前自动建网（OCI-Start 思路）：有则复用、无则创建
        if not cfg["subnet_ocid"]:
            self._log(task_id, "info", "子网未填，自动准备网络（有则复用、无则创建）…")
            try:
                cfg["subnet_ocid"] = await ensure_subnet(account, cfg["region"], cfg["ad"])
            except Exception as e:
                detail = e.detail if isinstance(e, HTTPException) else str(e)
                await self._finish_failed(task_id, cfg, "自动建网失败：%s" % detail)
                return
            # 写回任务，方便在列表/表单看到实际用的子网
            db2 = SessionLocal()
            try:
                t = db2.get(SnipeTask, task_id)
                if t:
                    t.subnet_ocid = cfg["subnet_ocid"]
                    db2.commit()
            finally:
                db2.close()
            self._log(task_id, "info", "网络已就绪，子网 %s" % cfg["subnet_ocid"])

        # root 密码：没填则生成随机密码，通过 cloud-init 下发（开机即生效）
        if not cfg["root_password"]:
            cfg["root_password"] = secrets.token_urlsafe(12)
            db2 = SessionLocal()
            try:
                t = db2.get(SnipeTask, task_id)
                if t:
                    t.root_password = cfg["root_password"]
                    db2.commit()
            finally:
                db2.close()
        user_data = build_root_password_script(cfg["root_password"])

        self._log(
            task_id, "info",
            "抢机启动：%s %sC/%sG @ %s" % (cfg["shape"], cfg["ocpus"], cfg["memory_gb"], cfg["region"])
            + ("（服务重启后恢复）" if resumed else ""),
        )
        try:
            unknown_streak = 0
            backoff_429 = 30.0
            stop_ev = self._stop_events.get(task_id)
            # 本地已抢台数（用于实例名序号后缀；_on_success 返回最新值保持同步）
            snipe_done = 0
            while not (stop_ev and stop_ev.is_set()):
                if not self._is_running(task_id):
                    self._log(task_id, "info", "任务已暂停/停止，worker 退出")
                    break

                # a. 查存量防重复：该账号该 shape 已有非终止实例则直接成功停止
                try:
                    existing = await self._find_existing(client, cfg)
                except Exception as e:
                    self._log(task_id, "warning", "查存量失败，10 秒后重试：%s" % str(e)[:160])
                    await self._sleep(task_id, 10)
                    continue
                if existing:
                    done, snipe_done = await self._on_success(
                        task_id, cfg, client, existing["id"], "查到存量实例（防重复创建）"
                    )
                    if done:
                        break
                    continue

                # b. 单账号限流（带抖动）
                await self._limiters.acquire(cfg["account_id"])

                # c. 发起创建（多台时实例名加序号后缀，避免重名）
                self._bump_attempts(task_id)
                seq = snipe_done + 1
                dn = cfg["display_name"] + ("-%d" % seq if cfg["target_count"] > 1 else "")
                try:
                    resp = await client.launch_instance(
                        compartment_id=cfg["compartment"],
                        availability_domain=cfg["ad"],
                        shape=cfg["shape"],
                        ocpus=cfg["ocpus"],
                        memory_gb=cfg["memory_gb"],
                        image_ocid=cfg["image_ocid"],
                        subnet_ocid=cfg["subnet_ocid"],
                        display_name=dn,
                        user_data=user_data,
                    )
                except (httpx.TimeoutException, httpx.ConnectError, httpx.ProxyError) as e:
                    unknown_streak += 1
                    if unknown_streak >= 10:
                        await self._finish_failed(
                            task_id, cfg, "连续 10 次网络异常，最后一次：%s" % type(e).__name__
                        )
                        break
                    delay = min(5 * unknown_streak, 60) + random.uniform(0, 3)
                    self._log(task_id, "warning",
                              "网络异常（%s），%.0f 秒后重试（连续 %d 次）"
                              % (type(e).__name__, delay, unknown_streak))
                    await self._sleep(task_id, delay)
                    continue
                except Exception as e:
                    unknown_streak += 1
                    if unknown_streak >= 10:
                        await self._finish_failed(task_id, cfg, "连续 10 次未知错误，最后一次：%s" % e)
                        break
                    delay = min(5 * unknown_streak, 60) + random.uniform(0, 3)
                    self._log(task_id, "warning", "请求异常：%s，%.0f 秒后重试" % (str(e)[:160], delay))
                    await self._sleep(task_id, delay)
                    continue

                kind, msg = classify_launch_error(resp.status_code, resp.text)
                if kind == "success":
                    instance_ocid = (resp.json() or {}).get("id", "")
                    done, snipe_done = await self._on_success(
                        task_id, cfg, client, instance_ocid, "抢机成功", display_name=dn)
                    if done:
                        break
                    # 未达目标台数：继续循环抢下一台
                    continue
                if kind == "no_capacity":
                    unknown_streak = 0
                    # 用户可配的抢机间隔 + 小抖动，避免多任务同步重试
                    delay = cfg["interval_seconds"] + random.uniform(0, 5)
                    self._log(task_id, "info", "暂无可用容量，%.1f 秒后重试" % delay)
                    await self._sleep(task_id, delay)
                    continue
                if kind == "rate_limited":
                    unknown_streak = 0
                    self._limiters.on_rate_limited(cfg["account_id"])
                    delay = min(backoff_429, 600) + random.uniform(0, 5)
                    self._log(task_id, "warning",
                              "触发限流，%.0f 秒后重试（退避基数 %.0fs，已降频）" % (delay, backoff_429))
                    backoff_429 *= 2
                    await self._sleep(task_id, delay)
                    continue
                if kind == "config_error":
                    await self._finish_failed(task_id, cfg, msg)
                    break
                if kind == "auth_error":
                    self._mark_account_key_invalid(cfg)
                    await self._finish_failed(task_id, cfg, msg)
                    break
                # unknown
                unknown_streak += 1
                if unknown_streak >= 10:
                    await self._finish_failed(task_id, cfg, "连续 10 次未知错误，最后一次：" + msg)
                    break
                delay = min(5 * unknown_streak, 60) + random.uniform(0, 3)
                self._log(task_id, "warning", "%s（连续 %d 次），%.0f 秒后重试" % (msg, unknown_streak, delay))
                await self._sleep(task_id, delay)
        finally:
            await client.aclose()

    # ---------- 成功 / 失败收尾 ----------

    async def _find_existing(self, client, cfg: dict) -> dict | None:
        """查该账号该 shape 的存量实例（非终止态即算存在）。"""
        items = await client.list_instances(cfg["compartment"])
        for inst in items:
            if inst.get("shape") == cfg["shape"] and inst.get("lifecycleState") in ACTIVE_STATES:
                return inst
        return None

    async def _on_success(self, task_id: int, cfg: dict, client, instance_ocid: str, note: str,
                          display_name: str = "") -> tuple[bool, int]:
        """抢到一台：success_count+1、OCID 追加 → 若达目标则 CAS 置 success 结束，
        否则继续循环。返回 (done, new_count)：done 为 True 表示任务已完成（调用方 break）。
        每台都发 TG（含"第 X/Y 台"），最后加 CF 自动同步。"""
        target = cfg.get("target_count", 1) or 1
        # 原子更新：success_count+1，instance_ocid 逗号追加
        db = SessionLocal()
        try:
            task = db.get(SnipeTask, task_id)
            if not task or task.status != "running":
                self._log(task_id, "warning", "状态已非 running（可能被暂停），放弃后处理")
                return True, 0  # 当作结束，调用方退出循环
            new_count = (task.success_count or 0) + 1
            task.success_count = new_count
            old_ocids = (task.instance_ocid or "").strip()
            task.instance_ocid = (old_ocids + "," + instance_ocid).strip(",") if old_ocids else instance_ocid
            task.last_error = ""
            done = new_count >= target
            if done:
                task.status = "success"
                task.finished_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()
        self._log(task_id, "info", "%s：第 %d/%d 台，实例 OCID %s" % (note, new_count, target, instance_ocid))
        # 抢机成功是后台事件，走手动审计（非 HTTP 请求，中间件覆盖不到）
        db3 = SessionLocal()
        try:
            log_operation(
                db3, "snipe.success", account_id=cfg.get("account_id"),
                detail="抢机成功（第 %d/%d 台）：%s 在 %s 抢到 %s，实例 %s"
                % (new_count, target, cfg["account_name"], cfg["region"], cfg["shape"], instance_ocid),
                operator="sniper",
            )
        finally:
            db3.close()
        # 新实例加入列表，失效实例缓存
        invalidate_instance_cache()
        # 新实例公网 IP 需要一点时间分配，最多等约 2 分钟
        ip = None
        for _ in range(12):
            try:
                ip = await self._get_public_ip(client, cfg["compartment"], instance_ocid)
            except Exception as e:
                logger.warning("task=%s 查新实例 IP 失败：%s", task_id, e)
            if ip:
                break
            await self._sleep(task_id, 10)
        # 开机成功通知：带机器信息、公网 IP 和 root 密码（纯文本发送，无转义问题）
        # 多台时注明"第 X/Y 台"，最后一台额外注明任务完成
        count_tag = "（第 %d/%d 台%s）" % (new_count, target, "，任务完成" if done else "")
        self._log(task_id, "info", "发送 TG 开机通知%s…" % count_tag)
        try:
            tg_ok = await telegram.send_message(
            "🚀 ————开机成功通知———— 🚀%s\n"
            "账号: %s\n"
            "区域: %s\n"
            "实例: %s (%s)\n"
            "Shape: %s (%sC/%sG)\n"
            "公网 IP: %s\n"
            "用户: root\n"
            "密码: %s"
            % (count_tag, cfg["account_name"], cfg["region"], display_name or cfg["display_name"], instance_ocid,
               cfg["shape"], cfg["ocpus"], cfg["memory_gb"],
               ip or "获取中", cfg["root_password"] or "未设置")
            )
            self._log(task_id, "info" if tg_ok else "warning",
                      "TG 开机通知%s" % ("发送成功" if tg_ok else "发送失败（检查系统设置里的 TG 配置）"))
        except Exception as e:
            self._log(task_id, "warning", "TG 开机通知异常：%s" % str(e)[:120])
        if not ip:
            self._log(task_id, "warning", "未获取到新实例公网 IP，跳过 CF 自动同步（可在网络页手动同步）")
            return done, new_count
        db2 = SessionLocal()
        try:
            results = await sync_instance_domains(db2, instance_ocid, ip)
        except Exception as e:
            self._log(task_id, "warning", "CF 自动同步异常：%s" % str(e)[:160])
            return done, new_count
        finally:
            db2.close()
        ok_n = sum(1 for r in results if r.get("ok"))
        self._log(task_id, "info", "CF 自动同步：%d/%d 个域名成功" % (ok_n, len(results)))
        return done, new_count

    async def _get_public_ip(self, client, compartment: str, instance_ocid: str) -> str | None:
        atts = await client.list_vnic_attachments(compartment, instance_ocid)
        if not atts:
            return None
        vnic = (await client.get_vnic(atts[0]["vnicId"])).json()
        return vnic.get("publicIp")

    async def _finish_failed(self, task_id: int, cfg: dict, msg: str):
        db = SessionLocal()
        try:
            _cas_status(db, task_id, {"running"}, "failed",
                        last_error=msg[:2000], finished_at=datetime.utcnow())
        finally:
            db.close()
        self._log(task_id, "error", "任务失败停止：" + msg)
        await telegram.send_message(
            "【抢机失败】账号 %s %s @ %s：%s" % (cfg["account_name"], cfg["shape"], cfg["region"], msg)
        )

    def _mark_account_key_invalid(self, cfg: dict):
        """401 时把账号状态置为密钥失效，避免其他任务继续空转。"""
        db = SessionLocal()
        try:
            account = db.get(Account, cfg["account_id"])
            if account and account.status != "key_invalid":
                account.status = "key_invalid"
                account.updated_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
        logger.warning("账号 %s 密钥失效，已置 key_invalid", cfg["account_name"])

    # ---------- 日志清理 ----------

    async def _cleanup_loop(self):
        """每小时清理一次过期抢机日志，只保留近 N 天。"""
        while True:
            try:
                await asyncio.sleep(3600)
                cutoff = datetime.utcnow() - timedelta(days=int(get_setting("SNIPE_LOG_RETENTION_DAYS")))
                db = SessionLocal()
                try:
                    n = db.query(SnipeLog).filter(SnipeLog.created_at < cutoff).delete(synchronize_session=False)
                    db.commit()
                    if n:
                        logger.info("清理过期抢机日志 %d 条", n)
                finally:
                    db.close()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("抢机日志清理异常")


# 模块级单例：api 层与 lifespan 共用
sniper_manager = SniperManager()

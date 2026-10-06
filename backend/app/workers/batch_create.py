"""批量创建实例引擎：先选配置、再批量创建。

复用 sniper.py 的 classify_launch_error 与 AccountRateLimiter，
错误分类与退避策略保持一致（E5 同样可能 Out of capacity）。

retry_mode：
- direct：每个 item 只尝试一次，失败即记 failed；
- retry：每个 item 独立循环，按错误分类重试（no_capacity 抖动重试、
  429 指数退避、400/401 停该 item），直到成功或任务被取消。

并发模型：BatchCreateManager 常驻 api 进程（lifespan 启动/停止）；
每个 task 一个 asyncio worker，task 内用信号量（默认 4 并发）并行处理
items；单账号限流器与抢机引擎共用一套策略（各自独立实例，互不干扰）；
支持取消：cancel 把未完成的 item 置 cancelled。
"""
import asyncio
import logging
import random
from datetime import datetime

import httpx
import secrets
from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from app.core import telegram
from app.core.cloud_init import build_root_password_script
from app.core.audit import log_operation
from app.core.deps import SessionLocal
from app.core.oci_factory import build_client_for_account, compartment_of
from app.models.models import Account, BatchCreateItem, BatchCreateTask
from app.services.instances import invalidate_instance_cache
from app.services.network_ensure import ensure_subnet
from app.workers.sniper import AccountRateLimiter, classify_launch_error

logger = logging.getLogger(__name__)

# 单任务内最大并发 item 数：太高容易触发账号限流，4 是个保守值
TASK_CONCURRENCY = 4
# retry 模式安全阀：单个 item 最多尝试次数，避免无限循环烧配额
MAX_ATTEMPTS_RETRY = 1000

# 任务终态：done 全部跑完 / cancelled 手动取消
TASK_RUNNING = "running"


def _cas_task_status(db, task_id: int, expected: set, new_status: str, **extra) -> bool:
    """CAS 更新任务状态，防止并发 API 重复操作。返回是否更新成功。"""
    rows = (
        db.query(BatchCreateTask)
        .filter(BatchCreateTask.id == task_id, BatchCreateTask.status.in_(expected))
        .update({"status": new_status, "updated_at": datetime.utcnow(), **extra}, synchronize_session=False)
    )
    db.commit()
    return rows == 1


def _set_item(db, item_id: int, status: str, **extra):
    """更新单个 item 状态（各协程用独立短会话调用）。"""
    db.query(BatchCreateItem).filter(BatchCreateItem.id == item_id).update(
        {"status": status, "updated_at": datetime.utcnow(), **extra}, synchronize_session=False
    )
    db.commit()


class BatchCreateManager:
    """管理所有批量创建任务的 asyncio worker。常驻 api 进程，由 lifespan 启动/停止。"""

    def __init__(self):
        self._workers: dict[int, asyncio.Task] = {}
        self._stop_events: dict[int, asyncio.Event] = {}
        self._limiters = AccountRateLimiter()
        self._started = False

    # ---------- 生命周期 ----------

    async def start(self):
        """启动引擎：恢复 DB 中 running 的任务（服务重启场景）。"""
        if self._started:
            return
        self._started = True
        db = SessionLocal()
        try:
            task_ids = [t.id for t in db.query(BatchCreateTask).filter(BatchCreateTask.status == TASK_RUNNING).all()]
        finally:
            db.close()
        for tid in task_ids:
            await self._spawn(tid)
        logger.info("批量创建引擎启动，恢复 %d 个 running 任务", len(task_ids))

    async def stop(self):
        for ev in self._stop_events.values():
            ev.set()
        for t in list(self._workers.values()):
            t.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._stop_events.clear()
        self._started = False
        logger.info("批量创建引擎已停止")

    # ---------- 任务控制（API 层调用） ----------

    async def launch_task(self, task_id: int) -> bool:
        """CAS pending → running，并启动 worker。"""
        db = SessionLocal()
        try:
            ok = _cas_task_status(db, task_id, {"pending"}, TASK_RUNNING, started_at=datetime.utcnow())
        finally:
            db.close()
        if ok:
            await self._spawn(task_id)
        return ok

    async def cancel_task(self, task_id: int) -> bool:
        """CAS running → cancelled：唤醒 worker，未开始的 item 直接置 cancelled。"""
        db = SessionLocal()
        try:
            ok = _cas_task_status(db, task_id, {TASK_RUNNING}, "cancelled")
            if ok:
                # 未开始的 item 直接取消；running 中的由 worker 自行收尾
                db.query(BatchCreateItem).filter(
                    BatchCreateItem.task_id == task_id,
                    BatchCreateItem.status == "pending",
                ).update({"status": "cancelled", "updated_at": datetime.utcnow()},
                           synchronize_session=False)
                db.commit()
        finally:
            db.close()
        if ok:
            ev = self._stop_events.get(task_id)
            if ev:
                ev.set()
        return ok

    async def _spawn(self, task_id: int):
        if task_id in self._workers and not self._workers[task_id].done():
            return
        self._stop_events[task_id] = asyncio.Event()
        self._workers[task_id] = asyncio.create_task(self._worker(task_id))

    def _stopped(self, task_id: int) -> bool:
        ev = self._stop_events.get(task_id)
        return bool(ev and ev.is_set())

    async def _worker(self, task_id: int):
        try:
            await self._execute(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("批量创建 worker 异常退出 task=%s", task_id)
            db = SessionLocal()
            try:
                _cas_task_status(db, task_id, {TASK_RUNNING}, "done",
                                 finished_at=datetime.utcnow())
            finally:
                db.close()
        finally:
            self._workers.pop(task_id, None)
            self._stop_events.pop(task_id, None)

    # ---------- 任务执行 ----------

    async def _execute(self, task_id: int):
        db = SessionLocal()
        try:
            task = db.get(BatchCreateTask, task_id)
            if not task:
                return
            item_ids = [
                i.id for i in db.query(BatchCreateItem)
                .filter(BatchCreateItem.task_id == task_id, BatchCreateItem.status == "pending")
                .order_by(BatchCreateItem.id).all()
            ]
            shape, ocpus, memory_gb = task.shape, task.ocpus, task.memory_gb
            retry_mode, name = task.retry_mode, task.name or ("任务#%d" % task_id)
        finally:
            db.close()

        sem = asyncio.Semaphore(TASK_CONCURRENCY)
        coros = []
        for item_id in item_ids:
            if self._stopped(task_id):
                # 取消竞态：还没启动的 item 直接置 cancelled
                db2 = SessionLocal()
                try:
                    _set_item(db2, item_id, "cancelled", last_error="任务已取消")
                finally:
                    db2.close()
                continue
            coros.append(self._process_item(task_id, item_id, shape, ocpus, memory_gb, retry_mode, sem))
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
        await self._finalize(task_id, name)

    async def _process_item(self, task_id: int, item_id: int, shape: str,
                            ocpus: float, memory_gb: float, retry_mode: str,
                            sem: asyncio.Semaphore):
        async with sem:
            if self._stopped(task_id):
                db = SessionLocal()
                try:
                    _set_item(db, item_id, "cancelled", last_error="任务已取消")
                finally:
                    db.close()
                return
            # 快照 item 配置 + 账号（joinedload 预加载 proxy，避免 DetachedInstanceError）
            db = SessionLocal()
            try:
                item = db.get(BatchCreateItem, item_id)
                if not item or item.status != "pending":
                    return
                account = (
                    db.query(Account).options(joinedload(Account.proxy))
                    .filter(Account.id == item.account_id).first()
                )
                if not account:
                    _set_item(db, item_id, "failed", last_error="账号不存在")
                    return
                cfg = {
                    "account_id": account.id,
                    "account_name": account.name,
                    "region": item.region,
                    "shape": shape,
                    "ocpus": ocpus,
                    "memory_gb": memory_gb,
                    "image_ocid": item.image_ocid,
                    "subnet_ocid": item.subnet_ocid,
                    "ad": item.availability_domain,
                    "display_name": item.display_name,
                    "root_password": item.root_password or "",
                    "compartment": compartment_of(account),
                }
                try:
                    client = build_client_for_account(account)
                except ValueError as e:
                    _set_item(db, item_id, "failed", last_error="客户端构建失败：%s" % e)
                    return
                # 子网留空则自动建网（OCI-Start 思路）：有则复用、无则创建
                if not cfg["subnet_ocid"]:
                    try:
                        cfg["subnet_ocid"] = await ensure_subnet(account, cfg["region"], cfg["ad"])
                    except Exception as e:
                        detail = e.detail if isinstance(e, HTTPException) else str(e)
                        _set_item(db, item_id, "failed",
                                  last_error="自动建网失败：%s" % detail[:500])
                        return
                    # 写回 item，方便查看实际用的子网
                    it = db.get(BatchCreateItem, item_id)
                    if it:
                        it.subnet_ocid = cfg["subnet_ocid"]
                # root 密码：没填则每台独立生成随机密码
                if not cfg["root_password"]:
                    cfg["root_password"] = secrets.token_urlsafe(12)
                    it = db.get(BatchCreateItem, item_id)
                    if it:
                        it.root_password = cfg["root_password"]
                cfg["user_data"] = build_root_password_script(cfg["root_password"])
                _set_item(db, item_id, "running")
            finally:
                db.close()
            try:
                if retry_mode == "retry":
                    await self._run_retry(task_id, item_id, cfg, client)
                else:
                    await self._run_direct(task_id, item_id, cfg, client)
            finally:
                await client.aclose()

    async def _attempt_once(self, client, cfg: dict) -> tuple[str, str, str]:
        """单次创建尝试。返回 (kind, 中文说明, instance_ocid)。"""
        try:
            resp = await client.launch_instance(
                compartment_id=cfg["compartment"],
                availability_domain=cfg["ad"],
                shape=cfg["shape"],
                ocpus=cfg["ocpus"],
                memory_gb=cfg["memory_gb"],
                image_ocid=cfg["image_ocid"],
                subnet_ocid=cfg["subnet_ocid"],
                display_name=cfg["display_name"],
                user_data=cfg.get("user_data", ""),
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ProxyError) as e:
            return "network", "网络异常（%s）" % type(e).__name__, ""
        except Exception as e:
            return "network", "请求异常：%s" % str(e)[:160], ""
        kind, msg = classify_launch_error(resp.status_code, resp.text)
        ocid = ""
        if kind == "success":
            try:
                ocid = (resp.json() or {}).get("id", "")
            except Exception:
                pass
        return kind, msg, ocid

    def _bump_attempts(self, item_id: int):
        db = SessionLocal()
        try:
            db.query(BatchCreateItem).filter(BatchCreateItem.id == item_id).update(
                {BatchCreateItem.attempts: BatchCreateItem.attempts + 1,
                 BatchCreateItem.updated_at: datetime.utcnow()},
                synchronize_session=False,
            )
            db.commit()
        finally:
            db.close()

    def _finish_item(self, item_id: int, status: str, instance_ocid: str = "",
                     last_error: str = "", public_ip: str = ""):
        db = SessionLocal()
        try:
            _set_item(db, item_id, status, instance_ocid=instance_ocid,
                      last_error=last_error[:2000], public_ip=public_ip)
        finally:
            db.close()

    async def _get_public_ip(self, client, compartment: str, instance_ocid: str) -> str | None:
        """查实例公网 IP（最多等约 1 分钟，拿不到返回 None 不阻塞）。"""
        for _ in range(6):
            try:
                atts = await client.list_vnic_attachments(compartment, instance_ocid)
                if atts:
                    vnic = (await client.get_vnic(atts[0]["vnicId"])).json()
                    if vnic.get("publicIp"):
                        return vnic.get("publicIp")
            except Exception as e:
                logger.warning("item 查公网 IP 失败：%s", str(e)[:120])
            await asyncio.sleep(10)
        return None

    async def _run_direct(self, task_id: int, item_id: int, cfg: dict, client):
        """direct 模式：单次尝试，失败即记 failed。"""
        await self._limiters.acquire(cfg["account_id"])
        self._bump_attempts(item_id)
        kind, msg, ocid = await self._attempt_once(client, cfg)
        if kind == "success":
            ip = await self._get_public_ip(client, cfg["compartment"], ocid)
            self._finish_item(item_id, "success", instance_ocid=ocid, public_ip=ip or "")
            await telegram.send_message(
                "🚀 ————开机成功通知———— 🚀\n"
                "账号: %s\n"
                "区域: %s\n"
                "实例: %s (%s)\n"
                "Shape: %s (%sC/%sG)\n"
                "公网 IP: %s\n"
                "用户: root\n"
                "密码: %s"
                % (cfg["account_name"], cfg["region"], cfg["display_name"], ocid,
                   cfg["shape"], cfg["ocpus"], cfg["memory_gb"],
                   ip or "获取中", cfg["root_password"] or "未设置")
            )
            logger.info("批量创建成功：%s @ 账号 %s", cfg["display_name"], cfg["account_name"])
            return
        if kind == "auth_error":
            self._mark_account_key_invalid(cfg)
        self._finish_item(item_id, "failed", last_error=msg)

    async def _run_retry(self, task_id: int, item_id: int, cfg: dict, client):
        """retry 模式：按错误分类重试，直到成功或任务被取消。"""
        unknown_streak = 0
        backoff_429 = 30.0
        attempts = 0
        while not self._stopped(task_id):
            if attempts >= MAX_ATTEMPTS_RETRY:
                self._finish_item(item_id, "failed", last_error="达到最大尝试次数（%d），停止" % MAX_ATTEMPTS_RETRY)
                return
            await self._limiters.acquire(cfg["account_id"])
            self._bump_attempts(item_id)
            attempts += 1
            kind, msg, ocid = await self._attempt_once(client, cfg)
            if kind == "success":
                ip = await self._get_public_ip(client, cfg["compartment"], ocid)
                self._finish_item(item_id, "success", instance_ocid=ocid, public_ip=ip or "")
                await telegram.send_message(
                    "🚀 ————开机成功通知———— 🚀\n"
                    "账号: %s\n"
                    "区域: %s\n"
                    "实例: %s (%s)\n"
                    "Shape: %s (%sC/%sG)\n"
                    "公网 IP: %s\n"
                    "用户: root\n"
                    "密码: %s"
                    % (cfg["account_name"], cfg["region"], cfg["display_name"], ocid,
                       cfg["shape"], cfg["ocpus"], cfg["memory_gb"],
                       ip or "获取中", cfg["root_password"] or "未设置")
                )
                logger.info("批量创建成功：%s @ 账号 %s（第 %d 次尝试）",
                            cfg["display_name"], cfg["account_name"], attempts)
                return
            if kind == "no_capacity":
                unknown_streak = 0
                await self._sleep(task_id, random.uniform(3, 8))
                continue
            if kind == "rate_limited":
                unknown_streak = 0
                self._limiters.on_rate_limited(cfg["account_id"])
                delay = min(backoff_429, 600) + random.uniform(0, 5)
                backoff_429 *= 2
                await self._sleep(task_id, delay)
                continue
            if kind == "config_error":
                self._finish_item(item_id, "failed", last_error=msg)
                return
            if kind == "auth_error":
                self._mark_account_key_invalid(cfg)
                self._finish_item(item_id, "failed", last_error=msg)
                return
            # network / unknown：短退避重试
            unknown_streak += 1
            if unknown_streak >= 10:
                self._finish_item(item_id, "failed", last_error="连续 10 次异常，最后一次：" + msg)
                return
            await self._sleep(task_id, min(5 * unknown_streak, 60) + random.uniform(0, 3))
        # 循环因取消而退出
        self._finish_item(item_id, "cancelled", last_error="任务已取消")

    async def _sleep(self, task_id: int, seconds: float):
        """可中断的 sleep：cancel 时立刻唤醒。"""
        ev = self._stop_events.get(task_id)
        if ev is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(ev.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _mark_account_key_invalid(self, cfg: dict):
        """401 时把账号状态置为密钥失效，避免其他 item 继续空转。"""
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

    # ---------- 收尾 ----------

    async def _finalize(self, task_id: int, name: str):
        """统计计数、任务置终态、TG 汇总推送。"""
        db = SessionLocal()
        try:
            task = db.get(BatchCreateTask, task_id)
            if not task:
                return
            items = db.query(BatchCreateItem).filter(BatchCreateItem.task_id == task_id).all()
            # 兜底：还有 pending/running 的（取消竞态）一律置 cancelled
            for it in items:
                if it.status in ("pending", "running"):
                    it.status = "cancelled"
                    it.updated_at = datetime.utcnow()
            success = sum(1 for i in items if i.status == "success")
            fail = sum(1 for i in items if i.status == "failed")
            cancel = sum(1 for i in items if i.status == "cancelled")
            task.success_count, task.fail_count, task.cancel_count = success, fail, cancel
            if task.status == TASK_RUNNING:
                task.status = "done"
            task.finished_at = datetime.utcnow()
            task.updated_at = datetime.utcnow()
            db.commit()
            final_status, total = task.status, task.total
        finally:
            db.close()
        done_text = "完成" if final_status == "done" else "已取消"
        # 后台任务完成，走手动审计（非 HTTP 请求，中间件覆盖不到）
        db4 = SessionLocal()
        try:
            log_operation(
                db4, "batch_create.done",
                detail="批量创建任务「%s」%s：成功 %d / 失败 %d / 取消 %d，共 %d 台"
                % (name, done_text, success, fail, cancel, total),
                operator="batch_create",
            )
        finally:
            db4.close()
        # 新实例加入列表，失效实例缓存
        invalidate_instance_cache()
        # 成功明细：每台一行 实例名 | 公网 IP | root 密码
        lines = []
        for i in items:
            if i.status == "success":
                lines.append("%s | %s | %s" % (
                    i.display_name or i.instance_ocid,
                    i.public_ip or "获取中",
                    i.root_password or "未设置",
                ))
        detail_text = ""
        if lines:
            detail_text = "\n" + "\n".join(lines[:20])
            if len(lines) > 20:
                detail_text += "\n…等共 %d 台" % len(lines)
        await telegram.send_message(
            "🚀 ————批量开机%s———— 🚀\n"
            "任务「%s」：成功 %d / 失败 %d / 取消 %d，共 %d 台%s"
            % (done_text, name, success, fail, cancel, total, detail_text)
        )
        logger.info("批量创建任务 #%d %s：成功 %d / 失败 %d / 取消 %d", task_id, done_text, success, fail, cancel)


# 模块级单例：api 层与 lifespan 共用
batch_create_manager = BatchCreateManager()

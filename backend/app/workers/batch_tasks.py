"""Celery 批量任务：并发执行实例开机 / 关机 / 重启 / 终止。

- Celery 任务是同步入口，内部用 asyncio.run 跑异步 OCI 调用；
- 每个实例独立建 OciClient（走该账号绑定的代理），逐个执行、逐个记结果；
- 进度实时写回 BatchTask（success_count / fail_count / detail_json），前端轮询即可；
- 全部完成后 TG 推送汇总；所有写操作记审计日志。
"""
import asyncio
import json
import logging

from app.core import telegram
from app.core.audit import log_operation
from app.core.deps import SessionLocal
from app.core.oci_factory import build_client_for_account
from app.models.models import Account, BatchTask
from app.services.instances import invalidate_instance_cache
from app.workers.celery_app import celery

logger = logging.getLogger(__name__)

# 批量任务类型 → OCI instance_action 的 action 取值
# （terminate 特殊，走 DELETE /20160918/instances/{id}）
ACTION_MAP = {"power_on": "START", "power_off": "STOP", "reboot": "RESET"}
SUCCESS_CODES = {200, 201, 202, 204}


async def _run_async(task_id: int):
    db = SessionLocal()
    try:
        task = db.get(BatchTask, task_id)
        if not task:
            logger.error("批量任务 #%d 不存在", task_id)
            return
        task.status = "running"
        db.commit()

        payload = json.loads(task.detail_json or "{}")
        items = payload.get("items", [])
        results = []

        for it in items:
            account = db.get(Account, it.get("account_id"))
            if not account:
                results.append({**it, "ok": False, "error": "账号不存在（可能已被删除）"})
                task.fail_count += 1
                continue
            client = build_client_for_account(account)
            try:
                if task.task_type == "terminate":
                    resp = await client.terminate_instance(it["instance_id"])
                else:
                    resp = await client.instance_action(it["instance_id"], ACTION_MAP[task.task_type])
                ok = resp.status_code in SUCCESS_CODES
                err = "" if ok else f"HTTP {resp.status_code} {resp.text[:200]}"
                results.append({**it, "ok": ok, "error": err, "status_code": resp.status_code})
                if ok:
                    task.success_count += 1
                else:
                    task.fail_count += 1
            except Exception as e:
                logger.exception("批量任务 #%d 实例 %s 执行异常", task_id, it.get("instance_id"))
                results.append({**it, "ok": False, "error": str(e)[:200]})
                task.fail_count += 1
            finally:
                await client.aclose()
            # 每完成一台就写回进度，前端轮询可见实时进度
            payload["results"] = results
            task.detail_json = json.dumps(payload, ensure_ascii=False)
            db.commit()

        task.status = "done"
        db.commit()
        log_operation(
            db, "batch.done",
            detail=f"批量任务 #{task.id}（{task.task_type}）完成：成功 {task.success_count} / 失败 {task.fail_count}",
        )
        # 批量操作后失效实例列表缓存
        invalidate_instance_cache()
        await telegram.send_message(
            f"【批量任务完成】{task.name}\n成功 {task.success_count} 台，失败 {task.fail_count} 台"
        )
    except Exception:
        logger.exception("批量任务 #%d 整体异常", task_id)
        try:
            task = db.get(BatchTask, task_id)
            if task:
                task.status = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@celery.task(name="batch.run", bind=True)
def run_batch_task(self, batch_task_id: int):
    """Celery 任务入口（同步包装，内部跑 asyncio）。"""
    asyncio.run(_run_async(batch_task_id))

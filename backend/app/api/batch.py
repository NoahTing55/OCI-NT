"""批量开机 / 关机 / 重启 / 终止。

流程：
1. 前端多选实例（可跨账号）→ POST /api/batch 创建 BatchTask
   （task_type + 实例列表存入 detail_json，状态 pending）；
2. 投递 Celery 任务并发执行：每个实例调自研 OciClient，
   action 取值 START / STOP / RESET，终止走 DELETE /20160918/instances/{id}；
3. 前端轮询 GET /api/batch/{id} 看 success_count / fail_count / 明细；
4. 终止（terminate）是不可逆操作，必须 confirm=true 二次确认。
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.models import BatchTask
from app.schemas.schemas import BatchCreateIn, BatchTaskOut

router = APIRouter()

TASK_TYPES = {"power_on", "power_off", "reboot", "terminate"}
TASK_TYPE_TEXT = {
    "power_on": "批量开机",
    "power_off": "批量关机",
    "reboot": "批量重启",
    "terminate": "批量终止",
}

def _to_out(task: BatchTask) -> BatchTaskOut:
    return BatchTaskOut(
        id=task.id,
        name=task.name,
        task_type=task.task_type,
        status=task.status,
        total=task.total,
        success_count=task.success_count,
        fail_count=task.fail_count,
        detail=json.loads(task.detail_json or "{}"),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )

@router.post("", response_model=BatchTaskOut)
def create_batch(data: BatchCreateIn, db: Session = Depends(get_db)):
    if data.task_type not in TASK_TYPES:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{data.task_type}")
    if not data.items:
        raise HTTPException(status_code=400, detail="实例列表为空")
    if data.task_type == "terminate" and not data.confirm:
        raise HTTPException(status_code=400, detail="终止实例为不可逆操作，请二次确认（confirm=true）后再提交")

    task = BatchTask(
        name=data.name or f"{TASK_TYPE_TEXT[data.task_type]}（{len(data.items)} 台）",
        task_type=data.task_type,
        status="pending",
        total=len(data.items),
        detail_json=json.dumps(
            {"items": [i.model_dump() for i in data.items], "results": []}, ensure_ascii=False
        ),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    # 局部导入：避免 celery_app → batch_tasks → api 之间的循环导入
    from app.workers.batch_tasks import run_batch_task

    try:
        run_batch_task.delay(task.id)
    except Exception as e:
        # Redis/Celery 不可用时：任务已入库但无法投递，标记失败并给明确提示
        task.status = "failed"
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"任务已创建但投递到 Celery 失败（Redis 不可用？）：{e}",
        )
    return _to_out(task)

@router.get("", response_model=list[BatchTaskOut])
def list_batch_tasks(db: Session = Depends(get_db)):
    return [_to_out(t) for t in db.query(BatchTask).order_by(BatchTask.id.desc()).limit(50).all()]

@router.get("/{task_id}", response_model=BatchTaskOut)
def get_batch_task(task_id: int, db: Session = Depends(get_db)):
    """进度查询：前端轮询此接口直到 status 为 done / failed。"""
    task = db.get(BatchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _to_out(task)

"""抢机任务 API。

- POST /：新建任务（同一账号同一 shape 同时只允许一个 running/paused 任务）；
- POST /{id}/start：CAS 置 running 并启动 worker；
- POST /{id}/pause：CAS 置 paused 并唤醒 worker；
- DELETE /{id}：running 任务需先暂停才能删除；
- GET /{id}/logs：分页查任务日志（前端轮询）；
- GET /templates：内置场景模板（免费 AMD 1C1G / ARM 1C6G / ARM 2C12G / E5 1C6G），前端一键填入表单。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.models import Account, SnipeLog, SnipeTask
from app.schemas.schemas import SnipeLogOut, SnipeTaskCreate, SnipeTaskOut, SnipeTaskUpdate
from app.workers.sniper import sniper_manager

router = APIRouter()

# 内置场景模板：前端"一键填入"用。不含区域/密钥/OCID，只有公开的 shape 配置。
# 顺序固定：免费 AMD 1C1G → ARM 1C6G → ARM 2C12G → E5 1C6G。区域跟随所选账号。
# 内置抢机模板（顺序固定，前端卡片按此顺序展示）
TEMPLATES = [
    {"name": "免费 AMD 1C1G",
     "shape": "VM.Standard.E2.1.Micro", "ocpus": 1, "memory_gb": 1},
    {"name": "ARM 1C6G",
     "shape": "VM.Standard.A1.Flex", "ocpus": 1, "memory_gb": 6},
    {"name": "ARM 2C12G",
     "shape": "VM.Standard.A1.Flex", "ocpus": 2, "memory_gb": 12},
    {"name": "E5 1C6G",
     "shape": "VM.Standard.E5.Flex", "ocpus": 1, "memory_gb": 6},
]

STATUS_TEXT = {
    "pending": "待启动", "running": "抢机中", "paused": "已暂停",
    "success": "已完成", "stopped": "已停止", "failed": "失败",
}

def _to_out(task: SnipeTask, account_name: str = "") -> SnipeTaskOut:
    return SnipeTaskOut(
        id=task.id,
        account_id=task.account_id,
        account_name=account_name,
        region=task.region,
        shape=task.shape,
        ocpus=task.ocpus,
        memory_gb=task.memory_gb,
        image_ocid=task.image_ocid,
        subnet_ocid=task.subnet_ocid,
        availability_domain=task.availability_domain,
        display_name=task.display_name or "",
        status=task.status,
        attempts=task.attempts,
        last_error=task.last_error or "",
        instance_ocid=task.instance_ocid or "",
        target_count=task.target_count or 1,
        success_count=task.success_count or 0,
        interval_seconds=task.interval_seconds or 60,
        open_all_ports=task.open_all_ports if task.open_all_ports is not None else True,
        boot_volume_gb=task.boot_volume_gb or 50,
        root_password=task.root_password or "",
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )

def _get_task(db: Session, task_id: int) -> SnipeTask:
    task = db.get(SnipeTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="抢机任务不存在")
    return task

@router.get("/templates")
def list_templates():
    """内置场景模板。"""
    return TEMPLATES

@router.post("", response_model=SnipeTaskOut)
def create_task(data: SnipeTaskCreate, db: Session = Depends(get_db)):
    account = db.get(Account, data.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    for field in ("image_ocid", "availability_domain"):
        if not getattr(data, field):
            raise HTTPException(status_code=400, detail="镜像 / 可用域不能为空（配错会导致 400 空转）；子网可留空，任务启动时自动建网")
    # 同一账号同一 shape 同时只允许一个 running/paused 任务
    dup = (
        db.query(SnipeTask)
        .filter(
            SnipeTask.account_id == data.account_id,
            SnipeTask.shape == data.shape,
            SnipeTask.status.in_(["running", "paused"]),
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=400,
            detail="该账号该 shape 已有进行中的抢机任务（#%d，状态 %s），请先暂停或删除" % (dup.id, STATUS_TEXT.get(dup.status, dup.status)),
        )
    task = SnipeTask(
        account_id=data.account_id,
        region=data.region,
        shape=data.shape,
        ocpus=data.ocpus,
        memory_gb=data.memory_gb,
        image_ocid=data.image_ocid,
        subnet_ocid=data.subnet_ocid,
        availability_domain=data.availability_domain,
        display_name=data.display_name or "",
        root_password=data.root_password or "",
        target_count=data.target_count,
        interval_seconds=data.interval_seconds,
        open_all_ports=data.open_all_ports,
        boot_volume_gb=data.boot_volume_gb,
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _to_out(task, account.name)

@router.get("", response_model=list[SnipeTaskOut])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(SnipeTask).order_by(SnipeTask.id.desc()).limit(100).all()
    names = {a.id: a.name for a in db.query(Account).all()}
    return [_to_out(t, names.get(t.account_id, "")) for t in tasks]

@router.get("/{task_id}", response_model=SnipeTaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    account = db.get(Account, task.account_id)
    return _to_out(task, account.name if account else "")

@router.put("/{task_id}", response_model=SnipeTaskOut)
def update_task(task_id: int, data: SnipeTaskUpdate, db: Session = Depends(get_db)):
    """编辑抢机任务：只有 pending/paused/stopped/failed 状态可编辑。

    只更新传入的非 None 字段；attempts 与 status 保持不变。
    """
    task = _get_task(db, task_id)
    if task.status in ("running", "success"):
        raise HTTPException(status_code=400, detail="任务运行中或已完成，不可编辑")
    # 只更新传入的非 None 字段
    updates = data.model_dump(exclude_none=True)
    new_account_id = updates.get("account_id", task.account_id)
    new_shape = updates.get("shape", task.shape)
    # 账号存在性校验（如果改了账号）
    if "account_id" in updates:
        account = db.get(Account, updates["account_id"])
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
    # 同一账号同一 shape 唯一性校验（排除自己）
    if "account_id" in updates or "shape" in updates:
        dup = (
            db.query(SnipeTask)
            .filter(
                SnipeTask.id != task.id,
                SnipeTask.account_id == new_account_id,
                SnipeTask.shape == new_shape,
                SnipeTask.status.in_(["running", "paused"]),
            )
            .first()
        )
        if dup:
            raise HTTPException(
                status_code=400,
                detail="该账号该 shape 已有进行中的抢机任务（#%d，状态 %s），请先暂停或删除" % (dup.id, STATUS_TEXT.get(dup.status, dup.status)),
            )
    for field, value in updates.items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    account = db.get(Account, task.account_id)
    return _to_out(task, account.name if account else "")

@router.post("/{task_id}/start")
async def start_task(task_id: int, db: Session = Depends(get_db)):
    """启动任务：CAS 置 running 并启动 worker。"""
    _get_task(db, task_id)
    ok = await sniper_manager.start_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务当前状态不允许启动（可能已在运行）")
    task = db.get(SnipeTask, task_id)
    return {"ok": True, "task_id": task_id, "status": "running"}

@router.post("/{task_id}/pause")
async def pause_task(task_id: int, db: Session = Depends(get_db)):
    """暂停任务：CAS 置 paused，worker 尽快退出。"""
    task = _get_task(db, task_id)
    ok = await sniper_manager.pause_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="只有抢机中的任务才能暂停")
    return {"ok": True, "task_id": task_id, "status": "paused"}

@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在抢机中，请先暂停再删除")
    account_id = task.account_id
    db.query(SnipeLog).filter(SnipeLog.task_id == task_id).delete(synchronize_session=False)
    db.delete(task)
    db.commit()
    return {"ok": True}

@router.get("/{task_id}/logs")
def list_logs(task_id: int, level: str = "", page: int = 1, size: int = 100,
              db: Session = Depends(get_db)):
    """分页查任务日志（前端轮询刷新）。page 从 1 开始，size 最大 500。"""
    _get_task(db, task_id)
    size = max(1, min(size, 500))
    q = db.query(SnipeLog).filter(SnipeLog.task_id == task_id)
    if level in ("info", "warning", "error"):
        q = q.filter(SnipeLog.level == level)
    total = q.count()
    rows = q.order_by(SnipeLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            SnipeLogOut(
                id=r.id, task_id=r.task_id, level=r.level,
                message=r.message, created_at=r.created_at,
            )
            for r in rows
        ],
    }

"""抢机任务 API。

- POST /：新建任务（同一账号同一 shape 同时只允许一个 running/paused 任务）；
- POST /{id}/start：CAS 置 running 并启动 worker；
- POST /{id}/pause：CAS 置 paused 并唤醒 worker；
- DELETE /{id}：running 任务需先暂停才能删除；
- GET /{id}/logs：分页查任务日志（前端轮询）；
- GET /templates：内置场景模板（ARM 4C24G 等），前端一键填入表单。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.models import Account, SnipeLog, SnipeTask
from app.schemas.schemas import SnipeLogOut, SnipeTaskCreate, SnipeTaskOut
from app.workers.sniper import sniper_manager

router = APIRouter()

# 内置场景模板：前端"一键填入"用。不含任何密钥/OCID，只有公开的 shape 与区域信息。
# 分两类：ARM（A1 4C24G）、免费 AMD（E2.1.Micro 1C1G）。
TEMPLATES = [
    {"name": "ARM 4C24G（首尔）", "region": "ap-seoul-1",
     "shape": "VM.Standard.A1.Flex", "ocpus": 4, "memory_gb": 24},
    {"name": "ARM 4C24G（东京）", "region": "ap-tokyo-1",
     "shape": "VM.Standard.A1.Flex", "ocpus": 4, "memory_gb": 24},
    {"name": "ARM 4C24G（新加坡）", "region": "ap-singapore-1",
     "shape": "VM.Standard.A1.Flex", "ocpus": 4, "memory_gb": 24},
    {"name": "ARM 4C24G（凤凰城）", "region": "us-phoenix-1",
     "shape": "VM.Standard.A1.Flex", "ocpus": 4, "memory_gb": 24},
    {"name": "免费 AMD 1C1G（首尔）", "region": "ap-seoul-1",
     "shape": "VM.Standard.E2.1.Micro", "ocpus": 1, "memory_gb": 1},
    {"name": "免费 AMD 1C1G（东京）", "region": "ap-tokyo-1",
     "shape": "VM.Standard.E2.1.Micro", "ocpus": 1, "memory_gb": 1},
    {"name": "免费 AMD 1C1G（新加坡）", "region": "ap-singapore-1",
     "shape": "VM.Standard.E2.1.Micro", "ocpus": 1, "memory_gb": 1},
    {"name": "免费 AMD 1C1G（凤凰城）", "region": "us-phoenix-1",
     "shape": "VM.Standard.E2.1.Micro", "ocpus": 1, "memory_gb": 1},
]

STATUS_TEXT = {
    "pending": "待启动", "running": "抢机中", "paused": "已暂停",
    "success": "已抢到", "stopped": "已停止", "failed": "失败",
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
    for field in ("image_ocid", "subnet_ocid", "availability_domain"):
        if not getattr(data, field):
            raise HTTPException(status_code=400, detail="镜像 / 子网 / 可用域不能为空（配错会导致 400 空转）")
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

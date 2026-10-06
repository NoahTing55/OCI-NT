"""批量创建实例 API（先选配置、再批量创建）。

- POST /：新建任务（校验配置 + 生成 items，同一任务内 (account_id, display_name) 去重）；
- POST /{id}/launch：启动任务（CAS pending → running，引擎接管）；
- POST /{id}/cancel：取消任务（CAS running → cancelled）；
- GET /{id}：任务详情 + items 进度（前端轮询）；
- /templates：配置模板 CRUD，下次一键载入；
- /shape-presets：shape 预设下拉（公开信息，无密钥）。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.models import Account, BatchCreateItem, BatchCreateTask, BatchCreateTemplate
from app.schemas.schemas import (
    BatchCreateItemOut,
    BatchCreateTaskDetailOut,
    BatchCreateTaskIn,
    BatchCreateTaskOut,
    BatchCreateTemplateIn,
    BatchCreateTemplateOut,
)
from app.workers.batch_create import batch_create_manager

router = APIRouter()

# shape 预设：前端下拉用。不含任何密钥/OCID，只有公开的 shape 名称。
# 不做 shape 特判：OCPU/内存上下限不硬编码，配错靠 OCI 400 走 config_error 分类。
SHAPE_PRESETS = [
    {"shape": "VM.Standard.E5.Flex", "label": "E5 Flex（x86 通用，推荐）"},
    {"shape": "VM.Standard.A1.Flex", "label": "A1 Flex（ARM）"},
    {"shape": "VM.Standard.E2.1.Micro", "label": "E2 Micro（AMD 免费机）"},
    {"shape": "VM.Standard.E3.Flex", "label": "E3 Flex"},
    {"shape": "VM.Standard.E4.Flex", "label": "E4 Flex"},
]

RETRY_MODES = {"direct": "单次尝试", "retry": "失败重试"}

STATUS_TEXT = {
    "pending": "待启动", "running": "创建中", "done": "已完成", "cancelled": "已取消",
    "success": "成功", "failed": "失败",
}

def _to_item_out(item: BatchCreateItem, account_names: dict) -> BatchCreateItemOut:
    return BatchCreateItemOut(
        id=item.id,
        task_id=item.task_id,
        account_id=item.account_id,
        account_name=account_names.get(item.account_id, ""),
        region=item.region,
        display_name=item.display_name or "",
        status=item.status,
        instance_ocid=item.instance_ocid or "",
        attempts=item.attempts,
        last_error=item.last_error or "",
        created_at=item.created_at,
        updated_at=item.updated_at,
    )

def _to_task_out(task: BatchCreateTask) -> BatchCreateTaskOut:
    return BatchCreateTaskOut(
        id=task.id,
        name=task.name or "",
        shape=task.shape,
        ocpus=task.ocpus,
        memory_gb=task.memory_gb,
        count_per_account=task.count_per_account,
        name_prefix=task.name_prefix or "",
        retry_mode=task.retry_mode,
        status=task.status,
        total=task.total,
        success_count=task.success_count,
        fail_count=task.fail_count,
        cancel_count=task.cancel_count,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )

def _get_task(db: Session, task_id: int) -> BatchCreateTask:
    task = db.get(BatchCreateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="批量创建任务不存在")
    return task

@router.get("/shape-presets")
def list_shape_presets():
    """shape 预设下拉。"""
    return SHAPE_PRESETS

@router.post("", response_model=BatchCreateTaskDetailOut)
async def create_task(data: BatchCreateTaskIn, db: Session = Depends(get_db)):
    """新建批量创建任务：校验配置 → 生成 items → 自动启动引擎。"""
    if data.retry_mode not in RETRY_MODES:
        raise HTTPException(status_code=400, detail="retry_mode 只能是 direct（单次）或 retry（失败重试）")
    if not data.name_prefix.strip():
        raise HTTPException(status_code=400, detail="命名前缀不能为空")
    # 任务级默认 OCID 允许为空（前端第 1 步可跳过）；改为按账号校验：
    # 每个已选账号的有效值（本行覆盖或任务级默认）都不能为空。
    # 账号去重（保持原顺序）
    seen, account_rows = set(), []
    for row in data.accounts:
        if row.account_id in seen:
            continue
        seen.add(row.account_id)
        account_rows.append(row)
    if not account_rows:
        raise HTTPException(status_code=400, detail="至少选择一个账号")

    accounts = {}
    for row in account_rows:
        acc = db.get(Account, row.account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="账号 #%d 不存在" % row.account_id)
        accounts[row.account_id] = acc
    for row in account_rows:
        acc = accounts[row.account_id]
        for field, label in (("image_ocid", "镜像"), ("subnet_ocid", "子网"),
                             ("availability_domain", "可用域")):
            effective = (getattr(row, field, "") or "").strip() or (getattr(data, field, "") or "").strip()
            if not effective:
                raise HTTPException(
                    status_code=400,
                    detail="账号「%s」缺少%s（请在第 1 步填默认值，或在第 2 步该账号行单独填写）" % (acc.name, label))

    prefix = data.name_prefix.strip()
    task = BatchCreateTask(
        name=data.name or "批量创建 %s" % datetime.utcnow().strftime("%Y%m%d-%H%M"),
        shape=data.shape,
        ocpus=data.ocpus,
        memory_gb=data.memory_gb,
        count_per_account=data.count_per_account,
        name_prefix=prefix,
        retry_mode=data.retry_mode,
        image_ocid=data.image_ocid,
        subnet_ocid=data.subnet_ocid,
        availability_domain=data.availability_domain,
        status="pending",
    )
    db.add(task)
    db.flush()  # 拿到 task.id 再建 items

    items = []
    for row in account_rows:
        acc = accounts[row.account_id]
        region = row.region.strip() or acc.region
        image_ocid = row.image_ocid.strip() or data.image_ocid
        subnet_ocid = row.subnet_ocid.strip() or data.subnet_ocid
        ad = row.availability_domain.strip() or data.availability_domain
        for seq in range(1, data.count_per_account + 1):
            display_name = "%s-%02d" % (prefix, seq)
            items.append(BatchCreateItem(
                task_id=task.id,
                account_id=row.account_id,
                region=region,
                image_ocid=image_ocid,
                subnet_ocid=subnet_ocid,
                availability_domain=ad,
                display_name=display_name,
                status="pending",
            ))
    # 同一任务内 (account_id, display_name) 去重（防御性：正常流程不会重复）
    uniq, seen_names = [], set()
    for it in items:
        key = (it.account_id, it.display_name)
        if key not in seen_names:
            seen_names.add(key)
            uniq.append(it)
    db.add_all(uniq)
    task.total = len(uniq)
    db.commit()
    db.refresh(task)

    # 自动启动引擎（新建即跑，符合"选完配置就批量创建"的心智）
    ok = await batch_create_manager.launch_task(task.id)
    db.refresh(task)
    account_names = {a.id: a.name for a in accounts.values()}
    out = _to_task_out(task)
    return BatchCreateTaskDetailOut(**out.model_dump(), items=[_to_item_out(i, account_names) for i in uniq])

@router.get("", response_model=list[BatchCreateTaskOut])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(BatchCreateTask).order_by(BatchCreateTask.id.desc()).limit(100).all()
    return [_to_task_out(t) for t in tasks]

# ---------------- 配置模板 ----------------

@router.get("/templates", response_model=list[BatchCreateTemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.query(BatchCreateTemplate).order_by(BatchCreateTemplate.id.desc()).all()

@router.post("/templates", response_model=BatchCreateTemplateOut)
def create_template(data: BatchCreateTemplateIn, db: Session = Depends(get_db)):
    if data.retry_mode not in RETRY_MODES:
        raise HTTPException(status_code=400, detail="retry_mode 只能是 direct 或 retry")
    if db.query(BatchCreateTemplate).filter(BatchCreateTemplate.name == data.name.strip()).first():
        raise HTTPException(status_code=400, detail="模板名称已存在：%s" % data.name.strip())
    tpl = BatchCreateTemplate(
        name=data.name.strip(),
        shape=data.shape,
        ocpus=data.ocpus,
        memory_gb=data.memory_gb,
        count_per_account=data.count_per_account,
        name_prefix=data.name_prefix,
        retry_mode=data.retry_mode,
        image_ocid=data.image_ocid,
        subnet_ocid=data.subnet_ocid,
        availability_domain=data.availability_domain,
        remark=data.remark,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl

@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.get(BatchCreateTemplate, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    name = tpl.name
    db.delete(tpl)
    db.commit()
    return {"ok": True}

@router.get("/{task_id}", response_model=BatchCreateTaskDetailOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    """任务详情 + items 进度（前端轮询刷新）。"""
    task = _get_task(db, task_id)
    items = (
        db.query(BatchCreateItem)
        .filter(BatchCreateItem.task_id == task_id)
        .order_by(BatchCreateItem.id).all()
    )
    account_names = {a.id: a.name for a in db.query(Account).all()}
    out = _to_task_out(task)
    return BatchCreateTaskDetailOut(**out.model_dump(), items=[_to_item_out(i, account_names) for i in items])

@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """取消任务：CAS running → cancelled，未完成的 item 置 cancelled。"""
    task = _get_task(db, task_id)
    ok = await batch_create_manager.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="只有创建中的任务才能取消")
    return {"ok": True, "task_id": task_id, "status": "cancelled"}

@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在创建中，请先取消再删除")
    db.delete(task)  # items 级联删除
    db.commit()
    return {"ok": True}


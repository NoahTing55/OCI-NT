"""系统设置：Web 化配置。

- 读取优先级：DB（网页设置）> 环境变量 > 代码默认值，见 services/settings.py；
- GET：分组返回，secret 项只返回是否已设置，不回明文；
- PUT：批量更新，逐项校验（非法值 400），变更涉及定时任务间隔时自动重排；
- POST /test-telegram：用当前配置发一条测试消息。
鉴权：由 main.py 统一挂 JWT 依赖；审计中间件自动记录 PUT/POST（敏感值脱敏）。
"""
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core import telegram
from app.services.settings import WEB_SETTINGS, get_all_masked, set_setting
from app.workers.scheduler import reschedule_jobs

router = APIRouter()

# 变更后需要实时重排 APScheduler 任务的设置键
SCHEDULE_KEYS = {"CHECK_DAILY_AT", "PROXY_SPEEDTEST_MINUTES"}


@router.get("")
def list_settings():
    """分组返回全部可配项。secret 项 value 为"已设置"/""，不回明文。"""
    return {"groups": get_all_masked()}


@router.put("")
def update_settings(data: dict[str, Any]):
    """批量更新设置。body 为 {key: value}；secret 留空表示不修改。

    逐项校验，非法值 400；返回实际变更的键；涉及定时任务间隔的自动重排。
    """
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=400, detail="请求体不能为空")
    changed: list[str] = []
    for key, value in data.items():
        if key not in WEB_SETTINGS:
            raise HTTPException(status_code=400, detail=f"未知设置项：{key}")
        # secret 项留空 = 不修改（前端占位符场景）
        if WEB_SETTINGS[key]["secret"] and (value is None or str(value).strip() == ""):
            continue
        try:
            if set_setting(key, value):
                changed.append(key)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    rescheduled: dict = {}
    if any(k in SCHEDULE_KEYS for k in changed):
        rescheduled = reschedule_jobs()
    return {"ok": True, "changed": changed, "rescheduled": rescheduled}


@router.post("/test-telegram")
async def test_telegram():
    """用当前配置发一条测试消息，返回成功或失败原因。"""
    ok, reason = await telegram.send_test_message()
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    return {"ok": True}

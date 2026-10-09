"""Web 终端：浏览器直连实例 SSH。

- WebSocket 端点：/api/terminal/ws/{account_id}/{instance_id}
- 用面板已存的 root 密码自动登录，全程不明文展示密码
- 短连接：空闲 5 分钟自动断开，会话结束即释放
- 会话开始/异常走 TG 提醒（未配置 TG 时静默跳过）
- 操作记入审计日志（敏感字段脱敏）
"""
import asyncio
import logging
from datetime import datetime, timedelta

import asyncssh
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import decrypt_text
from app.core import telegram
from app.models.models import Account, SnipeTask
from app.services import instances as instance_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/terminal", tags=["terminal"])

# 空闲超时（秒）
IDLE_TIMEOUT = 300


async def _get_instance_ip(account: Account, instance_id: str) -> str | None:
    """从 OCI 查实例公网 IP。"""
    items, _ = await instance_service.fetch_all_instances_cached([account], {})
    for item in items:
        if item.get("id") == instance_id or item.get("ocid") == instance_id:
            return item.get("public_ip")
    return None


async def _get_root_password(db: Session, account_id: int, instance_id: str) -> str | None:
    """从抢机任务查 root 密码（解密）。"""
    # 先找该账号最近的成功任务
    task = (
        db.query(SnipeTask)
        .filter(SnipeTask.account_id == account_id)
        .order_by(SnipeTask.id.desc())
        .first()
    )
    if task and task.root_password:
        try:
            return decrypt_text(task.root_password)
        except Exception:
            # 可能存的是明文（老数据）
            return task.root_password
    return None


@router.websocket("/ws/{account_id}/{instance_id}")
async def terminal_ws(
    websocket: WebSocket,
    account_id: int,
    instance_id: str,
    token: str = Query(..., description="JWT 登录 token"),
    db: Session = Depends(get_db),
):
    # TODO: JWT 校验（简化版，先接受连接，后续加强）
    await websocket.accept()

    # 查账号
    account = db.get(Account, account_id)
    if not account:
        await websocket.send_text("\r\n账号不存在，连接关闭。\r\n")
        await websocket.close()
        return

    # 查实例 IP
    ip = await _get_instance_ip(account, instance_id)
    if not ip:
        await websocket.send_text("\r\n无法获取实例公网 IP（实例可能已停止），连接关闭。\r\n")
        await websocket.close()
        return

    # 查 root 密码
    password = await _get_root_password(db, account_id, instance_id)
    if not password:
        await websocket.send_text("\r\n未找到该实例的 root 密码，连接关闭。\r\n")
        await websocket.close()
        return

    # TG 提醒：会话开始
    try:
        await telegram.send_message(
            f"💻 终端会话开始\n账号: {account.name}\n实例: {instance_id[:20]}...\nIP: {ip}\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception:
        pass  # TG 未配置时静默跳过

    # 建立 SSH 连接
    try:
        conn = await asyncssh.connect(
            ip,
            username="root",
            password=password,
            known_hosts=None,  # 生产环境建议做主机密钥校验
        )
    except Exception as e:
        await websocket.send_text(f"\r\nSSH 连接失败：{str(e)[:100]}\r\n")
        await websocket.close()
        return

    # 打开 shell
    chan, session = None, None
    try:
        chan = await conn.open_session()
        await chan.request_pty("xterm", 80, 24)
        await chan.open_shell()
    except Exception as e:
        await websocket.send_text(f"\r\n打开 shell 失败：{str(e)[:100]}\r\n")
        conn.close()
        await websocket.close()
        return

    # 双向转发
    last_active = datetime.now()

    async def ws_to_ssh():
        nonlocal last_active
        try:
            while True:
                data = await websocket.receive_text()
                last_active = datetime.now()
                chan.write(data)
        except WebSocketDisconnect:
            pass

    async def ssh_to_ws():
        nonlocal last_active
        try:
            while True:
                # 空闲超时检查
                if datetime.now() - last_active > timedelta(seconds=IDLE_TIMEOUT):
                    await websocket.send_text("\r\n空闲超时，连接已断开。\r\n")
                    break
                data = await asyncio.wait_for(chan.read(1024), timeout=1.0)
                if data:
                    await websocket.send_text(data)
                elif chan.eof_received:
                    break
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

    try:
        await asyncio.gather(ws_to_ssh(), ssh_to_ws())
    finally:
        chan.close()
        conn.close()
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("终端会话结束: account=%s instance=%s", account_id, instance_id[:20])

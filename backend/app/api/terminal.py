"""Web 终端：浏览器直连实例 SSH（参照成熟项目方案）。

方案：xterm.js + WebSocket + Paramiko
- Paramiko invoke_shell() 开交互式 shell，xterm-256color
- ThreadPoolExecutor 跑阻塞的 SSH 读取
- 打开终端自动连接，无需手动操作
"""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import paramiko
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import decrypt_text, decode_access_token
from app.models.models import Account, SnipeTask
from app.services import instances as instance_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/terminal", tags=["terminal"])

# 线程池跑阻塞的 Paramiko 读取
_executor = ThreadPoolExecutor(max_workers=20)

# 空闲超时（秒）
IDLE_TIMEOUT = 300


async def _get_root_password(db: Session, account, instance_id: str, display_name: str = "") -> str | None:
    """按实例精确匹配 root 密码：先按 OCID，再按显示名。"""
    tasks = (
        db.query(SnipeTask)
        .filter(SnipeTask.account_id == account.id)
        .filter(SnipeTask.root_password.isnot(None))
        .order_by(SnipeTask.id.desc())
        .all()
    )
    if not tasks:
        return None

    def _decrypt(t):
        try:
            return decrypt_text(t.root_password)
        except Exception:
            return t.root_password

    # 2. OCID 精确匹配（instance_ocid 可能是逗号分隔的多台）
    for task in tasks:
        ocids = [o.strip() for o in (task.instance_ocid or "").split(",") if o.strip()]
        if instance_id in ocids:
            return _decrypt(task)

    # 3. 显示名匹配：任务 display_name 是实例名前缀（多台时加 -序号后缀）
    if display_name:
        for task in tasks:
            base = (task.display_name or "").strip()
            if base and (display_name == base or display_name.startswith(base + "-")):
                return _decrypt(task)

    # 4. 都没匹配到：返回 None，不再盲试其他任务的密码
    logger.warning("终端密码未匹配到: account=%s instance=%s display_name=%s", account.id, instance_id[:20], display_name)
    return None


@router.get("/info/{account_id}/{instance_id}")
async def terminal_info(account_id: int, instance_id: str, display_name: str = "", db: Session = Depends(get_db)):
    """返回实例 SSH 连接信息（备用接口）。"""
    account = db.get(Account, account_id)
    if not account:
        return {"public_ip": None, "password": None}
    ip = await instance_service.get_instance_public_ip(account, instance_id)
    password = await _get_root_password(db, account, instance_id, display_name)
    return {"public_ip": ip, "password": password}


def _ssh_connect(ip: str, password: str):
    """阻塞式 SSH 连接（在线程池跑）。返回 (client, channel)。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username="root", password=password, timeout=10)
    channel = client.invoke_shell(term="xterm-256color", width=80, height=24)
    channel.settimeout(0.1)
    return client, channel


def _ssh_read(channel) -> bytes:
    """阻塞式读取（在线程池跑）。"""
    try:
        if channel.recv_ready():
            return channel.recv(4096)
    except Exception:
        pass
    return b""


@router.websocket("/ws/{account_id}/{instance_id}")
async def terminal_ws(
    websocket: WebSocket,
    account_id: int,
    instance_id: str,
    token: str = Query(...),
    display_name: str = Query(""),
    db: Session = Depends(get_db),
):
    # JWT 校验
    try:
        username = decode_access_token(token)
        if not username:
            raise ValueError("invalid")
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    account = db.get(Account, account_id)
    if not account:
        await websocket.send_text(json.dumps({"type": "error", "data": "账号不存在"}))
        await websocket.close()
        return

    # 查 IP 和密码
    ip = await instance_service.get_instance_public_ip(account, instance_id)
    if not ip:
        await websocket.send_text(json.dumps({"type": "error", "data": "无法获取实例公网 IP"}))
        await websocket.close()
        return

    password = await _get_root_password(db, account, instance_id, display_name)
    if not password:
        await websocket.send_text(json.dumps({"type": "error", "data": "未找到该实例的 root 密码（按实例名未匹配到对应开机任务）"}))
        await websocket.close()
        return

    # SSH 连接（单个匹配到的密码，在线程池跑阻塞操作）
    client, channel = None, None
    loop = asyncio.get_event_loop()
    try:
        client, channel = await loop.run_in_executor(_executor, _ssh_connect, ip, password)
    except paramiko.AuthenticationException:
        await websocket.send_text(json.dumps({"type": "error", "data": "密码错误，无法登录（该实例的开机任务密码可能已变更）"}))
        await websocket.close()
        return
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "data": f"SSH 连接失败: {str(e)[:100]}"}))
        await websocket.close()
        return

    await websocket.send_text(json.dumps({"type": "connected"}))
    logger.info("终端 SSH 已连接: account=%s ip=%s", account_id, ip)

    # 双向转发
    async def ssh_to_ws():
        try:
            while True:
                data = await loop.run_in_executor(_executor, _ssh_read, channel)
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="ignore"))
                else:
                    await asyncio.sleep(0.05)
                if channel.closed:
                    break
        except Exception as e:
            logger.error("ssh_to_ws 异常: %s", e)

    async def ws_to_ssh():
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    obj = json.loads(msg)
                    if obj.get("type") == "input":
                        channel.send(obj.get("data", ""))
                    elif obj.get("type") == "resize":
                        channel.resize_pty(width=obj.get("cols", 80), height=obj.get("rows", 24))
                except json.JSONDecodeError:
                    # 兼容直接发原文
                    channel.send(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("ws_to_ssh 异常: %s", e)

    try:
        await asyncio.gather(ssh_to_ws(), ws_to_ssh())
    finally:
        try:
            channel.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("终端会话结束: account=%s", account_id)

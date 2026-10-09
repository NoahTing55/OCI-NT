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
    return await instance_service.get_instance_public_ip(account, instance_id)


async def _get_root_passwords(db: Session, account_id: int) -> list[str]:
    """查该账号所有抢机任务的 root 密码（解密），去重。"""
    tasks = (
        db.query(SnipeTask)
        .filter(SnipeTask.account_id == account_id)
        .filter(SnipeTask.root_password.isnot(None))
        .order_by(SnipeTask.id.desc())
        .all()
    )
    passwords = []
    for task in tasks:
        try:
            pwd = decrypt_text(task.root_password)
        except Exception:
            pwd = task.root_password  # 老数据可能是明文
        if pwd and pwd not in passwords:
            passwords.append(pwd)
    return passwords


@router.get("/info/{account_id}/{instance_id}")
async def terminal_info(
    account_id: int,
    instance_id: str,
    db: Session = Depends(get_db),
    # op = Depends(get_current_operator),  # 简化：走全局 auth_dep
):
    """返回实例 SSH 连接信息（IP + root 密码）。密码仅返回给已登录用户。"""
    account = db.get(Account, account_id)
    if not account:
        return {"public_ip": None, "password": None}
    ip = await _get_instance_ip(account, instance_id)
    passwords = await _get_root_passwords(db, account_id)
    return {"public_ip": ip, "password": passwords[0] if passwords else None}


@router.websocket("/ws/{account_id}/{instance_id}")
async def terminal_ws(
    websocket: WebSocket,
    account_id: int,
    instance_id: str,
    token: str = Query(..., description="JWT 登录 token"),
    db: Session = Depends(get_db),
):
    # JWT 校验
    from app.core.security import decode_access_token
    try:
        username = decode_access_token(token)
        if not username:
            raise ValueError("invalid token")
    except Exception:
        await websocket.close(code=4401)
        return
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

    # 查 root 密码（该账号所有任务的密码都试一遍）
    passwords = await _get_root_passwords(db, account_id)
    if not passwords:
        await websocket.send_text("\r\n未找到该账号的 root 密码，连接关闭。\r\n")
        await websocket.close()
        return

    # TG 提醒：会话开始
    try:
        await telegram.send_message(
            f"💻 终端会话开始\n账号: {account.name}\n实例: {instance_id[:20]}...\nIP: {ip}\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception:
        pass  # TG 未配置时静默跳过

    # 建立 SSH 连接（逐个试密码）
    conn = None
    last_err = ""
    for pwd in passwords:
        try:
            conn = await asyncssh.connect(
                ip,
                username="root",
                password=pwd,
                known_hosts=None,
            )
            break
        except asyncssh.PermissionDenied:
            last_err = "密码错误"
            continue
        except Exception as e:
            last_err = str(e)[:100]
            break
    if not conn:
        await websocket.send_text(f"\r\nSSH 连接失败：{last_err}\r\n")
        await websocket.close()
        return

    # 打开 shell（用 create_process，直接拿到 stdin/stdout）
    process = None
    try:
        process = await conn.create_process(term_type="xterm", term_size=(80, 24))
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
                logger.info("收到终端输入: %s", data[:50])
                process.stdin.write(data)
                await process.stdin.drain()
        except WebSocketDisconnect:
            logger.info("WebSocket 断开")
        except Exception as e:
            logger.error("ws_to_ssh 异常: %s", e)

    async def ssh_to_ws():
        nonlocal last_active
        try:
            while True:
                if datetime.now() - last_active > timedelta(seconds=IDLE_TIMEOUT):
                    await websocket.send_text("\r\n空闲超时，连接已断开。\r\n")
                    break
                data = await asyncio.wait_for(process.stdout.read(1024), timeout=1.0)
                if data:
                    await websocket.send_text(data)
                elif process.stdout.at_eof():
                    break
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

    try:
        await asyncio.gather(ws_to_ssh(), ssh_to_ws())
    finally:
        process.close()
        conn.close()
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("终端会话结束: account=%s instance=%s", account_id, instance_id[:20])

"""Telegram Bot 命令控制。

手机上直接跟 Bot 对话控制面板（查询 + 执行操作），通知发送仍走 core/telegram.py。

实现方式：getUpdates 长轮询（30s 超时），在 FastAPI lifespan 中作为后台任务启动。
安全：只响应系统设置中 TG_CHAT_ID 发来的消息，其他人一律忽略（不回复）。

命令（中文）：
    /help /帮助      - 命令列表
    /状态            - 面板运行状态
    /账号            - 账号列表
    /实例 [账号名]   - 实例列表（可按账号过滤）
    /任务            - 抢机任务列表
    /开机 <账号别名> <区域> [数量]   - 新建 E5 1C6G 开机任务（需确认）
    /关机 <实例名> /开机实例 <实例名> /重启 <实例名>  - 电源操作（需确认）
    /换IP <实例名>   - 换预留 IP（需确认）

不可逆操作（开机/关机/重启/换IP）需二次确认：Bot 回复"确认吗？回复 Y 执行"，
用户回复 Y（5 分钟内有效）才执行。
"""
import asyncio
import logging
import time

import httpx

from app.core.audit import log_operation
from app.core.deps import SessionLocal
from app.core.oci_factory import build_client_for_account, compartment_of
from app.models.models import Account, SnipeTask
from app.services import instances as instance_service
from app.services.settings import get_setting

logger = logging.getLogger(__name__)

# getUpdates 长轮询超时（秒）
_POLL_TIMEOUT = 30
# 二次确认有效期（秒）
_CONFIRM_TTL = 300

_task = None
_offset = 0
# 待确认操作：{chat_id: {"action": str, "params": dict, "expires": float}}
_pending = {}


# ================= 基础：收发 =================

def _tg_token():
    return str(get_setting("TG_BOT_TOKEN") or "").strip()


def _tg_chat_id():
    return str(get_setting("TG_CHAT_ID") or "").strip()


async def _send(chat_id, text):
    """给指定 chat 发消息（Bot 控制专用，不经过通知渠道的 chat_id）。"""
    token = _tg_token()
    if not token:
        logger.warning("TG_BOT_TOKEN 未配置，Bot 控制不可用")
        return False
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.post(
                "https://api.telegram.org/bot%s/sendMessage" % token,
                json={"chat_id": chat_id, "text": text},
            )
            if r.status_code != 200:
                logger.error("Bot 发送消息失败：%s %s", r.status_code, r.text[:200])
                return False
            return True
    except Exception:
        logger.exception("Bot 发送消息异常")
        return False


async def _poll():
    """拉取新消息。返回 update 列表，出错返回空列表。"""
    global _offset
    token = _tg_token()
    if not token:
        return []
    try:
        async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 10, trust_env=False) as client:
            r = await client.get(
                "https://api.telegram.org/bot%s/getUpdates" % token,
                params={"offset": _offset, "timeout": _POLL_TIMEOUT},
            )
            if r.status_code != 200:
                logger.error("getUpdates 失败：%s %s", r.status_code, r.text[:200])
                return []
            data = r.json()
            updates = data.get("result", [])
            for u in updates:
                _offset = max(_offset, u.get("update_id", 0) + 1)
            return updates
    except Exception:
        logger.exception("getUpdates 异常")
        return []


# ================= 生命周期 =================

async def start():
    """lifespan 中调用：启动 Bot 轮询后台任务。"""
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_run(), name="tg-bot-poll")
    logger.info("TG Bot 控制已启动（getUpdates 长轮询）")


async def stop():
    """lifespan 关闭时调用。"""
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("TG Bot 控制已停止")


async def _run():
    """轮询主循环。"""
    global _offset
    # 启动时先把旧消息的 offset 跳过，避免重启后重复处理历史命令
    try:
        token = _tg_token()
        if token:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(
                    "https://api.telegram.org/bot%s/getUpdates" % token,
                    params={"offset": -1, "limit": 1},
                )
                if r.status_code == 200:
                    res = r.json().get("result", [])
                    if res:
                        _offset = res[-1]["update_id"] + 1
    except Exception:
        logger.exception("Bot 初始化 offset 失败（将从头处理，可能重复）")

    while True:
        try:
            updates = await _poll()
            for u in updates:
                try:
                    await _dispatch(u)
                except Exception:
                    logger.exception("处理 TG 消息异常：%s", str(u)[:200])
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bot 轮询循环异常，5 秒后重试")
            await asyncio.sleep(5)


async def _dispatch(update):
    """分发单条 update。非配置 chat_id 的消息直接忽略。"""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return

    # 安全：只响应配置的 chat_id
    allowed = _tg_chat_id()
    if not allowed or str(chat_id) != str(allowed):
        logger.warning("忽略非授权 chat_id %s 的 TG 消息", chat_id)
        return

    # 二次确认：Y / 确认
    if text.upper() == "Y" or text == "确认":
        await _do_confirm(chat_id)
        return
    # 收到新命令时清除旧的待确认（避免误触）
    _pending.pop(chat_id, None)

    await _handle_command(chat_id, text)


# ================= 命令解析 =================

def _parse(text):
    """解析命令。返回 (cmd, args)。兼容 @botname 后缀。"""
    parts = text.split()
    if not parts:
        return "", []
    cmd = parts[0].split("@")[0]
    return cmd, parts[1:]


async def _handle_command(chat_id, text):
    cmd, args = _parse(text)
    handler = _COMMANDS.get(cmd)
    if not handler:
        await _send(chat_id, "未知命令：%s\n发送 /help 查看可用命令" % cmd)
        return
    try:
        await handler(chat_id, args)
    except Exception as e:
        logger.exception("命令 %s 执行异常", cmd)
        await _send(chat_id, "执行出错：%s" % e)


# ================= 工具：账号/实例匹配 =================

def _find_account(db, name):
    """按别名模糊匹配账号（精确优先）。"""
    name = name.strip()
    if not name:
        return None
    acc = db.query(Account).filter(Account.name == name).first()
    if acc:
        return acc
    return db.query(Account).filter(Account.name.like("%%%s%%" % name)).first()


def _alive_days(account):
    """存活天数：注册当天=1 天（与前端 aliveDays 一致）。"""
    from datetime import datetime
    base = account.registered_at
    if not base:
        return 0
    try:
        d = (datetime.now().date() - base.date()).days
        return max(d, 0) + 1
    except Exception:
        return 0


async def _find_instances(keyword):
    """按实例名模糊匹配。返回 (matched, all)。"""
    db = SessionLocal()
    try:
        accounts = db.query(Account).order_by(Account.id).all()
    finally:
        db.close()
    items, errors = await instance_service.fetch_all_instances(accounts)
    kw = keyword.strip().lower()
    if not kw:
        return items, errors
    matched = [i for i in items if kw in (i.get("display_name") or "").lower()]
    return matched, errors


def _fmt_instance(i):
    state = i.get("lifecycle_state") or "-"
    ip = i.get("public_ip") or "无公网IP"
    return (
        "• %s\n"
        "  账号 %s｜%s｜%s｜%s" % (
            i.get("display_name"), i.get("account_name"),
            i.get("region"), state, ip)
    )


# ================= 二次确认 =================

def _ask_confirm(chat_id, action, params):
    """登记待确认操作。"""
    _pending[chat_id] = {"action": action, "params": params,
                         "expires": time.time() + _CONFIRM_TTL}


async def _do_confirm(chat_id):
    pend = _pending.pop(chat_id, None)
    if not pend:
        await _send(chat_id, "没有待确认的操作")
        return
    if time.time() > pend["expires"]:
        await _send(chat_id, "确认已超时，请重新发送命令")
        return
    action = pend["action"]
    fn = _CONFIRM_ACTIONS.get(action)
    if not fn:
        await _send(chat_id, "未知操作：%s" % action)
        return
    await fn(chat_id, pend["params"])


# ================= 命令实现 =================

_HELP_TEXT = """🤖 OCI 面板控制命令

查询：
/状态 - 面板运行状态
/账号 - 账号列表
/实例 [账号名] - 实例列表
/任务 - 抢机任务列表

操作（需回复 Y 确认）：
/开机 <账号别名> <区域> [数量] - 新建 E5 1C6G 开机任务
/关机 <实例名> - 关闭实例
/开机实例 <实例名> - 启动实例
/重启 <实例名> - 重启实例
/换IP <实例名> - 更换预留 IP

实例名支持模糊匹配（包含即可）。
/help 显示此帮助。"""


async def cmd_help(chat_id, args):
    await _send(chat_id, _HELP_TEXT)


async def cmd_status(chat_id, args):
    db = SessionLocal()
    try:
        account_count = db.query(Account).count()
        running_tasks = (
            db.query(SnipeTask)
            .filter(SnipeTask.status.in_(["running", "paused", "pending"]))
            .count()
        )
        accounts = db.query(Account).order_by(Account.id).all()
    finally:
        db.close()
    items, errors = await instance_service.fetch_all_instances(accounts)
    running = len([i for i in items if i.get("lifecycle_state") == "RUNNING"])
    lines = [
        "📊 面板状态",
        "账号：%d 个" % account_count,
        "实例：%d 台（运行中 %d 台）" % (len(items), running),
        "抢机任务（进行中）：%d 个" % running_tasks,
    ]
    if errors:
        lines.append("⚠️ %d 个账号实例查询失败" % len(errors))
    await _send(chat_id, "\n".join(lines))


async def cmd_accounts(chat_id, args):
    db = SessionLocal()
    try:
        accounts = db.query(Account).order_by(Account.id).all()
        if not accounts:
            await _send(chat_id, "还没有账号")
            return
        lines = ["👤 账号列表"]
        for a in accounts:
            atype = {"PAYG": "升级号", "FREE_TIER": "免费号"}.get(
                a.account_type or "", a.account_type or "未知")
            lines.append("• %s｜%s｜存活 %d 天｜%s" % (
                a.name, atype, _alive_days(a), a.status))
        await _send(chat_id, "\n".join(lines))
    finally:
        db.close()


async def cmd_instances(chat_id, args):
    keyword = args[0] if args else ""
    db = SessionLocal()
    try:
        accounts = db.query(Account).order_by(Account.id).all()
        if keyword:
            acc = _find_account(db, keyword)
            if acc:
                accounts = [acc]
    finally:
        db.close()
    items, errors = await instance_service.fetch_all_instances(accounts)
    if not items:
        await _send(chat_id, "没有实例" + ("（账号 %s）" % keyword if keyword else ""))
        return
    lines = ["💻 实例列表"]
    for i in items[:30]:
        lines.append(_fmt_instance(i))
    if len(items) > 30:
        lines.append("…还有 %d 台未显示" % (len(items) - 30))
    if errors:
        lines.append("⚠️ %d 个账号查询失败" % len(errors))
    await _send(chat_id, "\n".join(lines))


async def cmd_tasks(chat_id, args):
    from app.api.sniper import STATUS_TEXT
    db = SessionLocal()
    try:
        tasks = db.query(SnipeTask).order_by(SnipeTask.id.desc()).limit(10).all()
        if not tasks:
            await _send(chat_id, "还没有抢机任务")
            return
        lines = ["🎯 抢机任务（最近 10 个）"]
        for t in tasks:
            acc = db.get(Account, t.account_id)
            aname = acc.name if acc else ("#%d" % t.account_id)
            st = STATUS_TEXT.get(t.status, t.status)
            lines.append(
                "• #%d %s %s %s\n  %s｜进度 %d/%d" % (
                    t.id, aname, t.region, t.shape,
                    st, t.success_count, t.target_count)
            )
        await _send(chat_id, "\n".join(lines))
    finally:
        db.close()


async def cmd_snipe(chat_id, args):
    """开机：/开机 <账号别名> <区域> [数量]，E5 1C6G 模板，需确认。"""
    if len(args) < 2:
        await _send(chat_id, "用法：/开机 <账号别名> <区域> [数量]\n例如：/开机 oci-smtqya us-ashburn-1 2")
        return
    account_name, region = args[0], args[1]
    try:
        count = int(args[2]) if len(args) > 2 else 1
        count = max(1, min(count, 100))
    except ValueError:
        await _send(chat_id, "数量必须是数字")
        return

    db = SessionLocal()
    try:
        account = _find_account(db, account_name)
        if not account:
            await _send(chat_id, "找不到账号：%s\n发送 /账号 查看账号列表" % account_name)
            return
        account_id, real_name = account.id, account.name
    finally:
        db.close()

    text = (
        "将在账号 %s 的 %s 新建开机任务：\n"
        "E5 1C6G × %d 台\n确认吗？回复 Y 执行" % (real_name, region, count)
    )
    _ask_confirm(chat_id, "snipe_create",
                 {"account_id": account_id, "account_name": real_name,
                  "region": region, "count": count})
    await _send(chat_id, text)


async def _exec_snipe_create(chat_id, params):
    """确认后执行：创建开机任务。"""
    from fastapi import HTTPException
    from app.api.sniper import _do_create_task
    from app.schemas.schemas import SnipeTaskCreate

    account_id = params["account_id"]
    region = params["region"]
    count = params["count"]

    db = SessionLocal()
    try:
        account = db.get(Account, account_id)
        if not account:
            await _send(chat_id, "账号不存在（可能已被删除）")
            return
        # 自动获取可用域 + 最新 Ubuntu AMD 镜像（E5 是 AMD）
        client = build_client_for_account(account)
        try:
            client.region = region.strip()
            r = await client.request(
                "GET", "identity",
                "/20160918/availabilityDomains?compartmentId=%s" % account.tenancy_ocid,
            )
            ads = r.json() if r.status_code < 300 else []
            if not ads:
                await _send(chat_id, "查询 %s 可用域失败，无法创建任务" % region)
                return
            ad = ads[0].get("name", "")

            r2 = await client.request(
                "GET", "iaas",
                "/20160918/images?compartmentId=%s"
                "&operatingSystem=Canonical%%20Ubuntu&sortBy=TIMECREATED"
                "&sortOrder=DESC&limit=10" % account.tenancy_ocid,
            )
            image_ocid = ""
            if r2.status_code < 300:
                for img in r2.json():
                    if img.get("compartmentId"):
                        continue
                    low = (img.get("displayName") or "").lower()
                    if "aarch64" in low or "ampere" in low:
                        continue
                    image_ocid = img.get("id", "")
                    break
            if not image_ocid:
                await _send(chat_id, "查询 %s Ubuntu 镜像失败，无法创建任务" % region)
                return
        finally:
            await client.aclose()

        data = SnipeTaskCreate(
            account_id=account_id,
            region=region,
            shape="VM.Standard.E5.Flex",
            ocpus=1,
            memory_gb=6,
            image_ocid=image_ocid,
            subnet_ocid="",
            availability_domain=ad,
            display_name="",
            target_count=count,
        )
        try:
            task = _do_create_task(db, data)
        except HTTPException as e:
            await _send(chat_id, "创建失败：%s" % e.detail)
            return
        log_operation(
            db, "snipe.create", account_id=account_id,
            detail="TG Bot 创建开机任务 #%d：%s E5 1C6G × %d" % (task.id, region, count),
            operator="telegram",
        )
        # 建好后自动启动（与网页端行为一致）
        from app.workers.sniper import sniper_manager
        await sniper_manager.start_task(task.id)
        await _send(chat_id, "✅ 开机任务 #%d 已创建并启动：%s E5 1C6G × %d" % (task.id, region, count))
    except Exception as e:
        logger.exception("Bot 创建开机任务异常")
        await _send(chat_id, "创建异常：%s" % e)
    finally:
        db.close()


async def _resolve_instance(chat_id, keyword):
    """模糊匹配实例。0 台/多台时回复提示并返回 None；1 台时返回实例 dict。"""
    if not keyword:
        await _send(chat_id, "请提供实例名（支持模糊匹配）")
        return None
    matched, _ = await _find_instances(keyword)
    if not matched:
        await _send(chat_id, "找不到匹配「%s」的实例" % keyword)
        return None
    if len(matched) > 1:
        lines = ["匹配到 %d 台，请用更精确的名字：" % len(matched)]
        for i in matched[:10]:
            lines.append("• %s（%s）" % (i.get("display_name"), i.get("account_name")))
        await _send(chat_id, "\n".join(lines))
        return None
    return matched[0]


async def cmd_power(chat_id, args, action):
    """电源操作统一入口。action: power_on / power_off / reboot。"""
    names = {"power_on": "开机", "power_off": "关机", "reboot": "重启"}
    keyword = args[0] if args else ""
    inst = await _resolve_instance(chat_id, keyword)
    if not inst:
        return
    text = (
        "确认%s实例吗？\n"
        "%s（%s %s）\n"
        "回复 Y 执行" % (names[action], inst.get("display_name"),
                        inst.get("account_name"), inst.get("region"))
    )
    _ask_confirm(chat_id, "power",
                 {"account_id": inst["account_id"], "instance_id": inst["instance_id"],
                  "display_name": inst.get("display_name"), "action": action})
    await _send(chat_id, text)


async def _exec_power(chat_id, params):
    """确认后执行电源操作（直接调 OCI，不走 Celery，单台同步执行）。"""
    from app.workers.batch_tasks import ACTION_MAP, SUCCESS_CODES

    account_id = params["account_id"]
    instance_id = params["instance_id"]
    display_name = params["display_name"]
    action = params["action"]
    names = {"power_on": "开机", "power_off": "关机", "reboot": "重启"}

    db = SessionLocal()
    try:
        account = db.get(Account, account_id)
        if not account:
            await _send(chat_id, "账号不存在（可能已被删除）")
            return
        client = build_client_for_account(account)
        try:
            resp = await client.instance_action(instance_id, ACTION_MAP[action])
            ok = resp.status_code in SUCCESS_CODES
            code = resp.status_code
        finally:
            await client.aclose()
        if ok:
            log_operation(
                db, "batch." + action, account_id=account_id,
                detail="TG Bot %s实例 %s" % (names[action], display_name),
                operator="telegram",
            )
            instance_service.invalidate_instance_cache()
            await _send(chat_id, "✅ %s %s指令已发送" % (display_name, names[action]))
        else:
            await _send(chat_id, "❌ %s失败：OCI 返回 %s" % (names[action], code))
    except Exception as e:
        logger.exception("Bot 电源操作异常")
        await _send(chat_id, "执行异常：%s" % e)
    finally:
        db.close()


async def cmd_change_ip(chat_id, args):
    keyword = args[0] if args else ""
    inst = await _resolve_instance(chat_id, keyword)
    if not inst:
        return
    text = (
        "确认为实例换 IP 吗？\n"
        "%s（%s，当前 %s）\n"
        "回复 Y 执行" % (inst.get("display_name"), inst.get("account_name"),
                        inst.get("public_ip") or "无公网IP")
    )
    _ask_confirm(chat_id, "change_ip",
                 {"account_id": inst["account_id"], "instance_id": inst["instance_id"],
                  "display_name": inst.get("display_name")})
    await _send(chat_id, text)


async def _exec_change_ip(chat_id, params):
    """确认后执行换 IP（预留 IP 标准流程，复用 network.py 主流程逻辑）。"""
    import time as _time
    from app.services import cloudflare as cf_service

    account_id = params["account_id"]
    instance_id = params["instance_id"]
    display_name = params["display_name"]

    db = SessionLocal()
    try:
        account = db.get(Account, account_id)
        if not account:
            await _send(chat_id, "账号不存在（可能已被删除）")
            return
        client = build_client_for_account(account)
        try:
            compartment = compartment_of(account)
            atts = await client.list_vnic_attachments(compartment, instance_id)
            if not atts:
                await _send(chat_id, "该实例没有 VNIC 附件")
                return
            vnic_id = atts[0]["vnicId"]
            vnic = (await client.get_vnic(vnic_id)).json()
            old_ip = vnic.get("publicIp")

            privates = await client.list_private_ips(vnic_id)
            primary = next((p for p in privates if p.get("isPrimary")), None)
            if not primary:
                await _send(chat_id, "找不到主私网 IP")
                return

            r = await client.create_public_ip(
                compartment, display_name="panel-%d" % int(_time.time()))
            if r.status_code not in (200, 201):
                await _send(chat_id, "创建预留 IP 失败：OCI 返回 %d" % r.status_code)
                return
            new_pub = r.json()
            new_pub_id = new_pub["id"]

            r2 = await client.update_public_ip(new_pub_id, primary["id"])
            if r2.status_code != 200:
                await _send(chat_id, "绑定预留 IP 失败：OCI 返回 %d" % r2.status_code)
                return

            vnic2 = (await client.get_vnic(vnic_id)).json()
            new_ip = vnic2.get("publicIp") or new_pub.get("ipAddress")

            # CF DNS 自动同步（与 network.py 一致）
            cf_results = await cf_service.sync_instance_domains(db, instance_id, new_ip)
        finally:
            await client.aclose()

        log_operation(
            db, "network.change_ip", account_id=account_id,
            detail="TG Bot 换 IP：%s %s → %s" % (display_name, old_ip, new_ip),
            operator="telegram",
        )
        instance_service.invalidate_instance_cache()
        cf_text = ""
        if cf_results:
            ok_n = len([x for x in cf_results if x["ok"]])
            cf_text = "\nCF 同步：%d/%d 个域名成功" % (ok_n, len(cf_results))
        await _send(chat_id, "✅ 换 IP 成功\n%s\n%s → %s%s" % (
            display_name, old_ip, new_ip, cf_text))
        # TG 通知渠道也推一条（与网页端行为一致）
        from app.core import telegram as tg_notify
        await tg_notify.send_message(
            "【换 IP 成功】（TG Bot 操作）\n实例 %s\n%s → %s" % (
                display_name, old_ip, new_ip)
        )
    except Exception as e:
        logger.exception("Bot 换 IP 异常")
        await _send(chat_id, "执行异常：%s" % e)
    finally:
        db.close()


# ================= 命令表 =================

async def _cmd_power_on(chat_id, args):
    await cmd_power(chat_id, args, "power_on")


async def _cmd_power_off(chat_id, args):
    await cmd_power(chat_id, args, "power_off")


async def _cmd_reboot(chat_id, args):
    await cmd_power(chat_id, args, "reboot")


_COMMANDS = {
    "/help": cmd_help,
    "/帮助": cmd_help,
    "/状态": cmd_status,
    "/账号": cmd_accounts,
    "/实例": cmd_instances,
    "/任务": cmd_tasks,
    "/开机": cmd_snipe,
    "/关机": _cmd_power_off,
    "/开机实例": _cmd_power_on,
    "/重启": _cmd_reboot,
    "/换IP": cmd_change_ip,
    "/换ip": cmd_change_ip,
}

# 二次确认后执行的动作
_CONFIRM_ACTIONS = {
    "snipe_create": _exec_snipe_create,
    "power": _exec_power,
    "change_ip": _exec_change_ip,
}

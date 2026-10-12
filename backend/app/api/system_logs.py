"""系统日志查看：supervisord 管理的各进程日志文件"""
import os
from fastapi import APIRouter, Depends, Query, HTTPException
from app.core.deps import get_current_operator

router = APIRouter(prefix="/api/system-logs", tags=["系统日志"])

# 允许查看的日志文件（单容器版在 /var/log/ 下，四容器版走 docker logs）
LOG_FILES = {
    "api": "/var/log/api.log",
    "worker": "/var/log/worker.log",
}

@router.get("")
def list_logs(user=Depends(get_current_operator)):
    """列出可用的日志文件"""
    result = []
    for name, path in LOG_FILES.items():
        exists = os.path.isfile(path)
        size = os.path.getsize(path) if exists else 0
        result.append({"name": name, "path": path, "exists": exists, "size": size})
    return result

# 时间戳格式：
#   2026-10-11 17:24:28 / 2026-10-11T17:24:28（标准）
#   [2026-10-11 17:24:28,196: WARNING/MainProcess]（celery，方括号前缀+毫秒逗号）
_TS_RES = []
def _extract_ts(line: str) -> str | None:
    """从一行日志提取时间戳，返回 'YYYY-MM-DD HH:MM:SS'，提不到返回 None。"""
    import re
    if not _TS_RES:
        _TS_RES.extend([
            re.compile(r'^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})'),
            re.compile(r'^\[?(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})[,.]\d+'),
        ])
    for r in _TS_RES:
        m = r.match(line)
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


@router.get("/merged")
def get_merged_log(
    lines: int = Query(500, ge=1, le=2000),
    user=Depends(get_current_operator),
):
    """合并 api + worker 日志，按时间戳排序。

    无时间戳的行（uvicorn 输出、多行警告的后续行等）跟随上一行的时间戳，
    保证它们不乱序、始终跟自己的上下文在一起。
    """
    all_entries = []
    for name, path in LOG_FILES.items():
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 256 * 1024)
            f.seek(size - read_size)
            data = f.read().decode("utf-8", errors="replace")
        last_ts = "0000-00-00 00:00:00"  # 文件开头无时间戳的行放最前
        for line in data.splitlines():
            ts = _extract_ts(line)
            if ts:
                last_ts = ts
            # (排序键, 文件内序号, 展示行)：序号保证同时间戳内保持原文件顺序
            all_entries.append((last_ts, len(all_entries), f"[{name}] {line}"))
    all_entries.sort(key=lambda x: (x[0], x[1]))
    merged = [e[2] for e in all_entries]
    return {"name": "merged", "lines": merged[-lines:], "total": len(merged)}

@router.get("/{name}")
def get_log(
    name: str,
    lines: int = Query(200, ge=1, le=2000),
    user=Depends(get_current_operator),
):
    """读取指定日志的最后 N 行"""
    path = LOG_FILES.get(name)
    if not path:
        raise HTTPException(404, "未知日志")
    if not os.path.isfile(path):
        return {"name": name, "lines": [], "message": "日志文件不存在（可能是四容器部署）"}
    # 读最后 N 行，避免大文件 OOM
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        # 从尾部向前读，最多 512KB
        read_size = min(size, 512 * 1024)
        f.seek(size - read_size)
        data = f.read().decode("utf-8", errors="replace")
    all_lines = data.splitlines()
    return {"name": name, "lines": all_lines[-lines:], "total": len(all_lines)}

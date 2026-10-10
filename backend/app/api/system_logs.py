"""系统日志查看：supervisord 管理的各进程日志文件"""
import os
from fastapi import APIRouter, Depends, Query, HTTPException
from app.core.deps import get_current_operator

router = APIRouter(prefix="/api/system-logs", tags=["系统日志"])

# 允许查看的日志文件（单容器版在 /var/log/ 下，四容器版走 docker logs）
LOG_FILES = {
    "api": "/var/log/api.log",
    "worker": "/var/log/worker.log",
    "postgres": "/var/log/postgres.log",
    "redis": "/var/log/redis.log",
    "supervisord": "/var/log/supervisord.log",
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

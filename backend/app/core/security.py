"""安全基础：Fernet 对称加密 + 登录密码哈希 + JWT。

- Fernet：加密账号私钥 / 代理密码 / CF Token / TOTP 密钥。
  私钥明文只在「创建/更新账号」请求的瞬间出现在内存中，入库前必须加密；
  解密也只在发起 OCI 请求前的内存中进行，永不返回给前端。
- 密码：pbkdf2_hmac(sha256, 20 万轮) 加盐存储，不引入额外依赖。
- JWT：PyJWT，HS256，用于面板登录鉴权。
"""
import hashlib
import hmac
import os
import time

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _get_fernet() -> Fernet:
    key = settings.MASTER_KEY.strip()
    if not key:
        raise RuntimeError("MASTER_KEY 未配置：请在 .env 中设置（用 Fernet.generate_key() 生成）")
    return Fernet(key.encode("utf-8"))


def encrypt_text(plain: str) -> str:
    """加密明文，返回字符串。"""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_text(cipher: str) -> str:
    """解密。MASTER_KEY 不对或数据损坏时抛 ValueError。"""
    try:
        return _get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("解密失败：MASTER_KEY 可能不正确或数据已损坏") from e


# ---------- 登录密码 ----------

def hash_password(password: str) -> str:
    """pbkdf2_sha256 加盐哈希，格式：pbkdf2_sha256$轮数$salt_hex$hash_hex。"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """校验密码。用 hmac.compare_digest 防时序攻击，格式异常返回 False。"""
    try:
        algo, iters, salt_hex, hash_hex = hashed.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------- JWT ----------

def _jwt_key() -> str:
    key = settings.JWT_SECRET_KEY.strip() or settings.MASTER_KEY.strip()
    if not key:
        raise RuntimeError("JWT_SECRET_KEY / MASTER_KEY 未配置：请在 .env 中设置")
    return key


def create_access_token(username: str, expires_minutes: int | None = None) -> str:
    """签发 JWT，sub=用户名。"""
    minutes = expires_minutes if expires_minutes is not None else settings.JWT_EXPIRE_MINUTES
    payload = {"sub": username, "exp": int(time.time()) + minutes * 60}
    return jwt.encode(payload, _jwt_key(), algorithm="HS256")


def decode_access_token(token: str) -> str | None:
    """解码 JWT，返回用户名；过期/伪造/格式错误返回 None。"""
    try:
        payload = jwt.decode(token, _jwt_key(), algorithms=["HS256"])
        sub = payload.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None

"""TOTP 双因素：pyotp 实现。

流程：用户在「安全设置」页点绑定 → 后端生成 secret（Fernet 加密存库，
此时尚未启用）→ 返回 otpauth URI + 二维码 → 用户用验证器 App 扫码 →
输入 6 位动态码校验通过 → 正式启用。登录时若用户启用了 TOTP，
必须在密码之后再传 totp_code。
"""
import base64
import io

import pyotp
import qrcode

from app.core.config import settings
from app.core.security import decrypt_text, encrypt_text

# 加密前缀：区分普通文本与 TOTP 密钥
_TOTP_PREFIX = "totp:"


def generate_totp_secret() -> str:
    """生成 32 字符 base32 密钥。"""
    return pyotp.random_base32()


def get_otpauth_uri(secret: str, username: str) -> str:
    """拼 otpauth URI，验证器 App 扫码用。"""
    issuer = settings.APP_NAME.replace(" ", "")
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def make_qr_data_uri(otpauth_uri: str) -> str:
    """把 otpauth URI 画成二维码，返回 data URI，前端直接 <img src>。"""
    img = qrcode.make(otpauth_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


def verify_totp(secret: str, code: str) -> bool:
    """校验 6 位动态码。valid_window=1 容忍前后 30 秒时钟偏差。"""
    try:
        return pyotp.TOTP(secret).verify((code or "").strip(), valid_window=1)
    except Exception:
        return False


def encrypt_totp_secret(secret: str) -> str:
    """TOTP 密钥 Fernet 加密入库。"""
    return encrypt_text(_TOTP_PREFIX + secret)


def decrypt_totp_secret(enc: str) -> str:
    """解密 TOTP 密钥。"""
    plain = decrypt_text(enc)
    return plain[len(_TOTP_PREFIX):] if plain.startswith(_TOTP_PREFIX) else plain

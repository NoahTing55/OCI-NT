"""登录鉴权 + TOTP 双因素。

- POST /api/auth/init：初始化首个管理员（仅 operators 表为空时可用）；
- POST /api/auth/login：密码校验 → 若用户启用 TOTP 则必须再传 totp_code；
- GET /api/auth/me：当前登录账号信息；
- POST /api/auth/change-password：改密码；
- POST /api/auth/totp/setup：生成密钥 + otpauth URI + 二维码（此时未启用）；
- POST /api/auth/totp/enable：输入动态码校验通过后正式启用；
- POST /api/auth/totp/disable：密码二次确认后解绑。

注意：审计中间件会自动记录这些写操作；/api/auth/* 的参数摘要被强制置空，
密码/验证码绝不入库。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import totp as totp_lib
from app.core.deps import get_current_operator, get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.models import Operator

router = APIRouter()


class LoginIn(BaseModel):
    username: str
    password: str
    totp_code: str = ""


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class TotpCodeIn(BaseModel):
    totp_code: str


class TotpDisableIn(BaseModel):
    password: str


@router.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    """登录。TOTP 启用用户：第一次只传密码会返回 401/totp_required，前端再弹窗要动态码。"""
    op = db.query(Operator).filter(Operator.username == data.username.strip()).first()
    if not op or not op.is_active or not verify_password(data.password, op.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if op.totp_enabled:
        if not data.totp_code:
            raise HTTPException(status_code=401, detail="totp_required")
        if not totp_lib.verify_totp(totp_lib.decrypt_totp_secret(op.totp_secret_enc), data.totp_code):
            raise HTTPException(status_code=401, detail="动态验证码错误")
    return {
        "access_token": create_access_token(op.username),
        "token_type": "bearer",
        "username": op.username,
        "totp_enabled": op.totp_enabled,
    }


@router.post("/init")
def init_admin(data: LoginIn, db: Session = Depends(get_db)):
    """初始化首个管理员。表非空时 403，部署后该接口自动关闭。"""
    if db.query(Operator).count() > 0:
        raise HTTPException(status_code=403, detail="已存在管理员账号，该接口已关闭")
    if not data.username.strip() or len(data.password) < 6:
        raise HTTPException(status_code=400, detail="用户名不能为空，密码至少 6 位")
    op = Operator(username=data.username.strip(), password_hash=hash_password(data.password))
    db.add(op)
    db.commit()
    return {"ok": True, "username": op.username}


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """公开接口：是否已初始化管理员（登录页判断是否显示初始化入口）。"""
    return {"initialized": db.query(Operator).count() > 0}


@router.get("/me")
def me(op: Operator = Depends(get_current_operator)):
    return {"username": op.username, "totp_enabled": op.totp_enabled}


@router.post("/change-password")
def change_password(
    data: ChangePasswordIn,
    db: Session = Depends(get_db),
    op: Operator = Depends(get_current_operator),
):
    if not verify_password(data.old_password, op.password_hash):
        raise HTTPException(status_code=401, detail="原密码错误")
    op.password_hash = hash_password(data.new_password)
    db.commit()
    return {"ok": True}


@router.post("/totp/setup")
def totp_setup(db: Session = Depends(get_db), op: Operator = Depends(get_current_operator)):
    """生成 TOTP 密钥（加密入库，此时未启用），返回扫码用的 URI + 二维码。"""
    secret = totp_lib.generate_totp_secret()
    op.totp_secret_enc = totp_lib.encrypt_totp_secret(secret)
    db.commit()
    uri = totp_lib.get_otpauth_uri(secret, op.username)
    return {
        "otpauth_uri": uri,
        "secret": secret,
        "qr_data_uri": totp_lib.make_qr_data_uri(uri),
        "enabled": False,
    }


@router.post("/totp/enable")
def totp_enable(
    data: TotpCodeIn, db: Session = Depends(get_db), op: Operator = Depends(get_current_operator)
):
    """输入验证器 App 上的 6 位码，校验通过后正式启用双因素。"""
    if not op.totp_secret_enc:
        raise HTTPException(status_code=400, detail="请先获取绑定二维码")
    if not totp_lib.verify_totp(totp_lib.decrypt_totp_secret(op.totp_secret_enc), data.totp_code):
        raise HTTPException(status_code=400, detail="动态验证码错误，启用失败")
    op.totp_enabled = True
    db.commit()
    return {"ok": True, "enabled": True}


@router.post("/totp/disable")
def totp_disable(
    data: TotpDisableIn, db: Session = Depends(get_db), op: Operator = Depends(get_current_operator)
):
    """解绑 TOTP：需密码二次确认，解绑后同时清空密钥。"""
    if not verify_password(data.password, op.password_hash):
        raise HTTPException(status_code=401, detail="密码错误")
    op.totp_enabled = False
    op.totp_secret_enc = ""
    db.commit()
    return {"ok": True, "enabled": False}

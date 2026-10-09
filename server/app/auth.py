"""认证：bcrypt 密码哈希 + JWT + FastAPI 依赖。"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    """解析 token 返回用户 id；无效或过期返回 None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    user_id = decode_token(creds.credentials)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def get_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


def get_advideo_access(user: User = Depends(get_current_user)) -> User:
    """电商广告片的可见性闸门（已上线，默认全员可用）。

    settings.advideo_admin_only=False（默认，2026-10-09 拍板放开）时所有登录用户放行；
    True 时只有白名单账号可用，其余登录用户 403、匿名 401（由 get_current_user 抛出）。
    """
    if not getattr(settings, "advideo_admin_only", False):
        return user
    raw = str(getattr(settings, "advideo_admin_emails", "") or "")
    emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
    ok = (user.email or "").strip().lower() in emails if emails else user.role == "admin"
    if not ok:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "广告片功能暂未对外开放")
    return user

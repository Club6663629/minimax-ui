"""认证路由：注册 / 登录 / 当前用户。"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth import create_token, get_current_user, hash_password, verify_password
from ..config import settings
from ..database import get_db
from ..legal import record_consents
from ..models import User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..services.billing import add_credits

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, username=user.username,
        role=user.role, credits=user.credits, created_at=user.created_at,
    )


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "该邮箱已注册")
    user = User(
        email=body.email.lower(),
        username=body.username,
        password_hash=hash_password(body.password),
        credits=0,
    )
    db.add(user)
    db.flush()
    if settings.signup_bonus > 0:
        add_credits(db, user, settings.signup_bonus, "signup", note="注册赠送")
    # V2 协议留痕（只记录，不拦截）：勾选即合并同意《用户协议》《隐私政策》《AI 标识说明》
    record_consents(db, user.id, body.agreement_versions, request, entry="register")
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_token(user.id), user=_user_out(user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    return TokenOut(access_token=create_token(user.id), user=_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_out(user)

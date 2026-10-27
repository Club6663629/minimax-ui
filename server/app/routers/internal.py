"""平台侧开通接口（SAI 租户开户用，共享密钥鉴权，2026-10-26）。

背景：新开一个 SAI 租户时，需要同步在「海白菜」侧开户、赠送积分，并拿到一个
**长期不过期的 API Key**，供租户容器内的数字员工直接调用文生视频（JWT 只有 7 天，
不适合机器长期调用）。

鉴权：请求头 `X-Platform-Secret` 必须等于 settings.platform_provision_secret；
      该配置留空 => 接口整体关闭（403），不影响其他功能。

幂等：
- 同一 email 重复调用不会重复建号；
- 赠送积分按流水备注标记判定，重复调用不会重复赠送；
- API Key 默认只在首次签发时返回明文（库里只存 sha256），需要换新 key 传 rotate_key=true，
  旧 key 立即吊销。
"""
import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..config import settings
from ..database import get_db
from ..models import ApiKey, CreditLog, User
from ..services.billing import add_credits

router = APIRouter(prefix="/api/internal", tags=["internal"])

# 幂等标记：同一租户只赠送一次
GRANT_NOTE = "平台开通赠送"


class TenantProvisionIn(BaseModel):
    tenant: str = Field(min_length=1, max_length=60)
    email: str = ""          # 缺省 <tenant>@ai4ss.com
    username: str = ""       # 缺省 tenant
    credits_grant: int = Field(default=0, ge=0, le=100000)
    rotate_key: bool = False


def _require_secret(x_platform_secret: str = Header(default="")) -> None:
    expected = (settings.platform_provision_secret or "").strip()
    if not expected:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "平台开通接口未启用（PLATFORM_PROVISION_SECRET 为空）",
        )
    if not secrets.compare_digest(x_platform_secret, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "平台密钥不匹配")


def _issue_key(db: Session, user: User, tenant: str, rotate: bool):
    """签发长期 Key。已有有效 key 且未要求轮换 => (None, False)。"""
    active = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.id.desc())
        .first()
    )
    if active is not None and not rotate:
        return None, False
    if active is not None:
        active.revoked_at = datetime.now()
    plain = "hk_" + secrets.token_urlsafe(32)
    db.add(
        ApiKey(
            user_id=user.id,
            key_hash=hashlib.sha256(plain.encode()).hexdigest(),
            prefix=plain[:10],
            label=f"platform:{tenant}",
        )
    )
    return plain, True


@router.post("/tenants")
def provision_tenant(
    body: TenantProvisionIn,
    _: None = Depends(_require_secret),
    db: Session = Depends(get_db),
):
    """开户 + 赠送积分 + 签发长期 Key（幂等）。"""
    email = (body.email or f"{body.tenant}@ai4ss.com").strip().lower()
    username = (body.username or body.tenant).strip()[:60]

    user = db.query(User).filter(User.email == email).first()
    created = False
    if user is None:
        user = User(
            email=email,
            username=username,
            password_hash=hash_password(secrets.token_urlsafe(24)),
            credits=0,
        )
        db.add(user)
        db.flush()
        created = True

    granted = 0
    if body.credits_grant > 0:
        note = f"{GRANT_NOTE}（{body.tenant}）"
        done = (
            db.query(CreditLog)
            .filter(CreditLog.user_id == user.id, CreditLog.note == note)
            .first()
        )
        if done is None:
            add_credits(db, user, body.credits_grant, "grant", note=note)
            granted = body.credits_grant

    api_key, key_rotated = _issue_key(db, user, body.tenant, body.rotate_key)
    db.commit()
    db.refresh(user)
    return {
        "tenant": body.tenant,
        "user_id": user.id,
        "email": user.email,
        "created": created,
        "granted": granted,
        "credits": user.credits,
        "api_key": api_key,
        "key_rotated": key_rotated,
    }

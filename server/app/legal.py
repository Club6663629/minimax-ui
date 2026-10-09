"""法律文本（用户协议 V2 · 体验优先版）常量与同意留痕工具。

设计原则（对应 V2 方案 §3）：**只记录，不拦截**。
- 不对外提供任何"协议未同意"的拒绝依赖（无 428、无拦截）；
- 仅在注册与显式 ack 时写入留痕，供合规备查。
"""
from datetime import datetime
from typing import Iterable, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AgreementConsent

VERSION = "1.0"
UPDATED = "2026-09-30"
EFFECTIVE = "2026-09-30"

# 文档 key -> 标题 / 静态页路径（静态页由前端 web/public/legal/ 提供）
DOCS: list[dict] = [
    {"key": "ua", "title": "用户协议", "path": "/legal/user-agreement.html"},
    {"key": "pp", "title": "隐私政策", "path": "/legal/privacy-policy.html"},
    {"key": "ai", "title": "AI 标识说明", "path": "/legal/ai-labeling.html"},
    {"key": "credits", "title": "积分与计费规则", "path": "/legal/credits-rules.html"},
    {"key": "api", "title": "API 服务条款", "path": "/legal/api-terms.html"},
]
DOC_KEYS = tuple(d["key"] for d in DOCS)

# 注册页那 1 个合并勾选框覆盖的文档（V2 §4）
REGISTER_AGREED_KEYS = ("ua", "pp", "ai")

ALLOWED_ENTRIES = ("register", "login", "update_notice")


def client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def user_agent(request: Optional[Request]) -> str:
    if request is None:
        return ""
    return request.headers.get("user-agent", "")[:255]


def record_consents(
    db: Session,
    user_id: int,
    versions: Optional[dict] = None,
    request: Optional[Request] = None,
    entry: str = "register",
    keys: Optional[Iterable[str]] = None,
) -> int:
    """写入同意留痕；返回写入条数。

    versions 缺省（旧客户端 / 未传）时，按 keys（默认注册合并覆盖的 3 份）以当前版本记录，
    保证"注册即同意"这一事实有留痕；**任何情况下都不抛出异常、不阻断主流程**。
    """
    entry = entry if entry in ALLOWED_ENTRIES else "register"
    keys = tuple(keys) if keys is not None else (
        REGISTER_AGREED_KEYS if entry == "register" else DOC_KEYS
    )
    versions = versions or {}
    ip = client_ip(request)
    ua = user_agent(request)
    n = 0
    try:
        for k in keys:
            db.add(AgreementConsent(
                user_id=user_id,
                doc_type=k,
                version=str(versions.get(k) or VERSION)[:16],
                action="accept" if entry != "update_notice" else "update_notice",
                entry=entry,
                ip=ip,
                user_agent=ua,
                created_at=datetime.now(),
            ))
            n += 1
    except Exception:  # 留痕失败绝不影响注册/登录
        n = 0
    return n

"""法律文本接口（V2 体验优先版）：版本查询 + 同意留痕。**不含任何拦截。**"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..legal import DOCS, EFFECTIVE, UPDATED, VERSION, record_consents
from ..models import User
from ..schemas import LegalAckIn, LegalCurrentOut, LegalDocOut

router = APIRouter(prefix="/api/legal", tags=["legal"])


@router.get("/current", response_model=LegalCurrentOut)
def current() -> LegalCurrentOut:
    """当前生效的法律文本版本与目录（前端启动时静默比对，用于决定是否显示可关闭横幅）。"""
    return LegalCurrentOut(
        version=VERSION,
        updated=UPDATED,
        effective=EFFECTIVE,
        docs=[LegalDocOut(key=d["key"], title=d["title"], path=d["path"], version=VERSION) for d in DOCS],
    )


@router.post("/ack")
def ack(
    body: LegalAckIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """显式留痕：用户关闭"协议更新"横幅时调用。只记录，不影响任何功能可用性。"""
    n = record_consents(db, user.id, body.versions, request, entry=body.entry)
    db.commit()
    return {"ok": True, "recorded": n, "version": VERSION}

"""积分路由：余额 / 流水台账 / 充值套餐 / 兑换码充值。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import CreditLog, RedeemCode, User
from ..schemas import CreditLogOut, RedeemIn
from ..services.billing import add_credits

router = APIRouter(prefix="/api/credits", tags=["credits"])


@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"credits": user.credits}


@router.get("/logs", response_model=list[CreditLogOut])
def credit_logs(
    limit: int = Query(default=100, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logs = (
        db.query(CreditLog)
        .filter(CreditLog.user_id == user.id)
        .order_by(CreditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        CreditLogOut(
            id=l.id, amount=l.amount, type=l.type, note=l.note,
            task_id=l.task_id, created_at=l.created_at,
        )
        for l in logs
    ]


@router.post("/redeem")
def redeem(
    body: RedeemIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    code = db.query(RedeemCode).filter(RedeemCode.code == body.code.strip().upper()).first()
    if code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "兑换码不存在")
    if code.status != "unused":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "兑换码已被使用")

    code.status = "used"
    code.used_by = user.id
    code.used_at = datetime.now()
    add_credits(db, user, code.value, "redeem", note=f"兑换码充值 {code.code}")
    db.commit()
    return {"credits": user.credits, "added": code.value}

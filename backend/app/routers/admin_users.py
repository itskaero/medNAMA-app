"""Admin: accounts and invite codes.

    GET    /api/admin/users                      everyone, with last activity and questions answered
    PATCH  /api/admin/users/{id}                 {role?, is_active?}
    POST   /api/admin/users/{id}/reset-password  a new one-time temporary password (shown once)
    GET    /api/admin/invites                    invite codes and their use
    POST   /api/admin/invites                    {label?, max_uses?, days?} -> a new code
    DELETE /api/admin/invites/{id}
"""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin
from app.deps import get_db
from app.models import AnswerEvent, InviteCode, User

router = APIRouter()

_WORDS = ("amber", "basil", "cedar", "delta", "ember", "flint", "grove", "harbor", "iris", "juniper", "kestrel",
          "lotus", "maple", "nova", "opal", "pine", "quartz", "raven", "sage", "tidal", "umber", "violet", "willow")


def _new_code() -> str:
    """Easy to read out or type on a phone: 'maple-4821'."""
    return f"{secrets.choice(_WORDS)}-{secrets.randbelow(9000) + 1000}"


@router.get("/api/admin/users")
def list_users(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    activity = dict(db.query(AnswerEvent.user_id, func.count(AnswerEvent.id)).group_by(AnswerEvent.user_id).all())
    last = dict(db.query(AnswerEvent.user_id, func.max(AnswerEvent.created_at)).group_by(AnswerEvent.user_id).all())
    return [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active is not False,
             "created_at": u.created_at, "invited_with": u.invited_with,
             "answered": int(activity.get(u.id, 0)), "last_active": last.get(u.id)}
            for u in db.query(User).order_by(User.id)]


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, req: UserPatch, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")
    if user.id == admin.id and (req.is_active is False or (req.role and req.role != "admin")):
        raise HTTPException(status_code=400, detail="You can't disable or demote your own account.")
    if req.role is not None:
        if req.role not in ("student", "admin"):
            raise HTTPException(status_code=400, detail="Role must be student or admin.")
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    db.commit()
    return {"id": user.id, "role": user.role, "is_active": user.is_active}


@router.post("/api/admin/users/{user_id}/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")
    temp = f"{secrets.choice(_WORDS)}-{secrets.token_hex(3)}"
    user.password_hash = hash_password(temp)
    db.commit()
    return {"username": user.username, "temporary_password": temp,
            "note": "Shown once. The student should change it after signing in."}


@router.get("/api/admin/invites")
def list_invites(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    out = []
    for c in db.query(InviteCode).order_by(InviteCode.id.desc()):
        exp = c.expires_at.replace(tzinfo=timezone.utc) if c.expires_at and c.expires_at.tzinfo is None else c.expires_at
        out.append({"id": c.id, "code": c.code, "label": c.label, "uses": c.uses, "max_uses": c.max_uses,
                    "expires_at": c.expires_at, "created_at": c.created_at,
                    "usable": c.uses < c.max_uses and (exp is None or exp > now)})
    return out


class InviteNew(BaseModel):
    label: str | None = None
    max_uses: int = 1
    days: int | None = 30


@router.post("/api/admin/invites")
def create_invite(req: InviteNew, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if not 1 <= req.max_uses <= 500:
        raise HTTPException(status_code=400, detail="An invite code can be used 1 to 500 times.")
    code = _new_code()
    while db.query(InviteCode).filter(InviteCode.code == code).first():
        code = _new_code()
    invite = InviteCode(code=code, label=(req.label or "").strip()[:80] or None, max_uses=req.max_uses,
                        expires_at=(datetime.now(timezone.utc) + timedelta(days=req.days)) if req.days else None,
                        created_by=admin.id)
    db.add(invite)
    db.commit()
    return {"id": invite.id, "code": invite.code, "max_uses": invite.max_uses, "expires_at": invite.expires_at}


@router.delete("/api/admin/invites/{invite_id}")
def delete_invite(invite_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    invite = db.get(InviteCode, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="No such invite code.")
    db.delete(invite)
    db.commit()
    return {"deleted": invite_id}

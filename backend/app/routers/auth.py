"""Sign-in, registration and the session cookie.

Moved verbatim from app/main.py (routes keep their paths)."""

import os
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.config import settings
from app.models import InviteCode, User
from app.auth import hash_password, verify_password, create_access_token, get_current_user, rate_limiter
from app.deps import UserRegister, get_db

router = APIRouter()

# ======================== AUTHENTICATION ROUTING ========================

@router.post(
    "/api/auth/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter(limit=5, window=60))],
)
def register_user(user_in: UserRegister, db: Session = Depends(get_db)):
    """Registers a new user (defaults to student role)."""
    # Check duplicate
    existing = db.query(User).filter(User.username == user_in.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is already taken.")

    if user_in.role not in ["student", "admin"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role specification.")
    if len(user_in.username.strip()) < 3 or len(user_in.password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Pick a username of at least 3 characters and a password of at least 6.")

    mode = (settings.registration_mode or "invite").lower()
    invite = None
    if mode == "closed":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Sign-up is closed. Ask the admin to create your account.")
    if mode == "invite":
        from datetime import datetime, timezone
        code = (user_in.invite_code or "").strip()
        invite = db.query(InviteCode).filter(InviteCode.code == code).with_for_update().first() if code else None
        exp = invite.expires_at if invite is not None else None
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        expired = exp is not None and exp < datetime.now(timezone.utc)
        if invite is None or invite.uses >= invite.max_uses or expired:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="That invite code isn't valid (wrong, used up or expired). Ask the admin for one.")
        invite.uses += 1

    hashed_pw = hash_password(user_in.password)
    user = User(username=user_in.username.strip(), password_hash=hashed_pw, role="student",
                invited_with=invite.code if invite else None)
    db.add(user)
    db.commit()

    return {"message": "Registration successful.", "username": user.username, "role": user.role}

def _set_session_cookie(response: Response, token: str) -> None:
    """The session: an HttpOnly, SameSite=Lax cookie that lives as long as the token (auth.ACCESS_TOKEN_EXPIRE_MINUTES).
    Secure is off because the NAS serves plain HTTP on the LAN; turn it on behind HTTPS (COOKIE_SECURE=true)."""
    from app.auth import ACCESS_TOKEN_EXPIRE_MINUTES

    response.set_cookie(key="access_token", value=token, httponly=True, samesite="lax",
                        secure=os.getenv("COOKIE_SECURE", "").lower() == "true",
                        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)

@router.post(
    "/api/auth/login",
    dependencies=[Depends(rate_limiter(limit=10, window=60))],
)
def login_user(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Authenticates credentials, sets HttpOnly cookie, and returns credentials."""
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password."
        )
    if user.is_active is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This account is disabled. Ask the admin to re-enable it.")

    _set_session_cookie(response, create_access_token(data={"sub": user.username}))
    # The token lives only in the HttpOnly cookie: page scripts (and anything injected into them) never see it.
    return {"role": user.role, "username": user.username}

class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.post("/api/auth/change-password", dependencies=[Depends(rate_limiter(limit=10, window=60))])
def change_password(req: ChangePassword, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Change your own password (the current one is required)."""
    if not verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The current password is wrong.")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use at least 6 characters.")
    current_user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"message": "Password changed."}


@router.get("/api/auth/registration")
def registration_mode():
    """How sign-up works here, for the sign-up form ("invite" shows the code field)."""
    return {"mode": (settings.registration_mode or "invite").lower()}


@router.post("/api/auth/logout")
def logout_user(response: Response):
    """Logs out current session by deleting the HttpOnly cookie."""
    response.delete_cookie(key="access_token")
    return {"message": "Logged out successfully."}

@router.get("/api/auth/me")
def get_user_profile(request: Request, response: Response, current_user: User = Depends(get_current_user)):
    """Returns profile information for the authenticated user.

    A browser still signed in the old way (token in localStorage, sent as a header) gets the HttpOnly cookie
    here, so it moves to cookie-only sessions without having to sign in again."""
    header = request.headers.get("Authorization", "")
    if "access_token" not in request.cookies and header.startswith("Bearer ") and header.count(".") == 2:
        _set_session_cookie(response, header.split(" ", 1)[1])
    from app.routers.pages import viewer_allowed
    return {"username": current_user.username, "role": current_user.role,
            "can_view_pages": viewer_allowed(current_user)}

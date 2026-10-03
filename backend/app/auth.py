"""Authentication helpers for JWT tokens, password hashing, and role checks."""

import logging
from datetime import datetime, timedelta
from typing import Any

import jwt
import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

# The signing secret is JWT_SECRET (settings.jwt_secret). This used to read a setting that does not exist, so every
# token was signed with a hard-coded string that anyone could use to forge any account, admin included.
WEAK_JWT_SECRETS = {"", "dev-secret-change-in-production", "change-this-jwt-secret", "medrag_secret_key_change_me_in_prod"}
JWT_SECRET_KEY = settings.jwt_secret
if JWT_SECRET_KEY in WEAK_JWT_SECRETS or len(JWT_SECRET_KEY) < 24:
    logger.critical("JWT_SECRET is missing, short or a default value: anyone who knows it can sign in as any user. "
                    "Set a long random JWT_SECRET in the environment.")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day expiration for convenience


from collections import defaultdict
import time

# Simple in-memory rate limiting dictionary (IP -> list of request timestamps)
_rate_limit_cache = defaultdict(list)


def rate_limiter(limit: int, window: int):
    """Enforces API rate limits per client IP (in-memory sliding window)."""
    def dependency(request: Request):
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.time()
        
        # Filter out timestamps older than the sliding window
        active_requests = [t for t in _rate_limit_cache[client_ip] if now - t < window]
        
        if len(active_requests) >= limit:
            logger.warning(f"Rate limit exceeded for IP: {client_ip} (limit: {limit} requests per {window}s)")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later."
            )
            
        active_requests.append(now)
        _rate_limit_cache[client_ip] = active_requests
        
    return dependency


class OAuth2PasswordBearerWithQuery(OAuth2PasswordBearer):
    async def __call__(self, request: Request) -> str | None:
        # 1. Check cookies first (HttpOnly access_token)
        cookie_auth = request.cookies.get("access_token")
        if cookie_auth:
            return cookie_auth

        # 2. Check authorization header next
        header_auth = request.headers.get("Authorization")
        if header_auth and header_auth.startswith("Bearer "):
            return header_auth.split(" ")[1]
        
        # 3. Fallback to token query parameter (for media tags)
        token_param = request.query_params.get("token")
        if token_param:
            return token_param
            
        return await super().__call__(request)


oauth2_scheme = OAuth2PasswordBearerWithQuery(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify raw password against bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Generate JWT token with claims payload."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """Validate token and resolve user from DB."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    if user.is_active is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled.")
    return user


class RoleChecker:
    """Enforces role assertions on FastAPI endpoints."""
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role not in self.allowed_roles:
            logger.warning(f"Unauthorized access attempt by User '{user.username}' (role: {user.role})")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource"
            )
        return user


# Common role checks
require_admin = RoleChecker(["admin"])
require_student_or_admin = RoleChecker(["student", "admin"])

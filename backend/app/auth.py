from datetime import datetime, timedelta, timezone
from typing import Annotated
import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import settings
from app.db import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码长度不能超过 72 字节")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        if len(password.encode("utf-8")) > 72:
            return False
        return bcrypt.checkpw(password.encode(), encoded.encode())
    except (ValueError, TypeError):
        return False


def make_token(user: User) -> str:
    secret = settings().jwt_secret
    if len(secret) < 32:
        raise HTTPException(503, "服务端 JWT_SECRET 未配置或长度不足")
    return jwt.encode({"sub": str(user.id), "ver": user.token_version, "exp": datetime.now(timezone.utc) + timedelta(minutes=settings().jwt_minutes)}, secret, algorithm="HS256")


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], db: Annotated[Session, Depends(get_db)]) -> User:
    if not credentials or len(settings().jwt_secret) < 32:
        raise HTTPException(401, "请先登录")
    try:
        payload = jwt.decode(credentials.credentials, settings().jwt_secret, algorithms=["HS256"], options={"require": ["sub", "ver", "exp"]})
        user = db.scalar(select(User).where(User.id == int(payload["sub"]), User.active.is_(True)))
        if user and payload["ver"] != user.token_version:
            user = None
    except (jwt.PyJWTError, ValueError, KeyError):
        user = None
    if not user:
        raise HTTPException(401, "登录已失效")
    return user


def staff(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role not in ("admin", "staff"):
        raise HTTPException(403, "没有操作权限")
    return user


def admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可以操作")
    return user

# app/core/security.py (Updated)
from datetime import datetime, timedelta
from typing import Any, Optional, Union, List
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer
import asyncpg
from app.core.database import get_db_connection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Define available roles
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"

# Define role permissions
ROLE_PERMISSIONS = {
    ROLE_USER: ["read_own", "write_own"],
    ROLE_ADMIN: ["read_own", "write_own", "read_all", "write_all", "manage_orders", "manage_settings"],
    ROLE_SUPER_ADMIN: ["read_own", "write_own", "read_all", "write_all", "manage_orders", 
                      "manage_settings", "manage_users", "manage_roles"]
}

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), conn: asyncpg.Connection = Depends(get_db_connection)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.JWTError:
        raise credentials_exception
    
    user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", int(user_id))
    if user is None:
        raise credentials_exception
    
    return dict(user)

async def get_current_active_user(current_user: dict = Depends(get_current_user)):
    if not current_user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Inactive user account"
        )
    return current_user

def has_permission(required_permissions: List[str]):
    async def permission_checker(current_user: dict = Depends(get_current_user)):
        user_role = current_user.get("role", ROLE_USER)
        
        # Get permissions for the user's role
        user_permissions = ROLE_PERMISSIONS.get(user_role, [])
        
        # Check if user has all required permissions
        for permission in required_permissions:
            if permission not in user_permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not enough permissions. Admin access required."
                )
        
        return current_user
    
    return permission_checker

async def get_current_admin_user(
    current_user: dict = Security(has_permission(["read_all", "write_all"]))
):
    if current_user.get("role") not in [ROLE_ADMIN, ROLE_SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user

async def get_super_admin_user(
    current_user: dict = Security(has_permission(["manage_users", "manage_roles"]))
):
    if current_user.get("role") != ROLE_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )
    return current_user
# app/routes/auth.py (Updated)
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from datetime import timedelta
import asyncpg
from app.core.database import get_db_connection
from app.core.security import verify_password, create_access_token, get_password_hash
from app.core.config import settings
from app.models.auth import Token, LoginRequest, TokenWithRole
from app.models.user import UserCreate, UserResponse
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/register", response_model=TokenWithRole)
async def register(user: UserCreate, conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        # Check if user already exists
        existing_user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", user.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )
        
        # Create new user
        hashed_password = get_password_hash(user.password)
        user_id = await conn.fetchval("""
            INSERT INTO users (email, password_hash, first_name, last_name) 
            VALUES ($1, $2, $3, $4) 
            RETURNING id
        """, user.email, hashed_password, user.first_name, user.last_name)
        
        # Get created user
        created_user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        
        # Generate token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            subject=user_id, expires_delta=access_token_expires
        )
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "role": created_user["role"],
            "user_id": user_id
        }
    except Exception as e:
        logger.error(f"Registration error: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating user"
        )

@router.post("/login", response_model=TokenWithRole)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        # Find user
        user = await conn.fetchrow("SELECT * FROM users WHERE email = $1", form_data.username)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Verify password
        if not verify_password(form_data.password, user["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Check if user is active
        if not user["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is disabled",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Generate token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            subject=user["id"], expires_delta=access_token_expires
        )
        
        return {
            "access_token": access_token, 
            "token_type": "bearer",
            "role": user["role"],
            "user_id": user["id"]
        }
    except Exception as e:
        logger.error(f"Login error: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )
# app/routes/users.py
from fastapi import APIRouter, HTTPException, Depends, status
import asyncpg
from app.core.database import get_db_connection
from app.core.security import get_current_active_user, get_password_hash
from app.models.user import UserUpdate, UserResponse
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/me", response_model=UserResponse)
async def get_user_me(current_user: dict = Depends(get_current_active_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "first_name": current_user["first_name"],
        "last_name": current_user["last_name"],
        "role": current_user["role"],
        "is_active": current_user["is_active"],
        "created_at": current_user["created_at"]
    }

@router.put("/me", response_model=UserResponse)
async def update_user_me(
    user_update: UserUpdate, 
    current_user: dict = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    try:
        # Build update query dynamically based on provided fields
        update_values = []
        update_params = []
        param_index = 1
        
        if user_update.email is not None:
            # Check if email already exists for another user
            existing_user = await conn.fetchrow(
                "SELECT id FROM users WHERE email = $1 AND id != $2",
                user_update.email, current_user["id"]
            )
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )
            update_values.append(f"email = ${param_index}")
            update_params.append(user_update.email)
            param_index += 1
        
        if user_update.first_name is not None:
            update_values.append(f"first_name = ${param_index}")
            update_params.append(user_update.first_name)
            param_index += 1
        
        if user_update.last_name is not None:
            update_values.append(f"last_name = ${param_index}")
            update_params.append(user_update.last_name)
            param_index += 1
        
        if not update_values:
            # No fields to update
            return get_user_me(current_user)
        
        # Add updated_at timestamp
        update_values.append(f"updated_at = CURRENT_TIMESTAMP")
        
        # Build and execute query
        query = f"""
            UPDATE users 
            SET {', '.join(update_values)} 
            WHERE id = ${param_index} 
            RETURNING *
        """
        update_params.append(current_user["id"])
        
        updated_user = await conn.fetchrow(query, *update_params)
        
        return {
            "id": updated_user["id"],
            "email": updated_user["email"],
            "first_name": updated_user["first_name"],
            "last_name": updated_user["last_name"],
            "role": updated_user["role"],
            "is_active": updated_user["is_active"],
            "created_at": updated_user["created_at"]
        }
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user"
        )
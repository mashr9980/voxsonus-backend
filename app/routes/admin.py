# app/routes/admin.py (Updated)
from fastapi import APIRouter, HTTPException, Depends, Response, status, Query, BackgroundTasks, File, UploadFile
import asyncpg
import json
import os

import fastapi
from app.core.config import Settings
from app.core.database import get_db_connection, log_activity
from app.core.security import get_current_admin_user, get_super_admin_user, get_password_hash
from app.models.user import UserUpdate, UserResponse
from app.models.admin import (
    SystemSettingUpdate, SystemSettingResponse, AdminOrderUpdate,
    AdminStats, AdminUserResponse, AdminOrderListResponse, 
    AdminLogResponse, ProcessingRequest
)
from app.models.order import OrderStatus, OrderDetailResponse, PaymentStatus, VideoStatus
from app.services.subtitle_processor import process_order
from typing import List, Optional
from datetime import date, datetime, timedelta
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# System settings
@router.get("/settings", response_model=List[SystemSettingResponse])
async def get_system_settings(
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        settings = await conn.fetch("""
            SELECT s.*, u.email as updated_by_email 
            FROM system_settings s
            LEFT JOIN users u ON s.updated_by = u.id
            ORDER BY s.key
        """)
        
        result = []
        for setting in settings:
            setting_dict = dict(setting)
            setting_dict["updated_by"] = setting.get("updated_by_email")
            result.append(setting_dict)
            
        return result
    except Exception as e:
        logger.error(f"Error fetching system settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch system settings"
        )

@router.put("/settings/{key}", response_model=SystemSettingResponse)
async def update_system_setting(
    key: str,
    setting_update: SystemSettingUpdate,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Check if setting exists
        existing_setting = await conn.fetchrow(
            "SELECT * FROM system_settings WHERE key = $1", key
        )
        
        if not existing_setting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting with key '{key}' not found"
            )
        
        # Update setting
        updated_setting = await conn.fetchrow("""
            UPDATE system_settings 
            SET value = $1, description = $2, updated_at = CURRENT_TIMESTAMP, updated_by = $3
            WHERE key = $4
            RETURNING *
        """, setting_update.value, setting_update.description, current_user["id"], key)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "update_setting", 
            "system_settings", 
            key, 
            {"old_value": existing_setting["value"], "new_value": setting_update.value}
        )
        
        result = dict(updated_setting)
        
        # Get updater email
        updater = await conn.fetchrow("SELECT email FROM users WHERE id = $1", current_user["id"])
        if updater:
            result["updated_by"] = updater["email"]
        
        return result
    except Exception as e:
        logger.error(f"Error updating system setting: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update system setting"
        )

# Admin stats and dashboard
@router.get("/stats", response_model=AdminStats)
async def get_admin_stats(
    days_back: int = 30,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Get today's date in UTC
        today = datetime.utcnow().date()
        today_start = datetime.combine(today, datetime.min.time())
        period_start = today_start - timedelta(days=days_back)
        
        # Get total users
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        
        # Get total orders
        total_orders = await conn.fetchval("SELECT COUNT(*) FROM orders")
        
        # Get total completed orders
        total_completed_orders = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE status = $1",
            OrderStatus.COMPLETED
        )
        
        # Get total revenue
        total_revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(total_amount), 0) FROM orders WHERE payment_status = $1",
            PaymentStatus.PAID
        )
        
        # Get orders created today
        orders_today = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE created_at >= $1",
            today_start
        )
        
        # Get revenue from today's orders
        revenue_today = await conn.fetchval(
            """
            SELECT COALESCE(SUM(total_amount), 0) 
            FROM orders 
            WHERE created_at >= $1 AND payment_status = $2
            """,
            today_start, PaymentStatus.PAID
        )
        
        # Get period stats
        period_orders = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE created_at >= $1",
            period_start
        )
        
        period_revenue = await conn.fetchval(
            """
            SELECT COALESCE(SUM(total_amount), 0) 
            FROM orders 
            WHERE created_at >= $1 AND payment_status = $2
            """,
            period_start, PaymentStatus.PAID
        )
        
        # Get orders by status
        orders_by_status = await conn.fetch(
            """
            SELECT status, COUNT(*) as count 
            FROM orders 
            GROUP BY status
            """
        )
        
        status_counts = {row["status"]: row["count"] for row in orders_by_status}
        
        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "total_completed_orders": total_completed_orders,
            "total_revenue": float(total_revenue),
            "orders_today": orders_today,
            "revenue_today": float(revenue_today),
            "period_orders": period_orders,
            "period_revenue": float(period_revenue),
            "orders_by_status": status_counts
        }
    except Exception as e:
        logger.error(f"Error fetching admin stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch admin statistics"
        )

# User management
@router.get("/users", response_model=List[AdminUserResponse])
async def get_users(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    role: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        query_params = []
        query = """
            SELECT 
                u.*,
                COUNT(o.id) AS orders_count,
                COALESCE(SUM(o.total_amount) FILTER (WHERE o.payment_status = 'paid'), 0) AS total_spent
            FROM users u
            LEFT JOIN orders o ON u.id = o.user_id
        """
        
        # Add search condition if provided
        where_clauses = []
        param_index = 1
        
        if search:
            where_clauses.append(f"(u.email ILIKE ${param_index} OR u.first_name ILIKE ${param_index} OR u.last_name ILIKE ${param_index})")
            query_params.append(f"%{search}%")
            param_index += 1
        
        if role:
            where_clauses.append(f"u.role = ${param_index}")
            query_params.append(role)
            param_index += 1
        
        # Add where clauses if any
        if where_clauses:
            query += f" WHERE {' AND '.join(where_clauses)}"
        
        # Add group by, order by, and limit
        query += """
            GROUP BY u.id
            ORDER BY u.id
            LIMIT $%d OFFSET $%d
        """ % (param_index, param_index + 1)
        
        query_params.extend([limit, skip])
        
        users = await conn.fetch(query, *query_params)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "view_users", 
            "users", 
            None,
            {"search": search, "role": role, "count": len(users)}
        )
        
        return [dict(user) for user in users]
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch users"
        )

@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        user = await conn.fetchrow("""
            SELECT 
                u.*,
                COUNT(o.id) AS orders_count,
                COALESCE(SUM(o.total_amount) FILTER (WHERE o.payment_status = 'paid'), 0) AS total_spent
            FROM users u
            LEFT JOIN orders o ON u.id = o.user_id
            WHERE u.id = $1
            GROUP BY u.id
        """, user_id)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "view_user", 
            "users", 
            user_id,
            None
        )
        
        return dict(user)
    except Exception as e:
        logger.error(f"Error fetching user: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch user"
        )

@router.put("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Check if user exists
        existing_user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not existing_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Super admin check - only super admin can modify admin accounts
        if existing_user["role"] in ["admin", "super_admin"] and current_user["role"] != "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super admins can modify admin accounts"
            )
        
        # Build update query dynamically based on provided fields
        update_values = []
        update_params = []
        param_index = 1
        
        if user_update.email is not None:
            # Check if email already exists for another user
            existing_email = await conn.fetchrow(
                "SELECT id FROM users WHERE email = $1 AND id != $2",
                user_update.email, user_id
            )
            if existing_email:
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
        
        if user_update.is_active is not None:
            update_values.append(f"is_active = ${param_index}")
            update_params.append(user_update.is_active)
            param_index += 1
        
        if not update_values:
            # No fields to update, return current user
            return await get_user(user_id, conn, current_user)
        
        # Add updated_at timestamp
        update_values.append(f"updated_at = CURRENT_TIMESTAMP")
        
        # Build and execute query
        query = f"""
            UPDATE users 
            SET {', '.join(update_values)} 
            WHERE id = ${param_index} 
            RETURNING *
        """
        update_params.append(user_id)
        
        updated_user = await conn.fetchrow(query, *update_params)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "update_user", 
            "users", 
            user_id,
            {"updated_fields": [k for k in user_update.dict(exclude_unset=True).keys()]}
        )
        
        # Get updated user with orders count and total spent
        result = await conn.fetchrow("""
            SELECT 
                u.*,
                COUNT(o.id) AS orders_count,
                COALESCE(SUM(o.total_amount) FILTER (WHERE o.payment_status = 'paid'), 0) AS total_spent
            FROM users u
            LEFT JOIN orders o ON u.id = o.user_id
            WHERE u.id = $1
            GROUP BY u.id
        """, user_id)
        
        return dict(result)
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user"
        )

@router.put("/users/{user_id}/role", response_model=AdminUserResponse)
async def update_user_role(
    user_id: int,
    role: str,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_super_admin_user)  # Only super admins can change roles
):
    try:
        # Check if user exists
        existing_user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not existing_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Validate role
        if role not in ["user", "admin", "super_admin"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role. Must be 'user', 'admin', or 'super_admin'"
            )
        
        # Prevent changing own role
        if user_id == current_user["id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role"
            )
        
        # Update role
        updated_user = await conn.fetchrow("""
            UPDATE users 
            SET role = $1, updated_at = CURRENT_TIMESTAMP
            WHERE id = $2
            RETURNING *
        """, role, user_id)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "update_user_role", 
            "users", 
            user_id,
            {"old_role": existing_user["role"], "new_role": role}
        )
        
        # Get updated user with orders count and total spent
        result = await conn.fetchrow("""
            SELECT 
                u.*,
                COUNT(o.id) AS orders_count,
                COALESCE(SUM(o.total_amount) FILTER (WHERE o.payment_status = 'paid'), 0) AS total_spent
            FROM users u
            LEFT JOIN orders o ON u.id = o.user_id
            WHERE u.id = $1
            GROUP BY u.id
        """, user_id)
        
        return dict(result)
    except Exception as e:
        logger.error(f"Error updating user role: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user role"
        )

# Order management
@router.get("/orders", response_model=AdminOrderListResponse)
async def get_orders(
    skip: int = 0,
    limit: int = 100,
    status: Optional[OrderStatus] = None,
    payment_status: Optional[PaymentStatus] = None,
    user_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Build where clause
        where_clauses = []
        query_params = []
        param_index = 1
        
        if status:
            where_clauses.append(f"o.status = ${param_index}")
            query_params.append(status)
            param_index += 1
        
        if payment_status:
            where_clauses.append(f"o.payment_status = ${param_index}")
            query_params.append(payment_status)
            param_index += 1
        
        if user_id:
            where_clauses.append(f"o.user_id = ${param_index}")
            query_params.append(user_id)
            param_index += 1
        
        if start_date:
            where_clauses.append(f"o.created_at >= ${param_index}")
            query_params.append(datetime.combine(start_date, datetime.min.time()))
            param_index += 1
        
        if end_date:
            where_clauses.append(f"o.created_at <= ${param_index}")
            query_params.append(datetime.combine(end_date, datetime.max.time()))
            param_index += 1
        
        # Build where clause string
        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        # Get total count
        count_query = f"SELECT COUNT(*) FROM orders o {where_clause}"
        total = await conn.fetchval(count_query, *query_params)
        
        # Get orders
        orders_query = f"""
            SELECT o.*, u.email as user_email
            FROM orders o
            JOIN users u ON o.user_id = u.id
            {where_clause}
            ORDER BY o.created_at DESC
            LIMIT ${param_index} OFFSET ${param_index + 1}
        """
        query_params.extend([limit, skip])
        
        orders = await conn.fetch(orders_query, *query_params)
        
        # Get videos for each order
        result_orders = []
        for order in orders:
            order_dict = dict(order)
            videos = await conn.fetch(
                "SELECT * FROM videos WHERE order_id = $1", order_dict["id"]
            )
            order_dict["videos"] = [dict(video) for video in videos]
            result_orders.append(order_dict)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "view_orders", 
            "orders", 
            None,
            {"count": len(result_orders), "filters": {
                "status": status,
                "payment_status": payment_status,
                "user_id": user_id
            }}
        )
        
        return {
            "total": total,
            "orders": result_orders
        }
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch orders"
        )

@router.get("/orders/{order_id}", response_model=OrderDetailResponse)
async def get_order_details(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Get order
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        order_dict = dict(order)
        
        # Get user info
        user = await conn.fetchrow("SELECT email FROM users WHERE id = $1", order["user_id"])
        if user:
            order_dict["user_email"] = user["email"]
        
        # Get subtitle config
        config = await conn.fetchrow(
            "SELECT * FROM subtitle_configs WHERE order_id = $1", order_id
        )
        if config:
            order_dict["subtitle_config"] = dict(config)
        
        # Get videos
        videos = await conn.fetch("SELECT * FROM videos WHERE order_id = $1", order_id)
        order_dict["videos"] = [dict(video) for video in videos]
        
        # Get subtitle files
        subtitle_files = await conn.fetch("""
            SELECT sf.* 
            FROM subtitle_files sf
            JOIN videos v ON sf.video_id = v.id
            WHERE v.order_id = $1
        """, order_id)
        order_dict["subtitle_files"] = [dict(file) for file in subtitle_files]
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "view_order", 
            "orders", 
            order_id,
            None
        )
        
        return order_dict
    except Exception as e:
        logger.error(f"Error fetching order details: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch order details"
        )

@router.put("/orders/{order_id}", response_model=OrderDetailResponse)
async def update_order_status(
    order_id: int,
    order_update: AdminOrderUpdate,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Check if order exists
        existing_order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not existing_order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Build update query dynamically based on provided fields
        update_values = []
        update_params = []
        param_index = 1
        
        if order_update.status is not None:
            update_values.append(f"status = ${param_index}")
            update_params.append(order_update.status)
            param_index += 1
            
            # If status is updated to completed or failed, update videos status as well
            if order_update.status in [OrderStatus.COMPLETED, OrderStatus.FAILED]:
                await conn.execute(
                    "UPDATE videos SET status = $1 WHERE order_id = $2",
                    order_update.status, order_id
                )
        
        if order_update.payment_status is not None:
            update_values.append(f"payment_status = ${param_index}")
            update_params.append(order_update.payment_status)
            param_index += 1
        
        if order_update.admin_notes is not None:
            update_values.append(f"admin_notes = ${param_index}")
            update_params.append(order_update.admin_notes)
            param_index += 1
        
        if not update_values:
            # No fields to update, return current order
            return await get_order_details(order_id, conn, current_user)
        
        # Add processed_by and updated_at
        update_values.append(f"processed_by = ${param_index}")
        update_params.append(current_user["id"])
        param_index += 1
        
        update_values.append(f"updated_at = CURRENT_TIMESTAMP")
        
        # Build and execute query
        query = f"""
            UPDATE orders 
            SET {', '.join(update_values)} 
            WHERE id = ${param_index} 
            RETURNING *
        """
        update_params.append(order_id)
        
        updated_order = await conn.fetchrow(query, *update_params)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "update_order", 
            "orders", 
            order_id,
            {"updated_fields": order_update.dict(exclude_unset=True)}
        )
        
        # Return full order details
        return await get_order_details(order_id, conn, current_user)
    except Exception as e:
        logger.error(f"Error updating order: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update order"
        )

@router.post("/orders/{order_id}/reprocess", response_model=OrderDetailResponse)
async def reprocess_order(
    order_id: int,
    processing_request: ProcessingRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Check if order exists
        existing_order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not existing_order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Update order status to processing
        await conn.execute("""
            UPDATE orders 
            SET status = $1, updated_at = CURRENT_TIMESTAMP, processed_by = $2, admin_notes = $3
            WHERE id = $4
        """, OrderStatus.PROCESSING, current_user["id"], 
            processing_request.notes, order_id)
        
        # Update all videos to processing
        await conn.execute("""
            UPDATE videos
            SET status = $1, updated_at = CURRENT_TIMESTAMP
            WHERE order_id = $2
        """, VideoStatus.PROCESSING, order_id)
        
        # Start processing in background
        background_tasks.add_task(process_order, order_id)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "reprocess_order", 
            "orders", 
            order_id,
            {"notes": processing_request.notes}
        )
        
        # Return updated order
        return await get_order_details(order_id, conn, current_user)
    except Exception as e:
        logger.error(f"Error reprocessing order: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reprocess order"
        )

@router.post("/orders/{order_id}/refund", response_model=OrderDetailResponse)
async def refund_order(
    order_id: int,
    processing_request: ProcessingRequest,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Check if order exists
        existing_order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not existing_order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Verify order is paid
        if existing_order["payment_status"] != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only paid orders can be refunded"
            )
        
        # In a real implementation, process refund via Stripe API here
        # For now, just update the status
        
        # Update order status
        await conn.execute("""
            UPDATE orders 
            SET payment_status = $1, updated_at = CURRENT_TIMESTAMP, processed_by = $2, admin_notes = $3
            WHERE id = $4
        """, PaymentStatus.REFUNDED, current_user["id"], 
            processing_request.notes, order_id)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "refund_order", 
            "orders", 
            order_id,
            {"amount": existing_order["total_amount"], "notes": processing_request.notes}
        )
        
        # Return updated order
        return await get_order_details(order_id, conn, current_user)
    except Exception as e:
        logger.error(f"Error refunding order: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to refund order"
        )

# Download and QA
@router.get("/subtitle/{subtitle_id}/qa-download")
async def admin_download_subtitle(
    subtitle_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Get subtitle file
        subtitle = await conn.fetchrow("""
            SELECT sf.* 
            FROM subtitle_files sf
            WHERE sf.id = $1
        """, subtitle_id)
        
        if not subtitle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subtitle file not found"
            )
        
        # Check if file exists
        file_path = subtitle["file_path"]
        if not os.path.exists(file_path):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on server")
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "qa_download", 
            "subtitle_files", 
            subtitle_id,
            None
        )
        
        # Read file content
        with open(file_path, "rb") as file:
            content = file.read()
        
        # Get filename from path
        filename = os.path.basename(file_path)
        
        # Return file as response
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error downloading subtitle file: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download subtitle file"
        )

@router.put("/subtitle/{subtitle_id}/qa-status")
async def update_subtitle_qa_status(
    subtitle_id: int,
    qa_status: str = Query(..., description="QA status (approved, rejected)"),
    qa_notes: Optional[str] = Query(None, description="QA notes"),
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Validate QA status
        if qa_status not in ["approved", "rejected", "pending"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid QA status. Must be 'approved', 'rejected', or 'pending'"
            )
            
        # Get subtitle file
        subtitle = await conn.fetchrow("""
            SELECT sf.* 
            FROM subtitle_files sf
            WHERE sf.id = $1
        """, subtitle_id)
        
        if not subtitle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subtitle file not found"
            )
        
        # Update QA status
        await conn.execute("""
            UPDATE subtitle_files
            SET qa_status = $1, qa_notes = $2
            WHERE id = $3
        """, qa_status, qa_notes, subtitle_id)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "update_qa_status", 
            "subtitle_files", 
            subtitle_id,
            {"status": qa_status, "notes": qa_notes}
        )
        
        return {"message": f"QA status updated to {qa_status}"}
    except Exception as e:
        logger.error(f"Error updating QA status: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update QA status"
        )

# Activity logs
@router.get("/logs", response_model=List[AdminLogResponse])
async def get_activity_logs(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    action: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Build where clause
        where_clauses = []
        query_params = []
        param_index = 1
        
        if user_id:
            where_clauses.append(f"l.user_id = ${param_index}")
            query_params.append(user_id)
            param_index += 1
        
        if entity_type:
            where_clauses.append(f"l.entity_type = ${param_index}")
            query_params.append(entity_type)
            param_index += 1
        
        if entity_id:
            where_clauses.append(f"l.entity_id = ${param_index}")
            query_params.append(entity_id)
            param_index += 1
        
        if action:
            where_clauses.append(f"l.action = ${param_index}")
            query_params.append(action)
            param_index += 1
        
        if start_date:
            where_clauses.append(f"l.created_at >= ${param_index}")
            query_params.append(datetime.combine(start_date, datetime.min.time()))
            param_index += 1
        
        if end_date:
            where_clauses.append(f"l.created_at <= ${param_index}")
            query_params.append(datetime.combine(end_date, datetime.max.time()))
            param_index += 1
        
        # Build where clause string
        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        # Get logs
        query = f"""
            SELECT l.*, u.email as user_email
            FROM activity_logs l
            LEFT JOIN users u ON l.user_id = u.id
            {where_clause}
            ORDER BY l.created_at DESC
            LIMIT ${param_index} OFFSET ${param_index + 1}
        """
        query_params.extend([limit, skip])
        
        logs = await conn.fetch(query, *query_params)
        
        return [dict(log) for log in logs]
    except Exception as e:
        logger.error(f"Error fetching activity logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch activity logs"
        )

# System management endpoints
@router.get("/system/health")
async def system_health_check(
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_admin_user)
):
    try:
        # Check database connection
        db_status = "ok"
        try:
            await conn.fetchval("SELECT 1")
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            db_status = "error"
        
        # Check file system
        uploads_dir_exists = os.path.exists(Settings.UPLOAD_DIR)
        outputs_dir_exists = os.path.exists(Settings.OUTPUT_DIR)
        
        # Get system information
        system_info = {
            "database": db_status,
            "file_system": {
                "uploads_dir": uploads_dir_exists,
                "outputs_dir": outputs_dir_exists
            },
            "environment": {
                "python_version": os.environ.get("PYTHONVERSION", "unknown"),
                "fastapi_version": getattr(fastapi, "__version__", "unknown")
            }
        }
        
        return system_info
    except Exception as e:
        logger.error(f"Error checking system health: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check system health"
        )
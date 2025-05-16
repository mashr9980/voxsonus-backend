# app/routes/orders.py
from fastapi import APIRouter, HTTPException, Depends, status, File, UploadFile, Form, BackgroundTasks
import asyncpg
import os
import json
from typing import List, Optional
from app.core.database import get_db_connection
from app.core.security import get_current_active_user
from app.core.utils import (
    save_upload_file, get_video_duration, schedule_cleanup,
    get_cleanup_timestamp, create_output_directory
)
from app.core.config import settings
from app.models.order import (
    OrderCreate, OrderResponse, OrderDetailResponse, SubtitleConfig,
    VideoResponse, OrderStatus, PaymentStatus, VideoStatus, VideoCreate
)
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/videos/upload", response_model=VideoResponse)
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Check file size
        file.file.seek(0, 2)  # Seek to end of file
        file_size = file.file.tell()  # Get position (size)
        file.file.seek(0)  # Reset position
        
        # Get max file size from system settings
        max_file_size = await conn.fetchval(
            "SELECT value::bigint FROM system_settings WHERE key = 'max_file_size'"
        )
        max_file_size = max_file_size or settings.MAX_FILE_SIZE
        
        if file_size > max_file_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size is {max_file_size / (1024*1024)} MB"
            )
        
        # Check file extension
        filename = file.filename.lower()
        valid_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv']
        if not any(filename.endswith(ext) for ext in valid_extensions):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file format. Supported formats: mp4, avi, mov, mkv, wmv, flv"
            )
        
        # Save file
        unique_filename, original_filename, file_path = await save_upload_file(
            file, current_user["id"]
        )
        
        # Get video duration
        duration = get_video_duration(file_path)
        
        # Save to database with cleanup timestamp
        cleanup_timestamp = get_cleanup_timestamp(30)  # 30 minutes
        
        video_id = await conn.fetchval("""
            INSERT INTO videos (
                order_id, filename, original_filename, file_path, 
                file_size, duration, cleanup_timestamp, status
            )
            VALUES (NULL, $1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        """, unique_filename, original_filename, file_path, file_size, 
            duration, cleanup_timestamp, VideoStatus.UPLOADED)
        
        # Schedule cleanup task
        background_tasks.add_task(schedule_cleanup, file_path, 30)
        
        # Get created video
        video = await conn.fetchrow("SELECT * FROM videos WHERE id = $1", video_id)
        
        return dict(video)
    except Exception as e:
        logger.error(f"Error uploading video: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading video: {str(e)}"
        )

@router.post("/create", response_model=OrderResponse)
async def create_order(
    order_create: OrderCreate,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        async with conn.transaction():
            # Check if all videos exist and are not already assigned to an order
            if not order_create.videos:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one video is required"
                )
            
            videos = []
            total_duration = 0
            
            for video_id in order_create.videos:
                video = await conn.fetchrow("""
                    SELECT * FROM videos 
                    WHERE id = $1 AND order_id IS NULL
                """, video_id)
                
                if not video:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Video with ID {video_id} not found or already in use"
                    )
                
                videos.append(dict(video))
                total_duration += video["duration"]
            
            # Get price per minute from system settings
            price_per_minute = await conn.fetchval(
                "SELECT value::float FROM system_settings WHERE key = 'price_per_minute'"
            )
            price_per_minute = price_per_minute or settings.PRICE_PER_MINUTE
            
            # Calculate total amount (convert duration to minutes)
            total_amount = (total_duration / 60) * price_per_minute
            
            # Create order
            order_id = await conn.fetchval("""
                INSERT INTO orders (
                    user_id, total_duration, total_amount, status, payment_status
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """, current_user["id"], total_duration, total_amount, 
                OrderStatus.CREATED, PaymentStatus.UNPAID)
            
            # Update videos with order_id
            for video in videos:
                await conn.execute(
                    "UPDATE videos SET order_id = $1 WHERE id = $2",
                    order_id, video["id"]
                )
            
            # Insert subtitle config
            config_id = await conn.fetchval("""
                INSERT INTO subtitle_configs (
                    order_id, source_language, target_language, max_chars_per_line,
                    lines_per_subtitle, accessibility_mode, non_verbal_only_mode,
                    genre, output_format
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """, order_id, order_create.subtitle_config.source_language,
                order_create.subtitle_config.target_language,
                order_create.subtitle_config.max_chars_per_line,
                order_create.subtitle_config.lines_per_subtitle,
                order_create.subtitle_config.accessibility_mode,
                order_create.subtitle_config.non_verbal_only_mode,
                order_create.subtitle_config.genre,
                order_create.subtitle_config.output_format)
            
            # Get created order
            order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
            order_dict = dict(order)
            
            # Add videos to response
            videos = await conn.fetch("SELECT * FROM videos WHERE order_id = $1", order_id)
            order_dict["videos"] = [dict(video) for video in videos]
            
            return order_dict
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating order: {str(e)}"
        )

@router.get("/", response_model=List[OrderResponse])
async def get_user_orders(
    skip: int = 0,
    limit: int = 100,
    status: Optional[OrderStatus] = None,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Build query conditions
        conditions = ["user_id = $1"]
        params = [current_user["id"]]
        
        if status:
            conditions.append("status = $2")
            params.append(status)
        
        where_clause = " AND ".join(conditions)
        
        # Get orders
        orders = await conn.fetch(f"""
            SELECT * FROM orders 
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT $%d OFFSET $%d
        """ % (len(params) + 1, len(params) + 2), *params, limit, skip)
        
        # Get videos for each order
        result = []
        for order in orders:
            order_dict = dict(order)
            videos = await conn.fetch(
                "SELECT * FROM videos WHERE order_id = $1", order_dict["id"]
            )
            order_dict["videos"] = [dict(video) for video in videos]
            result.append(order_dict)
        
        return result
    except Exception as e:
        logger.error(f"Error fetching user orders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch orders"
        )

@router.get("/{order_id}", response_model=OrderDetailResponse)
async def get_order(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Get order and check ownership
        order = await conn.fetchrow(
            "SELECT * FROM orders WHERE id = $1 AND user_id = $2", 
            order_id, current_user["id"]
        )
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        order_dict = dict(order)
        
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
        
        return order_dict
    except Exception as e:
        logger.error(f"Error fetching order: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch order"
        )

@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_order(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Get order and check ownership
        order = await conn.fetchrow(
            "SELECT * FROM orders WHERE id = $1 AND user_id = $2", 
            order_id, current_user["id"]
        )
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Check if order can be canceled (only if not processed yet)
        if order["status"] not in [OrderStatus.CREATED, OrderStatus.PAID]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order cannot be canceled in its current state"
            )
        
        async with conn.transaction():
            # Update order status
            await conn.execute(
                "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                OrderStatus.CANCELED, order_id
            )
            
            # Delete all related videos and files
            videos = await conn.fetch("SELECT * FROM videos WHERE order_id = $1", order_id)
            
            for video in videos:
                # Delete subtitle files
                await conn.execute(
                    "DELETE FROM subtitle_files WHERE video_id = $1",
                    video["id"]
                )
                
                # Delete video file
                if os.path.exists(video["file_path"]):
                    os.remove(video["file_path"])
            
            # Set videos as deleted (or keep them with canceled status)
            await conn.execute(
                "UPDATE videos SET status = $1 WHERE order_id = $2",
                VideoStatus.FAILED, order_id
            )
        
        return None
    except Exception as e:
        logger.error(f"Error canceling order: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel order"
        )
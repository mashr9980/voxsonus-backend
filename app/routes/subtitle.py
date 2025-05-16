# app/routes/subtitles.py
from fastapi import APIRouter, HTTPException, Depends, status, Response
import asyncpg
import os
from app.core.database import get_db_connection
from app.core.security import get_current_active_user
from app.models.order import OrderStatus, PaymentStatus
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{subtitle_file_id}/download")
async def download_subtitle_file(
    subtitle_file_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Get subtitle file and verify ownership
        subtitle_file = await conn.fetchrow("""
            SELECT sf.*, v.order_id 
            FROM subtitle_files sf
            JOIN videos v ON sf.video_id = v.id
            JOIN orders o ON v.order_id = o.id
            WHERE sf.id = $1 AND o.user_id = $2
        """, subtitle_file_id, current_user["id"])
        
        if not subtitle_file:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subtitle file not found"
            )
        
        # Check if file exists
        file_path = subtitle_file["file_path"]
        if not os.path.exists(file_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found on server"
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
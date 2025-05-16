# app/core/utils.py
import os
import uuid
import shutil
from typing import List
from datetime import datetime, timedelta
import asyncio
from fastapi import UploadFile, HTTPException
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

async def save_upload_file(upload_file: UploadFile, user_id: int) -> tuple[str, str, str]:
    try:
        # Create user directory if it doesn't exist
        user_dir = os.path.join(settings.UPLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        
        # Generate unique filename
        file_extension = os.path.splitext(upload_file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(user_dir, unique_filename)
        
        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)
        
        return unique_filename, upload_file.filename, file_path
    
    except Exception as e:
        logger.error(f"Error saving file: {e}")
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")

async def delete_file(file_path: str) -> bool:
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
    except Exception as e:
        logger.error(f"Error deleting file {file_path}: {e}")
        return False

async def schedule_cleanup(file_path: str, delay_minutes: int = 30) -> None:
    async def delayed_delete():
        await asyncio.sleep(delay_minutes * 60)
        await delete_file(file_path)
    
    asyncio.create_task(delayed_delete())

def get_video_duration(file_path: str) -> int:
    try:
        from moviepy import VideoFileClip
        # Load video using the context manager
        with VideoFileClip(file_path) as video:
            # Access the duration in seconds (float) and round it to an integer
            duration = int(video.duration)
            return max(1, duration)  # Ensure at least 1 second
    except Exception as e:
        logger.error(f"Error getting video duration: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing video: {str(e)}")

def create_output_directory(user_id: int, order_id: int) -> str:
    """Create and return path to output directory for generated subtitle files"""
    output_path = os.path.join(settings.OUTPUT_DIR, str(user_id), str(order_id))
    os.makedirs(output_path, exist_ok=True)
    return output_path

def get_cleanup_timestamp(minutes: int = 30) -> datetime:
    """Get a timestamp for future cleanup"""
    return datetime.utcnow() + timedelta(minutes=minutes)
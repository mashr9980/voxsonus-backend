# scripts/cleanup_files.py
"""
Script to clean up stale files and update database records
"""
import asyncio
import asyncpg
import os
import sys
import argparse
from datetime import datetime, timedelta

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.models.order import OrderStatus, VideoStatus

async def cleanup_files():
    # Connect to database
    conn = await asyncpg.connect(settings.DATABASE_URL)
    
    try:
        # Get videos that are pending cleanup
        pending_cleanup = await conn.fetch("""
            SELECT id, file_path, cleanup_timestamp
            FROM videos
            WHERE cleanup_timestamp <= $1 AND file_path IS NOT NULL
        """, datetime.utcnow())
        
        cleanup_count = 0
        for video in pending_cleanup:
            # Check if file exists
            if os.path.exists(video["file_path"]):
                # Delete file
                os.remove(video["file_path"])
                cleanup_count += 1
            
            # Update record
            await conn.execute("""
                UPDATE videos
                SET cleanup_timestamp = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = $1
            """, video["id"])
        
        print(f"Cleaned up {cleanup_count} video files")
        
        # Find orphaned uploads (no order for > 30 minutes)
        cutoff_time = datetime.utcnow() - timedelta(minutes=30)
        orphaned_videos = await conn.fetch("""
            SELECT id, file_path
            FROM videos
            WHERE order_id IS NULL AND created_at < $1
        """, cutoff_time)
        
        orphan_count = 0
        for video in orphaned_videos:
            # Check if file exists
            if video["file_path"] and os.path.exists(video["file_path"]):
                # Delete file
                os.remove(video["file_path"])
                orphan_count += 1
            
            # Delete record
            await conn.execute("DELETE FROM videos WHERE id = $1", video["id"])
        
        print(f"Cleaned up {orphan_count} orphaned video uploads")
        
    finally:
        await conn.close()

def main():
    parser = argparse.ArgumentParser(description="Clean up stale files")
    
    # Run cleanup
    asyncio.run(cleanup_files())

if __name__ == "__main__":
    main()
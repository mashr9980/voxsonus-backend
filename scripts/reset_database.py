# scripts/reset_database.py
"""
Script to completely reset the database
WARNING: This will delete all data in the database!
"""
import asyncio
import asyncpg
import sys
import os
import shutil
import argparse

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

async def reset_database(confirm=False, reset_uploads=False):
    # Safety check
    if not confirm:
        print("WARNING: This will DELETE ALL DATA in the database!")
        print("To confirm, run with --confirm flag.")
        return
    
    # Extract database name from connection string
    db_parts = settings.DATABASE_URL.split('/')
    db_name = db_parts[-1]
    
    # Create connection string to postgres database for admin operations
    admin_conn_string = '/'.join(db_parts[:-1]) + '/postgres'
    
    try:
        # Connect to postgres database to perform admin operations
        conn = await asyncpg.connect(admin_conn_string)
        
        try:
            # Drop connections to the database
            await conn.execute(f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{db_name}'
              AND pid <> pg_backend_pid();
            """)
            
            # Drop and recreate database
            print(f"Dropping database {db_name}...")
            await conn.execute(f"DROP DATABASE IF EXISTS {db_name};")
            print(f"Creating database {db_name}...")
            await conn.execute(f"CREATE DATABASE {db_name};")
            print("Database reset complete.")
        finally:
            await conn.close()
        
        # Also clean up uploads and outputs directories if requested
        if reset_uploads:
            print("Cleaning up uploads directory...")
            cleanup_directory(settings.UPLOAD_DIR)
            print("Cleaning up outputs directory...")
            cleanup_directory(settings.OUTPUT_DIR)
            print("Directories cleaned.")
        
        print("\nNOTE: You must restart the application for changes to take effect.")
        print("After restarting, run the following to create admin user:")
        print(f"python scripts/init_admin.py --email admin@example.com --password your_password --role super_admin")
        
    except Exception as e:
        print(f"Error resetting database: {e}")
        sys.exit(1)

def cleanup_directory(directory):
    """Remove all files and subdirectories in directory but keep the directory itself"""
    if os.path.exists(directory):
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
    else:
        os.makedirs(directory)
        # Add .gitkeep file to ensure directory is tracked in git
        with open(os.path.join(directory, '.gitkeep'), 'w') as f:
            pass

def main():
    parser = argparse.ArgumentParser(description="Reset the database and optionally the upload directories")
    parser.add_argument("--confirm", action="store_true", help="Confirm database reset")
    parser.add_argument("--reset-uploads", action="store_true", help="Also reset uploads and outputs directories")
    
    args = parser.parse_args()
    
    asyncio.run(reset_database(args.confirm, args.reset_uploads))

if __name__ == "__main__":
    main()
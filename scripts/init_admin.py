# scripts/init_admin.py (Updated)
"""
Script to create an admin or super admin user for initial setup
"""
import asyncio
import asyncpg
import argparse
import sys
import os

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.security import get_password_hash
from app.core.database import log_activity

async def create_admin_user(email, password, role="admin", first_name=None, last_name=None):
    # Connect to database
    conn = await asyncpg.connect(settings.DATABASE_URL)
    
    try:
        # Check if user already exists
        existing_user = await conn.fetchrow("SELECT id, role FROM users WHERE email = $1", email)
        
        if existing_user:
            print(f"User with email {email} already exists")
            
            # Check if user already has the requested role
            if existing_user["role"] == role:
                print(f"User is already a {role}")
                return
            
            # Promote user to requested role
            await conn.execute(
                "UPDATE users SET role = $1 WHERE email = $2",
                role, email
            )
            print(f"User {email} has been promoted to {role}")
            
            # Log activity
            await log_activity(
                conn, 
                existing_user["id"], 
                "role_update_via_script", 
                "users", 
                existing_user["id"],
                {"new_role": role}
            )
            
            return
        
        # Validate role
        if role not in ["user", "admin", "super_admin"]:
            print("Invalid role. Must be 'user', 'admin', or 'super_admin'")
            return
        
        # Create new admin user
        hashed_password = get_password_hash(password)
        
        user_id = await conn.fetchval("""
            INSERT INTO users (email, password_hash, first_name, last_name, role) 
            VALUES ($1, $2, $3, $4, $5) 
            RETURNING id
        """, email, hashed_password, first_name, last_name, role)
        
        print(f"{role.capitalize()} user created successfully with ID: {user_id}")
        
        # Log activity
        await log_activity(
            conn, 
            user_id, 
            "user_created_via_script", 
            "users", 
            user_id,
            {"role": role}
        )
    
    finally:
        await conn.close()

def main():
    parser = argparse.ArgumentParser(description="Create an admin or super admin user")
    parser.add_argument("--email", required=True, help="Admin email")
    parser.add_argument("--password", required=True, help="Admin password")
    parser.add_argument("--role", choices=["admin", "super_admin"], default="admin", help="User role")
    parser.add_argument("--first-name", help="Admin first name")
    parser.add_argument("--last-name", help="Admin last name")
    
    args = parser.parse_args()
    
    asyncio.run(create_admin_user(
        args.email, 
        args.password,
        args.role,
        args.first_name,
        args.last_name
    ))

if __name__ == "__main__":
    main()
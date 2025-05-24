# app/core/database.py (Updated)
import asyncpg
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

async def get_db_connection():
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

async def create_tables():
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        # Create role enum type if it doesn't exist
        await conn.execute('''
        DO $$ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                CREATE TYPE user_role AS ENUM ('user', 'admin', 'super_admin');
            END IF;
        END$$;
        ''')
        
        # Create activity logs table
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            action VARCHAR(255) NOT NULL,
            entity_type VARCHAR(50) NOT NULL,
            entity_id INTEGER,
            details JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Update users table with role enum
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            first_name VARCHAR(255),
            last_name VARCHAR(255),
            role user_role NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Add permissions table for future extension
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS permissions (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Add role_permissions table for future extension
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS role_permissions (
            id SERIAL PRIMARY KEY,
            role user_role NOT NULL,
            permission_id INTEGER REFERENCES permissions(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(role, permission_id)
        )
        ''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            status VARCHAR(50) NOT NULL DEFAULT 'created',
            total_duration INTEGER NOT NULL,
            total_amount NUMERIC(10, 2) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            payment_intent_id VARCHAR(255),
            payment_status VARCHAR(50) DEFAULT 'unpaid',
            admin_notes TEXT,
            processed_by INTEGER REFERENCES users(id),
            approved_by INTEGER REFERENCES users(id),
            rejected_by INTEGER REFERENCES users(id),
            error_message TEXT
        )
        ''')

        await conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_orders_approved_by ON orders(approved_by)
        ''')

        await conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_orders_rejected_by ON orders(rejected_by)
        ''')

        await conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_orders_processed_by ON orders(processed_by)
        ''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
            filename VARCHAR(255) NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            file_path VARCHAR(255) NOT NULL,
            file_size INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'uploaded',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            cleanup_timestamp TIMESTAMP WITH TIME ZONE,
            qa_notes TEXT,
            error_message TEXT
        )
        ''')

        await conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
        ''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS subtitle_configs (
            id SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
            source_language VARCHAR(50) NOT NULL,
            target_language VARCHAR(50),
            max_chars_per_line INTEGER DEFAULT 42,
            lines_per_subtitle INTEGER DEFAULT 2,
            accessibility_mode BOOLEAN DEFAULT FALSE,
            non_verbal_only_mode BOOLEAN DEFAULT FALSE,
            genre VARCHAR(50),
            output_format VARCHAR(20) DEFAULT 'srt',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS subtitle_files (
            id SERIAL PRIMARY KEY,
            video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            config_id INTEGER REFERENCES subtitle_configs(id) ON DELETE CASCADE,
            file_path VARCHAR(255) NOT NULL,
            file_format VARCHAR(20) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            qa_status VARCHAR(20) DEFAULT 'pending',
            qa_notes TEXT,
            transcript_id VARCHAR(255)
        )
        ''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            id SERIAL PRIMARY KEY,
            key VARCHAR(255) UNIQUE NOT NULL,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_by INTEGER REFERENCES users(id)
        )
        ''')
        
        # Insert default system settings
        await conn.execute('''
        INSERT INTO system_settings (key, value, description)
        VALUES 
            ('price_per_minute', '1.0', 'Price per minute of video processing'),
            ('max_file_size', '1073741824', 'Maximum file size in bytes (1GB)'),
            ('max_files_per_order', '10', 'Maximum number of files per order')
        ON CONFLICT (key) DO NOTHING
        ''')
        
        # Insert default permissions
        await conn.execute('''
        INSERT INTO permissions (name, description)
        VALUES 
            ('read_own', 'Read own data'),
            ('write_own', 'Write own data'),
            ('read_all', 'Read all data'),
            ('write_all', 'Write all data'),
            ('manage_orders', 'Manage orders'),
            ('manage_settings', 'Manage system settings'),
            ('manage_users', 'Manage users'),
            ('manage_roles', 'Manage user roles')
        ON CONFLICT (name) DO NOTHING
        ''')
        
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Error creating database tables: {e}")
        raise
    finally:
        await conn.close()

async def log_activity(conn, user_id, action, entity_type, entity_id=None, details=None):
    """Log user activity in the system"""
    try:
        await conn.execute('''
            INSERT INTO activity_logs (user_id, action, entity_type, entity_id, details)
            VALUES ($1, $2, $3, $4, $5)
        ''', user_id, action, entity_type, entity_id, details)
    except Exception as e:
        logger.error(f"Error logging activity: {e}")
# app/core/config.py
import os
from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Subtitles Platform"
    API_V1_STR: str = "/api"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-here")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30 * 24 * 60  # 30 days

    POSTGRES_USER: str = os.getenv("DB_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("DB_PASSWORD", "123")
    POSTGRES_SERVER: str = os.getenv("DB_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("DB_PORT", "5432")
    POSTGRES_DB: str = os.getenv("DB_NAME", "soniqmind")
    DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_SERVER}:{POSTGRES_PORT}/{POSTGRES_DB}"
    
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    
    STRIPE_API_KEY: str = os.getenv("STRIPE_API_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    
    ASSEMBLYAI_API_KEY: str = os.getenv("ASSEMBLYAI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DEEPL_API_KEY: str = os.getenv("DEEPL_API_KEY", "")
    
    # File handling
    UPLOAD_DIR: str = "uploads"
    OUTPUT_DIR: str = "outputs"
    ALLOWED_EXTENSIONS: set = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    
    MAX_FILE_SIZE: int = 1024 * 1024 * 1024  # 1GB
    PRICE_PER_MINUTE: float = 1.0

    # Stripe Configuration
    STRIPE_PUBLISHABLE_KEY: Optional[str] = None
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    
    # Frontend URLs
    FRONTEND_URL: str = "http://localhost:3000"
    FRONTEND_SUCCESS_URL: str = "http://localhost:3000/orders/{order_id}/success"
    FRONTEND_CANCEL_URL: str = "http://localhost:3000/orders/{order_id}/cancel"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
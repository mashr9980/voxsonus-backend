# app/core/config.py
import os
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Subtitles Platform"
    API_V1_STR: str = "/api"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-here")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    POSTGRES_USER: str = os.getenv("DB_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("DB_PASSWORD", "123")
    POSTGRES_SERVER: str = os.getenv("DB_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("DB_PORT", "5432")
    POSTGRES_DB: str = os.getenv("DB_NAME", "soniqmind")
    DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_SERVER}:{POSTGRES_PORT}/{POSTGRES_DB}"
    
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    
    # Frontend URL for payment redirects
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "")

    STRIPE_API_KEY: str = os.getenv("STRIPE_API_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    
    ASSEMBLY_AI_API_KEY: str = os.getenv("ASSEMBLY_AI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "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")
    
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads")
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "outputs")
    
    MAX_FILE_SIZE: int = 1024 * 1024 * 1024  # 1GB
    PRICE_PER_MINUTE: float = 1.0
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
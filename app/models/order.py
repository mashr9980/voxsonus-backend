# app/models/order.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum

class OrderStatus(str, Enum):
    CREATED = "created"
    PAID = "paid"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"

class OutputFormat(str, Enum):
    SRT = "srt"
    VTT = "vtt"
    ASS = "ass"
    TXT = "txt"

class Genre(str, Enum):
    GENERAL = "general"
    HORROR = "horror"
    COMEDY = "comedy"
    ROMANCE = "romance"
    ACTION = "action"
    DOCUMENTARY = "documentary"

class VideoStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class SubtitleConfig(BaseModel):
    source_language: str
    target_language: Optional[str] = None
    max_chars_per_line: int = 42
    lines_per_subtitle: int = 2
    accessibility_mode: bool = False
    non_verbal_only_mode: bool = False
    genre: Optional[Genre] = Genre.GENERAL
    output_format: OutputFormat = OutputFormat.SRT

class VideoCreate(BaseModel):
    filename: str
    original_filename: str
    file_path: str
    file_size: int
    duration: int

class VideoResponse(BaseModel):
    id: int
    original_filename: str
    duration: int
    status: VideoStatus
    created_at: datetime

class SubtitleFileResponse(BaseModel):
    id: int
    file_path: str
    file_format: str
    created_at: datetime

class OrderCreate(BaseModel):
    videos: List[int] = []
    subtitle_config: SubtitleConfig

class OrderResponse(BaseModel):
    id: int
    status: OrderStatus
    total_duration: int
    total_amount: float
    payment_status: PaymentStatus
    created_at: datetime
    updated_at: datetime
    videos: List[VideoResponse] = []

class OrderDetailResponse(OrderResponse):
    subtitle_config: SubtitleConfig
    subtitle_files: List[SubtitleFileResponse] = []
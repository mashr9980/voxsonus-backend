# app/models/admin.py (Updated)
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
from app.models.order import OrderStatus, PaymentStatus, OrderResponse

class SystemSettingUpdate(BaseModel):
    value: str
    description: Optional[str] = None

class SystemSettingResponse(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    updated_at: datetime
    updated_by: Optional[str] = None

class AdminOrderUpdate(BaseModel):
    status: Optional[OrderStatus] = None
    payment_status: Optional[PaymentStatus] = None
    admin_notes: Optional[str] = None

class ProcessingRequest(BaseModel):
    notes: Optional[str] = None

class AdminStats(BaseModel):
    total_users: int
    total_orders: int
    total_completed_orders: int
    total_revenue: float
    orders_today: int
    revenue_today: float
    period_orders: int
    period_revenue: float
    orders_by_status: Dict[str, int]

class AdminUserResponse(BaseModel):
    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime
    orders_count: int
    total_spent: float

class AdminOrderListResponse(BaseModel):
    total: int
    orders: List[OrderResponse]

class AdminLogResponse(BaseModel):
    id: int
    user_id: Optional[int]
    user_email: Optional[str]
    action: str
    entity_type: str
    entity_id: Optional[int]
    details: Optional[Dict[str, Any]]
    created_at: datetime

class RoleUpdate(BaseModel):
    role: str
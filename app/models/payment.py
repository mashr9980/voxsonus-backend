# app/models/payment.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class CheckoutSessionResponse(BaseModel):
    success: bool
    checkout_url: str
    session_id: str

class PaymentStatusResponse(BaseModel):
    order_id: int
    order_status: str
    payment_status: str
    total_amount: float
    last_updated: datetime
    stripe_status: Optional[str] = None
    stripe_session_status: Optional[str] = None

class PaymentSuccessResponse(BaseModel):
    success: bool
    message: str
    order_id: int
    status: str

class PaymentCancelResponse(BaseModel):
    success: bool
    message: str
    order_id: int
    status: str
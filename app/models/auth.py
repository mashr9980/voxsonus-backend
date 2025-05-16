# app/models/auth.py (Updated)
from pydantic import BaseModel, EmailStr, Field

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenWithRole(Token):
    role: str
    user_id: int

class TokenData(BaseModel):
    user_id: int

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
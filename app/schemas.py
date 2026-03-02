# app/schemas.py
import uuid
from pydantic import BaseModel, Field, EmailStr, ConfigDict
from decimal import Decimal
from datetime import datetime
from typing import Optional, List

class UserCreate(BaseModel):
    """Register request schema - JSON body"""
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    password: str = Field(..., min_length=8, description="Password for authentication")  # ✅ ADDED
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "username": "umesh",
            "email": "umesh@test.com",
            "full_name": "Umesh Kumar",
            "password": "SecurePass123!"
        }
    })

class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class WalletResponse(BaseModel):
    wallet_id: int
    user_id: uuid.UUID
    username: str
    balance: Decimal
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class TransactionRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, description="Amount must be positive")
    model_config = ConfigDict(json_schema_extra={"example": {"amount": 25.50}})

class TransactionRecord(BaseModel):
    id: int
    amount: Decimal
    transaction_type: str
    balance_after: Decimal
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class LedgerResponse(BaseModel):
    transactions: List[TransactionRecord]
    current_balance: Decimal
    username: str
    model_config = ConfigDict(from_attributes=True)

class UserLogin(BaseModel):
    """Login request schema - JSON body"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    model_config = ConfigDict(json_schema_extra={"example": {"username": "umesh", "password": "SecurePass123!"}})

class Token(BaseModel):
    """JWT token response"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class TokenData(BaseModel):
    user_id: Optional[uuid.UUID] = None
    exp: Optional[datetime] = None
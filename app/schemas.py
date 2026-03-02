import uuid
from pydantic import BaseModel, Field, EmailStr, ConfigDict
from decimal import Decimal
from datetime import datetime
from typing import Optional, List

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "username": "johndoe",
            "email": "john@example.com",
            "full_name": "John Doe"
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
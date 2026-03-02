# app/api.py
from fastapi import APIRouter, Depends, HTTPException, status, Security
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timedelta
import uuid

from app.database import get_db_session
from app.models import User
from app.services import (
    create_user_service,
    get_user_by_username_service,
    get_user_by_id_service,
    authenticate_user_service,
    UserAlreadyExistsError,
    UserNotFoundError,
    create_wallet_for_user_service,
    credit_wallet_service,
    debit_wallet_service,
    get_ledger_service,
    get_wallet_by_user_id_service,
    LockTimeoutError,
    InsufficientFundsError,
    WalletNotFoundError,
)
from app.schemas import (
    UserCreate, UserResponse, UserLogin, Token,
    WalletResponse, TransactionRequest, LedgerResponse
)
from app.auth import (
    create_access_token,
    get_current_user_with_session,  # ✅ Combined dependency
    http_bearer,
)
from app.logging_config import logger
from app.config import settings

router = APIRouter(prefix="/api/v1", tags=["Wallet & Users"])

USER_NOT_FOUND = "User not found"
USER_ALREADY_EXISTS = "User already exists"
WALLET_NOT_FOUND = "Wallet not found"
DATABASE_ERROR = "Database error"
TRANSACTION_FAILED = "Transaction failed"
INVALID_CREDENTIALS = "Invalid username or password"

# ============ PUBLIC ENDPOINTS ============
@router.post("/auth/login", response_model=Token)
async def login(
    credentials: UserLogin,  # ✅ Variable name: credentials
    session: AsyncSession = Depends(get_db_session)
):
    """🔓 PUBLIC: Authenticate and issue JWT token"""
    user = await authenticate_user_service(session, credentials.username, credentials.password)
    if not user:
        logger.warning("Login failed - invalid credentials", extra={"username": credentials.username})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    logger.info("User logged in successfully", extra={"user_id": str(user.id), "username": user.username})
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
     UserCreate,  # ✅ FIXED: Added variable name 'data'
    session: AsyncSession = Depends(get_db_session)
):
    """🔓 PUBLIC: Register a new user"""
    try:
        user = await create_user_service(
            session,
            username=data.username,
            email=data.email,
            full_name=data.full_name,
            password=data.password
        )
        logger.info("User registered successfully", extra={"user_id": str(user.id), "username": user.username})
        return UserResponse.model_validate(user)
    except UserAlreadyExistsError as e:
        logger.warning("Registration failed - user exists", extra={"error": str(e)})
        raise HTTPException(status_code=409, detail=USER_ALREADY_EXISTS)
    except IntegrityError:
        logger.error("Database integrity error during registration")
        raise HTTPException(status_code=500, detail=DATABASE_ERROR)
    except Exception as e:
        logger.error("Unexpected registration error: {}".format(str(e)))
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)

@router.get("/users/{username}", response_model=UserResponse)
async def get_user(
    username: str,
    session: AsyncSession = Depends(get_db_session)
):
    """🔓 PUBLIC: Get user details by username"""
    try:
        user = await get_user_by_username_service(session, username)
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)

# ============ PROTECTED ENDPOINTS ============
@router.post("/wallet", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
async def create_wallet(
    current_user: User = Depends(get_current_user_with_session),  # ✅ Single session
):
    """🔐 PROTECTED: Create wallet for authenticated user"""
    try:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            wallet = await create_wallet_for_user_service(session, current_user.id)
            return WalletResponse(
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                username=current_user.username,
                balance=wallet.balance,
                created_at=wallet.created_at
            )
    except IntegrityError:
        logger.error("Database error creating wallet", extra={"user_id": str(current_user.id)})
        raise HTTPException(status_code=500, detail=DATABASE_ERROR)
    except Exception as e:
        logger.error("Wallet creation failed: {}".format(str(e)), extra={"user_id": str(current_user.id)})
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)

@router.post("/wallet/credit", response_model=WalletResponse)
async def credit_money(
     TransactionRequest,  # ✅ FIXED: Added variable name 'data'
    current_user: User = Depends(get_current_user_with_session),  # ✅ Single session
):
    """🔐 PROTECTED: Credit money to authenticated user's wallet"""
    try:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            wallet = await credit_wallet_service(session, current_user.id, data.amount)
            return WalletResponse(
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                username=current_user.username,
                balance=wallet.balance,
                created_at=wallet.created_at
            )
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)
    except Exception as e:
        logger.error("Credit failed: {}".format(str(e)), extra={"user_id": str(current_user.id), "amount": str(data.amount)})
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)
    except LockTimeoutError:
        logger.warning("Credit failed - lock timeout", extra={"user_id": str(current_user.id)})
        raise HTTPException(status_code=409, detail="Wallet temporarily locked, please retry")

@router.post("/wallet/debit", response_model=WalletResponse)
async def debit_money(
     TransactionRequest,  # ✅ FIXED: Added variable name 'data'
    current_user: User = Depends(get_current_user_with_session),  # ✅ Single session
):
    """🔐 PROTECTED: Debit money from authenticated user's wallet"""
    try:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            wallet = await debit_wallet_service(session, current_user.id, data.amount)
            return WalletResponse(
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                username=current_user.username,
                balance=wallet.balance,
                created_at=wallet.created_at
            )
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)
    except InsufficientFundsError as e:
        logger.warning("Debit failed - insufficient funds", extra={"user_id": str(current_user.id), "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Debit failed: {}".format(str(e)), extra={"user_id": str(current_user.id), "amount": str(data.amount)})
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)
    except LockTimeoutError:
        logger.warning("Debit failed - lock timeout", extra={"user_id": str(current_user.id)})
        raise HTTPException(status_code=409, detail="Wallet temporarily locked, please retry")

@router.get("/wallet/balance", response_model=WalletResponse)
async def get_balance(
    current_user: User = Depends(get_current_user_with_session),  # ✅ Single session
):
    """🔐 PROTECTED: Get authenticated user's wallet balance"""
    try:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            wallet, user = await get_wallet_by_user_id_service(session, current_user.id)
            return WalletResponse(
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                username=user.username,
                balance=wallet.balance,
                created_at=wallet.created_at
            )
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)

@router.get("/wallet/ledger", response_model=LedgerResponse)
async def get_history(
    current_user: User = Depends(get_current_user_with_session),  # ✅ Single session
):
    """🔐 PROTECTED: Get authenticated user's transaction history"""
    try:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            transactions, balance, username = await get_ledger_service(session, current_user.id)
            return LedgerResponse(
                transactions=transactions,
                current_balance=balance,
                username=username
            )
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)
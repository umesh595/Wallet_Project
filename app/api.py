from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from app.database import get_db_session
from app.services import (
    create_user_service,
    get_user_by_username_service,
    get_user_by_id_service,
    UserAlreadyExistsError,
    UserNotFoundError,
    create_wallet_for_user_service,
    credit_wallet_service,
    debit_wallet_service,
    get_ledger_service,
    get_wallet_by_user_id_service,
    InsufficientFundsError,
    WalletNotFoundError,
)
from app.schemas import (
    UserCreate, UserResponse,
    WalletResponse, TransactionRequest, LedgerResponse
)
from app.logging_config import logger

router = APIRouter(prefix="/api/v1", tags=["Wallet & Users"])

USER_NOT_FOUND = "User not found"
USER_ALREADY_EXISTS = "User already exists"
WALLET_NOT_FOUND = "Wallet not found"
DATABASE_ERROR = "Database error"
TRANSACTION_FAILED = "Transaction failed"

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    data: UserCreate,
    session: AsyncSession = Depends(get_db_session)
):
    """Register a new user"""
    try:
        user = await create_user_service(
            session,
            username=data.username,
            email=data.email,
            full_name=data.full_name
        )
        logger.info(
            "User registered successfully",
            extra={"user_id": str(user.id), "username": user.username}
        )
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
    """Get user details by username"""
    try:
        user = await get_user_by_username_service(session, username)
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)

@router.post("/wallet", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
async def create_wallet(
    user_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_db_session)
):
    """Create wallet for an existing user"""
    try:
        wallet = await create_wallet_for_user_service(session, user_id)
        user = await get_user_by_id_service(session, user_id)
        
        return WalletResponse(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            username=user.username,
            balance=wallet.balance,
            created_at=wallet.created_at
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    except IntegrityError:
        logger.error("Database error creating wallet", extra={"user_id": str(user_id)})
        raise HTTPException(status_code=500, detail=DATABASE_ERROR)
    except Exception as e:
        logger.error("Wallet creation failed: {}".format(str(e)), extra={"user_id": str(user_id)})
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)

@router.post("/wallet/credit", response_model=WalletResponse)
async def credit_money(
    user_id: uuid.UUID = Query(...),
    data: TransactionRequest = None,
    session: AsyncSession = Depends(get_db_session)
):
    """Credit money to user's wallet"""
    try:
        wallet = await credit_wallet_service(session, user_id, data.amount)
        user = await get_user_by_id_service(session, user_id)
        
        return WalletResponse(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            username=user.username,
            balance=wallet.balance,
            created_at=wallet.created_at
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)
    except Exception as e:
        logger.error(
            "Credit failed: {}".format(str(e)),
            extra={"user_id": str(user_id), "amount": str(data.amount)}
        )
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)

@router.post("/wallet/debit", response_model=WalletResponse)
async def debit_money(
    user_id: uuid.UUID = Query(...),
    data: TransactionRequest = None,
    session: AsyncSession = Depends(get_db_session)
):
    """Debit money from user's wallet"""
    try:
        wallet = await debit_wallet_service(session, user_id, data.amount)
        user = await get_user_by_id_service(session, user_id)
        return WalletResponse(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            username=user.username,
            balance=wallet.balance,
            created_at=wallet.created_at
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)
    except InsufficientFundsError as e:
        logger.warning(
            "Debit failed - insufficient funds",
            extra={"user_id": str(user_id), "error": str(e)}
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Debit failed: {}".format(str(e)),
            extra={"user_id": str(user_id), "amount": str(data.amount)}
        )
        raise HTTPException(status_code=500, detail=TRANSACTION_FAILED)

@router.get("/wallet/balance", response_model=WalletResponse)
async def get_balance(
    user_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_db_session)
):
    """Get current wallet balance"""
    try:
        wallet, user = await get_wallet_by_user_id_service(session, user_id)
        return WalletResponse(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            username=user.username,
            balance=wallet.balance,
            created_at=wallet.created_at
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)

@router.get("/wallet/ledger", response_model=LedgerResponse)
async def get_history(
    user_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_db_session)
):
    """Get transaction history for user's wallet"""
    try:
        transactions, balance, username = await get_ledger_service(session, user_id)
        return LedgerResponse(
            transactions=transactions,
            current_balance=balance,
            username=username
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    except WalletNotFoundError:
        raise HTTPException(status_code=404, detail=WALLET_NOT_FOUND)
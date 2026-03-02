import uuid
import asyncio
from decimal import Decimal
from typing import List, Tuple, Optional
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, IntegrityError, DBAPIError
from app.database import AsyncSession
from app.models import User, Wallet, Transaction
from app.logging_config import logger
from app.auth import get_password_hash, verify_password
class InsufficientFundsError(Exception):
    pass
class WalletNotFoundError(Exception):
    pass
class UserAlreadyExistsError(Exception):
    pass
class UserNotFoundError(Exception):
    pass
class LockTimeoutError(Exception):
    """Raised when lock wait exceeds timeout"""
    pass
class DeadlockRetryError(Exception):
    """Raised when deadlock retry limit exceeded"""
    pass

# Production config
LOCK_TIMEOUT_MS = 5000
MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_DELAY_MS = 50

async def create_user_service(session: AsyncSession, username: str, email: str, full_name: Optional[str] = None, password: Optional[str] = None) -> User:
    result = await session.execute(
        select(User).where(
            (User.username == username) | (User.email == email)
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.warning(
            "User creation failed - already exists",
            extra={"username": username, "email": email, "existing_id": str(existing.id)}
        )
        raise UserAlreadyExistsError("User with username '{}' or email '{}' already exists".format(username, email))
    if password is None:
        raise ValueError("Password is required for user creation")
    user = User(username=username, email=email, full_name=full_name, is_active=True, hashed_password=get_password_hash(password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("User created successfully", extra={"user_id": str(user.id), "username": user.username})
    return user

async def authenticate_user_service(session: AsyncSession, username: str, password: str) -> Optional[User]:
    """
    Authenticate user by username + password.
    Returns User if valid, None otherwise.
    """
    result = await session.execute(
        select(User).where(
            (User.username == username) | (User.email == username),
            User.is_active == True
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        logger.warning("Authentication failed - user not found", extra={"username": username})
        return None
    
    if not verify_password(password, user.hashed_password):
        logger.warning("Authentication failed - invalid password", extra={"username": username})
        return None
    logger.info("User authenticated successfully", extra={"user_id": str(user.id), "username": user.username})
    return user

async def get_user_by_username_service(session: AsyncSession, username: str) -> User:
    result = await session.execute(select(User).where(User.username == username, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("User not found by username", extra={"username": username})
        raise UserNotFoundError("No active user found with username: {}".format(username))
    return user

async def get_user_by_id_service(session: AsyncSession, user_id: uuid.UUID) -> User:
    result = await session.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("User not found by ID", extra={"user_id": str(user_id)})
        raise UserNotFoundError("No active user found with ID: {}".format(user_id))
    return user

async def create_wallet_for_user_service(session: AsyncSession, user_id: uuid.UUID) -> Wallet:
    await get_user_by_id_service(session, user_id)
    result = await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    existing_wallet = result.scalar_one_or_none()
    if existing_wallet:
        logger.info("Wallet already exists for user", extra={"user_id": str(user_id), "wallet_id": existing_wallet.id})
        return existing_wallet
    wallet = Wallet(user_id=user_id, balance=Decimal("0.00"))
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    logger.info("Wallet created for user", extra={"user_id": str(user_id), "wallet_id": wallet.id})
    return wallet

async def _get_wallet_with_lock(session: AsyncSession, user_id: uuid.UUID) -> Tuple[Wallet, User]:
    """Get wallet + user with row-level lock. Lock timeout set at DB connection level."""
    user = await get_user_by_id_service(session, user_id)
    
    # 🔒 Row-level lock — timeout handled by DB connection config
    result = await session.execute(
        select(Wallet)
        .where(Wallet.user_id == user_id)
        .with_for_update(
            nowait=False,
            read=False,
            key_share=False,
            of=Wallet
        )
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        logger.warning("Wallet not found for user", extra={"user_id": str(user_id)})
        raise WalletNotFoundError("No wallet found for user ID: {}".format(user_id))
    return wallet, user

async def get_wallet_by_user_id_service(session: AsyncSession, user_id: uuid.UUID) -> Tuple[Wallet, User]:
    """Non-locking version for read-only operations"""
    user = await get_user_by_id_service(session, user_id)
    result = await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        logger.warning("Wallet not found for user", extra={"user_id": str(user_id)})
        raise WalletNotFoundError("No wallet found for user ID: {}".format(user_id))
    return wallet, user

async def _execute_with_retry(operation_name: str, func, *args, **kwargs):
    """
    Execute async function with retry logic for:
    - Deadlocks (40P01)
    - Lock timeouts (55P03)
    - Serialization failures (40001)
    """
    last_error = None
    for attempt in range(MAX_DEADLOCK_RETRIES):
        try:
            return await func(*args, **kwargs)
        except (OperationalError, DBAPIError) as e:
            error_str = str(e).lower()
            if "40p01" in error_str or "40001" in error_str or "deadlock" in error_str or "lock_timeout" in error_str or "55p03" in error_str:
                last_error = e
                retry_delay = DEADLOCK_RETRY_DELAY_MS * (attempt + 1) / 1000.0
                logger.warning(
                    "{} detected during {}, retrying in {}s (attempt {}/{})".format(
                        "Deadlock/lock timeout" if "deadlock" in error_str or "55p03" in error_str else "Serialization failure",
                        operation_name, retry_delay, attempt + 1, MAX_DEADLOCK_RETRIES
                    ),
                    extra={"error": str(e), "attempt": attempt + 1, "operation": operation_name, "error_code": getattr(e.orig, 'pgcode', None) if hasattr(e, 'orig') else None}
                )
                await asyncio.sleep(retry_delay)
                continue
            raise
        except LockTimeoutError:
            raise
    logger.error(
        "Retry limit exceeded for {}".format(operation_name),
        extra={"error": str(last_error), "operation": operation_name}
    )
    raise DeadlockRetryError("Failed after {} retries: {}".format(MAX_DEADLOCK_RETRIES, last_error))

async def _do_credit_wallet(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    """Internal: Core credit logic (called within retry wrapper)"""
    async with session.begin():
        wallet, user = await _get_wallet_with_lock(session, user_id)
        wallet.balance += amount
        ledger_entry = Transaction(
            wallet_id=wallet.id,
            amount=amount,
            transaction_type="CREDIT",
            balance_after=wallet.balance,
        )
        session.add(ledger_entry)
        await session.flush()
        logger.info(
            "Wallet credited successfully",
            extra={
                "wallet_id": wallet.id,
                "user_id": str(user_id),
                "username": user.username,
                "amount": str(amount),
                "new_balance": str(wallet.balance),
                "locked": True,
            },
        )
        return wallet

async def _do_debit_wallet(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    """Internal: Core debit logic (called within retry wrapper)"""
    async with session.begin():
        wallet, user = await _get_wallet_with_lock(session, user_id)
        if wallet.balance < amount:
            logger.warning(
                "Insufficient funds for debit",
                extra={
                    "wallet_id": wallet.id,
                    "user_id": str(user_id),
                    "username": user.username,
                    "requested": str(amount),
                    "available": str(wallet.balance),
                    "locked": True,
                },
            )
            raise InsufficientFundsError("Insufficient funds. Balance: {}, Requested: {}".format(wallet.balance, amount))
        wallet.balance -= amount
        ledger_entry = Transaction(
            wallet_id=wallet.id,
            amount=amount,
            transaction_type="DEBIT",
            balance_after=wallet.balance,
        )
        session.add(ledger_entry)
        await session.flush()
        logger.info(
            "Wallet debited successfully",
            extra={
                "wallet_id": wallet.id,
                "user_id": str(user_id),
                "username": user.username,
                "amount": str(amount),
                "new_balance": str(wallet.balance),
                "locked": True,
            },
        )
        return wallet

async def credit_wallet_service(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    """Credit with retry logic for concurrency safety"""
    return await _execute_with_retry("credit_wallet", _do_credit_wallet, session, user_id, amount)

async def debit_wallet_service(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    """Debit with retry logic for concurrency safety"""
    return await _execute_with_retry("debit_wallet", _do_debit_wallet, session, user_id, amount)

async def get_ledger_service(session: AsyncSession, user_id: uuid.UUID) -> Tuple[List[Transaction], Decimal, str]:
    """Read-only: no locking needed"""
    wallet, user = await get_wallet_by_user_id_service(session, user_id)
    result = await session.execute(
        select(Transaction)
        .where(Transaction.wallet_id == wallet.id)
        .order_by(Transaction.created_at.desc())
    )
    transactions: List[Transaction] = result.scalars().all()
    logger.debug(
        "Ledger retrieved",
        extra={
            "wallet_id": wallet.id,
            "user_id": str(user_id),
            "username": user.username,
            "count": len(transactions),
            "balance": str(wallet.balance),
        },
    )
    return transactions, wallet.balance, user.username
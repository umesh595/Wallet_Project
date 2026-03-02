import uuid
from decimal import Decimal
from typing import List, Tuple, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import AsyncSession
from app.models import User, Wallet, Transaction
from app.logging_config import logger

class InsufficientFundsError(Exception):
    pass
class WalletNotFoundError(Exception):
    pass
class UserAlreadyExistsError(Exception):
    pass
class UserNotFoundError(Exception):
    pass

async def create_user_service(session: AsyncSession, username: str, email: str, full_name: Optional[str] = None) -> User:
    """Create a new user with validation"""
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
        raise UserAlreadyExistsError(f"User with username '{username}' or email '{email}' already exists")
    user = User(
        username=username,
        email=email,
        full_name=full_name,
        is_active=True
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("User created successfully", extra={"user_id": str(user.id), "username": user.username})
    return user

async def get_user_by_username_service(session: AsyncSession, username: str) -> User:
    """Retrieve user by username"""
    result = await session.execute(select(User).where(User.username == username, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("User not found by username", extra={"username": username})
        raise UserNotFoundError(f"No active user found with username: {username}")
    return user

async def get_user_by_id_service(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Retrieve user by ID"""
    result = await session.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("User not found by ID", extra={"user_id": str(user_id)})
        raise UserNotFoundError(f"No active user found with ID: {user_id}")
    return user

async def create_wallet_for_user_service(session: AsyncSession, user_id: uuid.UUID) -> Wallet:
    """Create wallet for an existing user by user ID"""
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

async def get_wallet_by_user_id_service(session: AsyncSession, user_id: uuid.UUID) -> Tuple[Wallet, User]:
    """Get wallet and associated user by user ID"""
    user = await get_user_by_id_service(session, user_id)
    result = await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        logger.warning("Wallet not found for user", extra={"user_id": str(user_id)})
        raise WalletNotFoundError(f"No wallet found for user ID: {user_id}")
    
    return wallet, user

async def credit_wallet_service(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    """Credit money to wallet with atomic ledger entry"""
    async with session.begin():
        wallet, user = await get_wallet_by_user_id_service(session, user_id)
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
            },
        )
        return wallet

async def debit_wallet_service(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    """Debit money from wallet with atomic ledger entry"""
    async with session.begin():
        wallet, user = await get_wallet_by_user_id_service(session, user_id)
        if wallet.balance < amount:
            logger.warning(
                "Insufficient funds for debit",
                extra={
                    "wallet_id": wallet.id,
                    "user_id": str(user_id),
                    "username": user.username,
                    "requested": str(amount),
                    "available": str(wallet.balance),
                },
            )
            raise InsufficientFundsError(
                f"Insufficient funds. Balance: {wallet.balance}, Requested: {amount}"
            )
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
            },
        )
        return wallet

async def get_ledger_service(
    session: AsyncSession, user_id: uuid.UUID
) -> Tuple[List[Transaction], Decimal, str]:
    """
    Retrieve transaction ledger for a user's wallet.
    Returns:
        Tuple[List[Transaction], Decimal, str]: (transactions, balance, username)
    """
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
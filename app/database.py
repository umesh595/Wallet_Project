from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import event, text
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=30,          
    max_overflow=60,
    pool_timeout=30,
    execution_options={
        "isolation_level": "READ COMMITTED",
    }
)
@event.listens_for(engine.sync_engine, "connect")
def set_lock_timeout(dbapi_conn, connection_record):
    """Set lock_timeout on every new PostgreSQL connection"""
    try:
        cursor = dbapi_conn.cursor()
        cursor.execute("SET lock_timeout = '5000ms'")  
        cursor.close()
    except Exception as e:
        import logging
        logging.warning(f"Failed to set lock_timeout: {e}")

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

Base = declarative_base()

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def shutdown_db():
    await engine.dispose()
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from polybot.config.settings import settings

# Create async engine
# Note: DATABASE_URL should be postgresql+asyncpg://...
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False, # Set to True for SQL logging
    future=True
)

async_session_maker = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency to provide an async database session.
    """
    async with async_session_maker() as session:
        yield session

async def init_db():
    """
    Initializes the database (creates tables). 
    Intended to be run on startup or via alembic.
    """
    from sqlmodel import SQLModel
    # Import schemas so they are registered with SQLModel
    from polybot.db import schemas 
    
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

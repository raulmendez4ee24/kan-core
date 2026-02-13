import os
from functools import lru_cache
from typing import AsyncGenerator

from cryptography.fernet import Fernet, InvalidToken

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/kan_core",
)
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in {"1", "true", "yes"}

engine = create_async_engine(DATABASE_URL, echo=SQL_ECHO, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class SecurityManager:
    """Centralized encryption/decryption using a master key."""

    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(master_key.encode())

    def encrypt_token(self, plain_text: str) -> str:
        return self._fernet.encrypt(plain_text.encode()).decode()

    def decrypt_token(self, cipher_text: str) -> str:
        try:
            return self._fernet.decrypt(cipher_text.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Invalid cipher text") from exc


@lru_cache(maxsize=1)
def get_security_manager() -> SecurityManager:
    master_key = os.getenv("MASTER_KEY") or os.getenv("DATA_ENCRYPTION_KEY")
    if not master_key:
        raise RuntimeError("MASTER_KEY is not set")
    return SecurityManager(master_key)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables if AUTO_CREATE_DB is enabled. Prefer migrations in production."""
    from models import Base as ModelsBase  # local import to avoid circular refs

    async with engine.begin() as conn:
        await conn.run_sync(ModelsBase.metadata.create_all)

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, get_security_manager


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)

    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    shopify_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    alpaca_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    persona: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    llm_preferences: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="gemini-1.5-flash",
        server_default="gemini-1.5-flash",
    )
    temperature: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.4, server_default=text("0.4")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def set_api_key(self, value: str) -> None:
        self.api_key_encrypted = get_security_manager().encrypt_token(value)

    def get_api_key(self) -> str:
        return get_security_manager().decrypt_token(self.api_key_encrypted)

    def set_shopify_token(self, value: Optional[str]) -> None:
        self.shopify_token_encrypted = (
            None if value is None else get_security_manager().encrypt_token(value)
        )

    def get_shopify_token(self) -> Optional[str]:
        if not self.shopify_token_encrypted:
            return None
        return get_security_manager().decrypt_token(self.shopify_token_encrypted)

    def set_meta_token(self, value: Optional[str]) -> None:
        self.meta_token_encrypted = (
            None if value is None else get_security_manager().encrypt_token(value)
        )

    def get_meta_token(self) -> Optional[str]:
        if not self.meta_token_encrypted:
            return None
        return get_security_manager().decrypt_token(self.meta_token_encrypted)

    def set_alpaca_token(self, value: Optional[str]) -> None:
        self.alpaca_token_encrypted = (
            None if value is None else get_security_manager().encrypt_token(value)
        )

    def get_alpaca_token(self) -> Optional[str]:
        if not self.alpaca_token_encrypted:
            return None
        return get_security_manager().decrypt_token(self.alpaca_token_encrypted)


class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), index=True
    )
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), index=True
    )
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ClientCreate(BaseModel):
    name: str
    api_key: str
    shopify_token: Optional[str] = None
    meta_token: Optional[str] = None
    alpaca_token: Optional[str] = None
    system_prompt: Optional[str] = None
    model_name: str = "gemini-1.5-flash"
    temperature: float = 0.4
    persona: Dict[str, Any] = Field(default_factory=dict)
    llm_preferences: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ClientRead(BaseModel):
    id: uuid.UUID
    name: str
    persona: Dict[str, Any]
    llm_preferences: Dict[str, Any]
    system_prompt: Optional[str] = None
    model_name: str
    temperature: float
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True
        from_attributes = True

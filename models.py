import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True, index=True
    )
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    resource_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="ok", server_default="ok"
    )
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audit_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
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

    model_config = ConfigDict(from_attributes=True)


class ApiIntegration(Base):
    __tablename__ = "api_integrations"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_api_integrations_client_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    auth_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="none", server_default="none"
    )
    header_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    docs_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    openapi_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    openapi_spec: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    last_workflow_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_workflow_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    integration_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )

    def set_secret(self, value: Optional[str]) -> None:
        if value is None:
            self.secret_encrypted = None
            return
        self.secret_encrypted = get_security_manager().encrypt_token(value)

    def get_secret(self) -> Optional[str]:
        token = self.secret_encrypted
        if not token:
            return None
        return get_security_manager().decrypt_token(token)


class IntegrationMemory(Base):
    __tablename__ = "integration_memory"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "function_name",
            "provider_id",
            "industry",
            name="uq_integration_memory_client_function_provider_industry",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True, index=True
    )
    function_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    avg_duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    memory_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class IntegrationRunLog(Base):
    __tablename__ = "integration_run_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    function_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    integration_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    auto_repair_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class CrmLead(Base):
    __tablename__ = "crm_leads"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_crm_leads_org_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="new", server_default="new", index=True
    )
    score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    conversion_probability: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    source: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    owner: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    tags: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    lead_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    last_interaction_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class CrmInteraction(Base):
    __tablename__ = "crm_interactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_leads.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(
        String(20), nullable=False, default="inbound", server_default="inbound"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    interaction_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class FollowUpJob(Base):
    __tablename__ = "follow_up_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_leads.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scheduled", server_default="scheduled", index=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class BusinessEvent(Base):
    __tablename__ = "business_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class RuntimeRun(Base):
    __tablename__ = "runtime_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    result: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_runtime_events_run_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class RuntimeHookRegistration(Base):
    __tablename__ = "runtime_hook_registrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="annotate", server_default="annotate"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), index=True
    )
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class DesktopAgent(Base):
    __tablename__ = "desktop_agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capabilities: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    connected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class ExecutionJob(Base):
    __tablename__ = "execution_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    leased_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class DesktopCommand(Base):
    __tablename__ = "desktop_commands"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("desktop_agents.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    args: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=30000, server_default=text("30000"))
    requested_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    command_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


class DesktopCommandResult(Base):
    __tablename__ = "desktop_command_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("desktop_agents.id"), nullable=False, index=True
    )
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("desktop_commands.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )


_DYNAMIC_MODEL_TABLES: Dict[str, str] = {
    "AlertIncident": "alert_incidents",
    "AlertRule": "alert_rules",
    "AnalyticsInsight": "analytics_insights",
    "AnalyticsUpload": "analytics_uploads",
    "AutonomyV2Benchmark": "autonomy_v2_benchmarks",
    "BusinessMetricsSnapshot": "business_metrics_snapshots",
    "CollectionsAction": "collections_actions",
    "CollectionsCase": "collections_cases",
    "ContentAsset": "content_assets",
    "ContentPlan": "content_plans",
    "CrmInteraction": "crm_interactions",
    "CrmLead": "crm_leads",
    "CustomerProfile": "customer_profiles",
    "DesktopAgent": "desktop_agents",
    "DesktopCommand": "desktop_commands",
    "DesktopCommandResult": "desktop_command_results",
    "EvidenceAsset": "evidence_assets",
    "ExecutionJob": "execution_jobs",
    "FollowUpJob": "follow_up_jobs",
    "GlobalLearning": "global_learnings",
    "InternalDoc": "internal_docs",
    "InternalDocChunk": "internal_doc_chunks",
    "InternalQuery": "internal_queries",
    "Mission": "missions",
    "MissionStep": "mission_steps",
    "Objective": "objectives",
    "ObjectiveRun": "objective_runs",
    "OmnichannelMessage": "omnichannel_messages",
    "OmnichannelThread": "omnichannel_threads",
    "OrgMembership": "org_memberships",
    "OrgPolicy": "org_policies",
    "Permission": "permissions",
    "PredictionRecord": "predictions",
    "ProcessAuditFinding": "process_audit_findings",
    "ProcessAuditJob": "process_audit_jobs",
    "Recommendation": "recommendations",
    "RecommendationAction": "recommendation_actions",
    "RecruitingCandidate": "recruiting_candidates",
    "RecruitingInterview": "recruiting_interviews",
    "RecruitingScore": "recruiting_scores",
    "RoiSnapshot": "roi_snapshots",
    "Role": "roles",
    "RolePermission": "role_permissions",
    "SalesPipelineEvent": "sales_pipeline_events",
    "Task": "tasks",
    "TaskRun": "task_runs",
    "TaskRunStep": "task_run_steps",
    "UIState": "ui_states",
    "UITransition": "ui_transitions",
    "User": "users",
    "WorkflowPlaybook": "workflow_playbooks",
    "WorkflowPlaybookRun": "workflow_playbook_runs",
    "WorkflowRecording": "workflow_recordings",
    "WorkflowRecordingStep": "workflow_recording_steps",
}


def _placeholder_columns() -> Dict[str, Any]:
    return {
        "id": mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        "organization_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "client_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "user_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "role_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "permission_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "objective_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "objective_run_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "recommendation_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "mission_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "lead_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "agent_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "command_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "task_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "run_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "step_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "recording_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "playbook_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "created_by_user_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "updated_by_user_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "requested_by_user_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "approved_by_user_id": mapped_column(UUID(as_uuid=True), nullable=True, index=True),
        "email": mapped_column(String(320), nullable=True, index=True),
        "full_name": mapped_column(String(240), nullable=True),
        "password_hash": mapped_column(Text, nullable=True),
        "name": mapped_column(String(240), nullable=True, index=True),
        "agent_name": mapped_column(String(240), nullable=True, index=True),
        "title": mapped_column(String(255), nullable=True),
        "status": mapped_column(String(64), nullable=True, index=True),
        "kind": mapped_column(String(80), nullable=True, index=True),
        "provider": mapped_column(String(120), nullable=True, index=True),
        "provider_id": mapped_column(String(120), nullable=True, index=True),
        "pattern_key": mapped_column(String(255), nullable=True, index=True),
        "integration": mapped_column(String(120), nullable=True),
        "function_name": mapped_column(String(160), nullable=True, index=True),
        "event_type": mapped_column(String(120), nullable=True, index=True),
        "action": mapped_column(String(160), nullable=True, index=True),
        "resource": mapped_column(String(160), nullable=True, index=True),
        "resource_id": mapped_column(String(160), nullable=True, index=True),
        "source": mapped_column(String(120), nullable=True, index=True),
        "channel": mapped_column(String(120), nullable=True, index=True),
        "entity_id": mapped_column(String(160), nullable=True, index=True),
        "session_id": mapped_column(String(160), nullable=True, index=True),
        "role": mapped_column(String(80), nullable=True, index=True),
        "summary": mapped_column(Text, nullable=True),
        "detail": mapped_column(Text, nullable=True),
        "error": mapped_column(Text, nullable=True),
        "error_code": mapped_column(String(120), nullable=True, index=True),
        "error_message": mapped_column(Text, nullable=True),
        "content": mapped_column(Text, nullable=True),
        "environment": mapped_column(String(32), nullable=True, index=True),
        "timezone": mapped_column(String(80), nullable=True),
        "step_order": mapped_column(Integer, nullable=True),
        "current_step": mapped_column(Integer, nullable=True),
        "attempts": mapped_column(Integer, nullable=True),
        "window_minutes": mapped_column(Integer, nullable=True),
        "input_tokens": mapped_column(Integer, nullable=True),
        "output_tokens": mapped_column(Integer, nullable=True),
        "total_leads": mapped_column(Integer, nullable=True),
        "total_conversations": mapped_column(Integer, nullable=True),
        "total_messages": mapped_column(Integer, nullable=True),
        "total_appointments": mapped_column(Integer, nullable=True),
        "total_sales": mapped_column(Integer, nullable=True),
        "total_errors": mapped_column(Integer, nullable=True),
        "total_api_failures": mapped_column(Integer, nullable=True),
        "priority": mapped_column(Integer, nullable=True),
        "risk_score": mapped_column(Float, nullable=True),
        "confidence": mapped_column(Float, nullable=True),
        "success_rate": mapped_column(Float, nullable=True),
        "avg_impact_score": mapped_column(Float, nullable=True),
        "cost_usd": mapped_column(Float, nullable=True),
        "duration_ms": mapped_column(Integer, nullable=True),
        "avg_duration_ms": mapped_column(Float, nullable=True),
        "avg_cost_usd": mapped_column(Float, nullable=True),
        "success_count": mapped_column(Integer, nullable=True),
        "failure_count": mapped_column(Integer, nullable=True),
        "attempt_number": mapped_column(Integer, nullable=True),
        "sample_size": mapped_column(Integer, nullable=True),
        "is_active": mapped_column(Boolean, nullable=True),
        "is_owner": mapped_column(Boolean, nullable=True),
        "auto_repair_used": mapped_column(Boolean, nullable=True),
        "fallback_used": mapped_column(Boolean, nullable=True),
        "requires_human_approval": mapped_column(Boolean, nullable=True),
        "timeout_ms": mapped_column(Integer, nullable=True),
        "payload": mapped_column(JSONB, nullable=False, default=dict),
        "result": mapped_column(JSONB, nullable=False, default=dict),
        "data": mapped_column(JSONB, nullable=False, default=dict),
        "args": mapped_column(JSONB, nullable=False, default=dict),
        "capabilities": mapped_column(JSONB, nullable=False, default=dict),
        "policy": mapped_column(JSONB, nullable=False, default=dict),
        "metrics": mapped_column(JSONB, nullable=False, default=dict),
        "diagnostics": mapped_column(JSONB, nullable=False, default=dict),
        "definition": mapped_column(JSONB, nullable=False, default=dict),
        "tags": mapped_column(JSONB, nullable=False, default=list),
        "context": mapped_column(JSONB, nullable=False, default=dict),
        "steps": mapped_column(JSONB, nullable=False, default=list),
        "llm_preferences": mapped_column(JSONB, nullable=False, default=dict),
        "persona": mapped_column(JSONB, nullable=False, default=dict),
        "reasoning": mapped_column(Text, nullable=True),
        "metadata_json": mapped_column("metadata", JSONB, nullable=False, default=dict),
        "command_metadata": mapped_column(JSONB, nullable=False, default=dict),
        "memory_metadata": mapped_column(JSONB, nullable=False, default=dict),
        "learning_metadata": mapped_column(JSONB, nullable=False, default=dict),
        "integration_metadata": mapped_column(JSONB, nullable=False, default=dict),
        "openapi_spec": mapped_column(JSONB, nullable=True),
        "openapi_url": mapped_column(Text, nullable=True),
        "docs_url": mapped_column(Text, nullable=True),
        "base_url": mapped_column(Text, nullable=True),
        "auth_type": mapped_column(String(64), nullable=True),
        "header_name": mapped_column(String(128), nullable=True),
        "secret_encrypted": mapped_column(Text, nullable=True),
        "integration_name": mapped_column(String(160), nullable=True, index=True),
        "last_workflow_id": mapped_column(String(160), nullable=True, index=True),
        "last_workflow_url": mapped_column(Text, nullable=True),
        "industry": mapped_column(String(120), nullable=True, index=True),
        "last_status": mapped_column(String(64), nullable=True),
        "last_error_code": mapped_column(String(120), nullable=True, index=True),
        "window_start": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "window_end": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "scheduled_for": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "executed_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "started_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "finished_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "approved_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "issued_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "received_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "last_heartbeat_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "connected_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "disconnected_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "last_used_at": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "timestamp": mapped_column(DateTime(timezone=True), nullable=True, index=True),
        "created_at": mapped_column(DateTime(timezone=True), server_default=func.now(), index=True),
        "updated_at": mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True),
    }


def _iter_column_names(model_cls: type) -> set[str]:
    table = getattr(model_cls, "__table__", None)
    if table is None:
        return set()
    return {str(col.name) for col in table.columns}


def _create_dynamic_model(name: str) -> type:
    tablename = _DYNAMIC_MODEL_TABLES[name]
    attrs: Dict[str, Any] = {"__tablename__": tablename, "__allow_unmapped__": True}
    attrs.update(_placeholder_columns())

    def _init(self: Any, **kwargs: Any) -> None:
        aliases = {
            "metadata": "metadata_json",
            "model_metadata": "metadata_json",
            "metadata_json": "metadata_json",
        }
        columns = _iter_column_names(self.__class__)
        for key, value in kwargs.items():
            target = aliases.get(key, key)
            if target in columns:
                setattr(self, target, value)

    attrs["__init__"] = _init
    if name == "ApiIntegration":
        def _set_secret(self: Any, value: Optional[str]) -> None:
            if value is None:
                self.secret_encrypted = None
                return
            self.secret_encrypted = get_security_manager().encrypt_token(value)

        def _get_secret(self: Any) -> Optional[str]:
            token = getattr(self, "secret_encrypted", None)
            if not token:
                return None
            return get_security_manager().decrypt_token(token)

        attrs["set_secret"] = _set_secret
        attrs["get_secret"] = _get_secret
    return type(name, (Base,), attrs)


# ---------------------------------------------------------------------------
# Cold Email Ops — DB layer (Opción B: Sheets + DB en paralelo)
# ---------------------------------------------------------------------------

class ColdEmailLead(Base):
    """Mirror of the 'leads' tab. Upserted on every /cold-email-ops/run."""
    __tablename__ = "ce_leads"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "sheet_lead_id", name="uq_ce_leads_sheet"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sheet_lead_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    # From Sheet — overwritten on each sync
    company: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    linkedin: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    segment: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    last_contacted_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    personalization_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_validation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ColdEmailInbox(Base):
    """Mirror of the 'inboxes' tab. Upserted on every /cold-email-ops/run."""
    __tablename__ = "ce_inboxes"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "sheet_inbox_id", name="uq_ce_inboxes_sheet"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sheet_inbox_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    from_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    from_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    domain: Mapped[Optional[str]] = mapped_column(String(253), nullable=True, index=True)
    warmup_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    daily_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sent_today: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    health_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rotation_group: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ColdEmailCampaign(Base):
    """Mirror of the 'campaign' tab. Upserted on every /cold-email-ops/run."""
    __tablename__ = "ce_campaigns"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "sheet_campaign_id", name="uq_ce_campaigns_sheet"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sheet_campaign_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    offer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_segment: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    sequence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    spacing_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    send_window_localtime: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    max_new_leads_per_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    personalization_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ColdEmailDomain(Base):
    """Mirror of the 'domains' tab. Upserted on every /cold-email-ops/run."""
    __tablename__ = "ce_domains"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "domain", name="uq_ce_domains_sheet"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False, index=True)

    registrar: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    dns_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    spf_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dkim_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dmarc_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tracking_subdomain_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tls_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    monthly_cost_estimate: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ColdEmailRun(Base):
    """One record per execution of /cold-email-ops/run."""
    __tablename__ = "ce_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)

    # status: running | ok | error | blocked
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running", index=True
    )
    leads_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    inboxes_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    leads_queued: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    bloqueos: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    result_summary: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ColdEmailSend(Base):
    """One record per lead queued/attempted per run. Fields are frozen at queue time."""
    __tablename__ = "ce_sends"
    __table_args__ = (
        UniqueConstraint("run_id", "sheet_lead_id", name="uq_ce_sends_run_lead"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ce_runs.id"), nullable=False, index=True
    )
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Snapshot from Sheet at queue time — never mutated after insert
    sheet_lead_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    company: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    segment: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    from_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    personalization_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_validation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Send outcome — updated externally after actual send
    # status: queued | sent | bounced | failed | skipped
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default="queued", index=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AutonomousCase(Base):
    __tablename__ = "autonomous_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    case_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="open", server_default="open", index=True
    )
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    current_goal: Mapped[str] = mapped_column(Text, nullable=False)
    business_context: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    world_state: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    last_decision: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    last_execution_result: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    resolution_summary: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False, index=True
    )


class AutonomousCaseStep(Base):
    __tablename__ = "autonomous_case_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autonomous_cases.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="planned", server_default="planned", index=True
    )
    decision_payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    execution_payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    execution_result: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    outcome_evaluation: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class BusinessStateSnapshot(Base):
    __tablename__ = "business_state_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    snapshot_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="operational", server_default="operational", index=True
    )
    state_payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class OperationMemoryEntry(Base):
    __tablename__ = "operation_memory_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autonomous_cases.id"), nullable=True, index=True
    )
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ExecutionLease(Base):
    __tablename__ = "execution_leases"
    __table_args__ = (
        UniqueConstraint("organization_id", "lease_key", name="uq_execution_leases_org_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autonomous_cases.id"), nullable=False, index=True
    )
    lease_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    leased_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False, index=True
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_idempotency_records_org_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autonomous_cases.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    step_hash: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    result_payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


def __getattr__(name: str) -> Any:
    if name in _DYNAMIC_MODEL_TABLES:
        model = _create_dynamic_model(name)
        globals()[name] = model
        return model
    raise AttributeError(name)

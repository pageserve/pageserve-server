import uuid

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func

# Timezone-aware timestamp shorthand (maps to TIMESTAMPTZ on Postgres).
TZ = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255))
    role = Column(String(20), nullable=False, default="member")
    is_active = Column(Boolean, nullable=False, default=True)
    avatar_url = Column(String(500))
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TZ, server_default=func.now())
    last_login_at = Column(TZ)

    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys = relationship("ApiKey", back_populates="user")
    memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(255), nullable=False, unique=True)
    user_agent = Column(Text)
    ip_address = Column(String(45))
    expires_at = Column(TZ, nullable=False)
    created_at = Column(TZ, server_default=func.now())
    last_used_at = Column(TZ)
    revoked_at = Column(TZ)

    user = relationship("User", back_populates="refresh_tokens")


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    created_at = Column(TZ, server_default=func.now())
    updated_at = Column(TZ, onupdate=func.now())

    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="project", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    webhooks = relationship("Webhook", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    role = Column(String(20), nullable=False, default="member")
    created_at = Column(TZ, server_default=func.now())

    user = relationship("User", back_populates="memberships")
    project = relationship("Project", back_populates="members")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    public_key = Column(String(60), nullable=False, unique=True)
    secret_hash = Column(String(255), nullable=False, unique=True)
    secret_prefix = Column(String(20), nullable=False)
    key_type = Column(String(10), nullable=False, default="live")
    scopes = Column(ARRAY(Text), nullable=False, default=["read", "write"])
    is_active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(TZ)
    last_used_ip = Column(String(45))
    request_count = Column(BigInteger, nullable=False, default=0)
    expires_at = Column(TZ)
    created_at = Column(TZ, server_default=func.now())

    project = relationship("Project", back_populates="api_keys")
    user = relationship("User", back_populates="api_keys")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(500), nullable=False)
    doc_type = Column(String(10), nullable=False, default="pdf")
    page_count = Column(Integer)
    file_size = Column(BigInteger)
    description = Column(Text, nullable=False, default="")
    tags = Column(ARRAY(Text), nullable=False, default=list)
    language = Column(String(10), nullable=False, default="vi")
    status = Column(String(20), nullable=False, default="pending", index=True)
    error_msg = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TZ, server_default=func.now())
    updated_at = Column(TZ, onupdate=func.now())

    project = relationship("Project", back_populates="documents")
    structure = relationship(
        "Structure",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )
    pages = relationship("Page", back_populates="document", cascade="all, delete-orphan")


class Structure(Base):
    __tablename__ = "structure"

    doc_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tree = Column(JSONB, nullable=False)
    updated_at = Column(TZ, server_default=func.now())

    document = relationship("Document", back_populates="structure")


class Page(Base):
    __tablename__ = "pages"

    doc_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_num = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False, default="")

    document = relationship("Document", back_populates="pages")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"))
    action = Column(String(50), nullable=False)
    resource = Column(String(255))
    detail = Column(JSONB)
    ip_address = Column(String(45))
    created_at = Column(TZ, server_default=func.now(), index=True)


class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    url = Column(String(500), nullable=False)
    secret = Column(String(100))
    events = Column(
        ARRAY(Text),
        nullable=False,
        default=lambda: ["document.completed", "document.failed"],
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TZ, server_default=func.now())

    project = relationship("Project", back_populates="webhooks")


class PlaygroundHistory(Base):
    __tablename__ = "playground_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    question = Column(Text, nullable=False)
    mode = Column(String(10), nullable=False, default="answer")
    response = Column(JSONB)
    elapsed_ms = Column(Integer)
    starred = Column(Boolean, nullable=False, default=False)
    created_at = Column(TZ, server_default=func.now())

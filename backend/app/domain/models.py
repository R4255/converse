from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EMBED_DIM = 768


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    address: Mapped[str] = mapped_column(Text)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    brand: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(60), index=True)
    subcategory: Mapped[str] = mapped_column(String(60), index=True)
    description: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    rating: Mapped[float] = mapped_column(Float, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    popularity: Mapped[float] = mapped_column(Float, default=0)
    embedding = mapped_column(Vector(EMBED_DIM), nullable=True)

    variants: Mapped[list["Variant"]] = relationship(
        back_populates="product", order_by="Variant.price", lazy="selectin"
    )


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    label: Mapped[str] = mapped_column(String(40))  # "1 L", "500 g", "Pack of 6"
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    mrp: Mapped[float] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(Integer, default=100)

    product: Mapped[Product] = relationship(back_populates="variants", lazy="joined")


class DeliveryPreference(Base):
    """The delivery slot chosen for the user's current cart (None = express, ~10 minutes)."""

    __tablename__ = "delivery_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    slot_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    author: Mapped[str] = mapped_column(String(80))
    rating: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)


class CartItem(Base):
    __tablename__ = "cart_items"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    variant: Mapped[Variant] = relationship(lazy="joined")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="delivered")
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    total: Mapped[float] = mapped_column(Numeric(10, 2))
    address: Mapped[str] = mapped_column(Text)
    delivery_slot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"))
    product_name: Mapped[str] = mapped_column(String(160))
    variant_label: Mapped[str] = mapped_column(String(40))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class IdempotencyKey(Base):
    """Replay-safe mutations: the same key returns the stored result instead of re-applying."""

    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PendingAction(Base):
    """Side-effecting actions proposed by the agent that need explicit user confirmation."""

    __tablename__ = "pending_actions"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    session_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    description: Mapped[str] = mapped_column(Text)
    schedule_expression: Mapped[str] = mapped_column(String(80))
    items: Mapped[list] = mapped_column(JSONB, default=list)
    schedule_arn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    """Transactional outbox: written in the same transaction as the state change, relayed to SQS."""

    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    queue: Mapped[str] = mapped_column(String(60))
    body: Mapped[dict] = mapped_column(JSONB)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessedEvent(Base):
    """Consumer-side dedupe for at-least-once SQS delivery."""

    __tablename__ = "processed_events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    memory: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    input: Mapped[str] = mapped_column(Text)
    reply: Mapped[str] = mapped_column(Text, default="")
    route: Mapped[str] = mapped_column(String(20))  # "fast" | "agent"
    model: Mapped[str] = mapped_column(String(60))
    steps: Mapped[list] = mapped_column(JSON, default=list)
    ui_actions: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

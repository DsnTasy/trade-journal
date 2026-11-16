"""Database tables.

Key ideas:
- Every product moves through a fixed pipeline of statuses.
- Every number we show (a metric) records WHERE it came from and WHEN.
- Every real-world action goes through an ApprovalRequest first.
- Every paid call writes a CostEntry; every action writes a LogEntry.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProductStatus(str, enum.Enum):
    RESEARCHED = "researched"
    SHORTLISTED = "shortlisted"
    SUPPLIER_CHOSEN = "supplier_chosen"
    SAMPLE_ORDERED = "sample_ordered"
    CONTENT_READY = "content_ready"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


# The normal order a product moves through (REJECTED can happen from any step).
PIPELINE = [
    ProductStatus.RESEARCHED,
    ProductStatus.SHORTLISTED,
    ProductStatus.SUPPLIER_CHOSEN,
    ProductStatus.SAMPLE_ORDERED,
    ProductStatus.CONTENT_READY,
    ProductStatus.APPROVED,
    ProductStatus.PUBLISHED,
]


class ActionType(str, enum.Enum):
    """Real-world actions. None of these ever happen without an approved request."""

    PUBLISH_PRODUCT = "publish_product"
    LAUNCH_AD = "launch_ad"
    PLACE_ORDER = "place_order"
    SEND_EMAIL = "send_email"
    SPEND_MONEY = "spend_money"
    PAID_API_CALL = "paid_api_call"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, native_enum=False), default=ProductStatus.RESEARCHED
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    metrics: Mapped[list[Metric]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Metric(Base):
    """One sourced data point, e.g. 'search_interest = 72 (Google Trends CSV, 2026-11-16)'."""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String(100))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = source returned nothing
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source: Mapped[str] = mapped_column(String(200))  # required: where did this come from?
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped[Product] = relationship(back_populates="metrics")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType, native_enum=False))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    estimated_cost_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False), default=ApprovalStatus.PENDING
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CostEntry(Base):
    __tablename__ = "cost_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    service: Mapped[str] = mapped_column(String(100))  # e.g. "anthropic"
    operation: Mapped[str] = mapped_column(String(200))  # e.g. "content:product_description"
    cost_gbp: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(10))  # INFO / WARNING / ERROR
    module: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cost_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

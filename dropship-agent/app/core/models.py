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
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Filled in by product research (phase 2):
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1: how much data we had
    score_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"for": [...], "against": [...]}
    compliance_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Filled in by supplier finder / profit calculator (phase 3):
    chosen_supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profit_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # your edited assumptions
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


class Supplier(Base):
    """One supplier offer for one product. Numbers come from the supplier's listing (see `source`)."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str | None] = mapped_column(String(60), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    warehouse: Mapped[str | None] = mapped_column(String(20), nullable=True)  # UK / EU / US / CN / other
    unit_cost_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    shipping_cost_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    shipping_days_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shipping_days_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orders_sold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    return_policy_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responds_within_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_ukca_ce: Mapped[str | None] = mapped_column(String(10), nullable=True)  # yes / no / None=unknown
    has_safety_docs: Mapped[str | None] = mapped_column(String(10), nullable=True)
    has_branding_info: Mapped[str | None] = mapped_column(String(10), nullable=True)
    original_price: Mapped[str | None] = mapped_column(String(60), nullable=True)  # e.g. "4.10 USD"
    source: Mapped[str] = mapped_column(String(300))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    red_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    profit: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # latest profit calculation

    product: Mapped[Product] = relationship()
    messages: Mapped[list[SupplierMessage]] = relationship(back_populates="supplier", cascade="all, delete-orphan")


class MessageStatus(str, enum.Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    SENT = "sent"
    REPLIED = "replied"


class SupplierMessage(Base):
    """Outreach emails. Drafted automatically, NEVER sent without approval."""

    __tablename__ = "supplier_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus, native_enum=False), default=MessageStatus.DRAFT)
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    supplier: Mapped[Supplier] = relationship(back_populates="messages")


class CompetitorReport(Base):
    """Research-only report on a competitor store. Stores FACTS and our own summaries only:
    never their images, product descriptions or site code."""

    __tablename__ = "competitor_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String(200))
    # allowed = collected automatically; forbidden = their robots.txt/terms say no;
    # needs_terms_check = couldn't find terms; manual = facts entered by you; error = couldn't reach it
    status: Mapped[str] = mapped_column(String(30))
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(60), nullable=True)
    auto_facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manual_facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_themes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    insights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # AI summary, our own words
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # your notes: positioning, ad angles
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)  # every URL we fetched, when
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def facts(self) -> dict:
        """Your manual entries win over automatic ones."""
        return {**(self.auto_facts or {}), **{k: v for k, v in (self.manual_facts or {}).items() if v is not None}}

"""Loads settings from config.yaml (non-secret) and .env (secrets).

Why two files?
- config.yaml is safe to commit: budgets, models, business assumptions.
- .env holds API keys and is NEVER committed (it's in .gitignore).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# The project root is the folder that contains run.py and config.yaml.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BudgetConfig(BaseModel):
    monthly_cap_gbp: float = Field(25.0, gt=0, description="Hard monthly cap for ALL paid API calls.")
    warn_at_fraction: float = Field(0.8, gt=0, le=1, description="Warn when this share of the cap is used.")
    approval_threshold_gbp: float = Field(
        1.0, ge=0, description="Any single paid call estimated above this needs approval in the dashboard."
    )


class ModelPrice(BaseModel):
    """Price in US dollars per 1 million tokens (Anthropic bills in USD)."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_read_usd_per_mtok: float = 0.0
    cache_write_usd_per_mtok: float = 0.0


class LLMConfig(BaseModel):
    default_model: str = "claude-opus-5-5"
    # Per-task overrides, e.g. {"scoring": "claude-haiku-5-5"}. Empty = use default_model.
    task_models: dict[str, str] = Field(default_factory=dict)
    effort: str = "medium"
    max_tokens: int = 4000
    usd_to_gbp: float = Field(0.79, gt=0, description="Exchange-rate assumption used to convert API costs.")
    prices: dict[str, ModelPrice] = Field(default_factory=dict)

    def model_for(self, task: str) -> str:
        return self.task_models.get(task) or self.default_model


class BusinessConfig(BaseModel):
    currency: str = "GBP"
    vat_registered: bool = False
    vat_rate: float = 0.20
    vat_registration_threshold_gbp: float = 90000.0
    usd_to_gbp: float = Field(0.79, gt=0, description="Used when a supplier lists prices in USD.")
    eur_to_gbp: float = Field(0.85, gt=0, description="Used when a supplier lists prices in EUR.")


class ResearchWeights(BaseModel):
    demand: float = 0.30
    trend: float = 0.20
    margin: float = 0.30
    competition: float = 0.20


class ResearchConfig(BaseModel):
    candidates_file: str = "research/candidates.csv"
    trends_dir: str = "research/trends"
    weights: ResearchWeights = Field(default_factory=ResearchWeights)
    target_gross_margin: float = Field(0.60, gt=0, le=1, description="Rough margin that scores full marks.")
    competition_high: int = Field(50, gt=0, description="This many competitor stores = maximum competition.")
    saturation_high: int = Field(30, gt=0, description="This many advertisers = maximum saturation penalty.")
    saturation_max_penalty: float = Field(25.0, ge=0, description="Most points saturation can remove.")
    seasonal_ratio: float = Field(2.5, gt=1, description="Peak / average interest above this = seasonal.")
    seasonal_penalty: float = Field(5.0, ge=0)
    low_confidence_below: float = Field(0.5, ge=0, le=1)
    avoid_categories: list[str] = Field(default_factory=list)
    # Meta Ad Library (optional, needs META_AD_LIBRARY_TOKEN in .env)
    meta_enabled: bool = False
    meta_api_version: str = "v21.0"
    meta_countries: list[str] = Field(default_factory=lambda: ["IE", "FR", "DE"])
    meta_max_pages: int = Field(3, ge=1, le=20)
    request_delay_seconds: float = Field(1.0, ge=0)


class ProfitConfig(BaseModel):
    """Default assumptions for the profit calculator. All editable in the dashboard per product."""
    payment_fee_pct: float = Field(2.0, ge=0, description="Card fee % (Shopify Payments, UK cards).")
    payment_fee_fixed_gbp: float = Field(0.25, ge=0)
    third_party_gateway_fee_pct: float = Field(0.0, ge=0, description="Extra Shopify fee if NOT using Shopify Payments.")
    platform_monthly_fee_gbp: float = Field(25.0, ge=0, description="Shopify plan + paid apps per month.")
    expected_orders_per_month: int = Field(50, gt=0, description="Used to spread the monthly fee per order.")
    packaging_gbp: float = Field(0.0, ge=0)
    refund_rate: float = Field(0.05, ge=0, le=1, description="Share of orders refunded.")
    chargeback_rate: float = Field(0.005, ge=0, le=1)
    chargeback_fee_gbp: float = Field(15.0, ge=0)
    cpa_gbp: float = Field(10.0, ge=0, description="Ad cost per sale: the biggest guess, edit it.")
    go_min_profit_gbp: float = Field(5.0, ge=0)
    go_min_margin: float = Field(0.15, ge=0, le=1)
    max_comfortable_roas: float = Field(3.0, gt=0, description="Break-even ROAS above this = hard to profit from ads.")


class SupplierWeights(BaseModel):
    price: float = 0.30
    delivery: float = 0.25
    warehouse: float = 0.15
    reliability: float = 0.15
    terms: float = 0.15


class SupplierConfig(BaseModel):
    suppliers_file: str = "research/suppliers.csv"
    weights: SupplierWeights = Field(default_factory=SupplierWeights)
    target_delivery_days: int = Field(7, gt=0, description="Delivery within this many days scores well.")
    slow_shipping_days: int = Field(15, gt=0, description="Flag anything this slow or slower.")
    red_flag_penalty: float = Field(5.0, ge=0, description="Points removed per red flag.")
    top_n: int = Field(3, gt=0)


class CompetitorConfig(BaseModel):
    # Identify ourselves honestly to the sites we visit.
    user_agent: str = "dropship-agent-research/0.4 (personal market research; low volume; respects robots.txt)"
    request_delay_seconds: float = Field(3.0, ge=0, description="Wait between requests to the same site.")
    cache_hours: float = Field(24, ge=0, description="Re-use a downloaded page for this long.")
    max_product_pages: int = Field(4, ge=1, le=20, description="Pages of 250 products to read at most.")
    timeout_seconds: float = Field(20, gt=0)


class StoreConfig(BaseModel):
    platform: str = "shopify"  # "shopify" (chosen) or "custom"


class Settings(BaseModel):
    # SAFETY: dry_run=True means nothing real happens (no publishing, emails, orders or ad spend).
    dry_run: bool = True
    database_url: str = "sqlite:///data/dropship.db"
    log_file: str = "logs/app.log"
    llm_cache_dir: str = "data/llm_cache"
    http_cache_dir: str = "data/http_cache"
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    business: BusinessConfig = Field(default_factory=BusinessConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    profit: ProfitConfig = Field(default_factory=ProfitConfig)
    suppliers: SupplierConfig = Field(default_factory=SupplierConfig)
    competitors: CompetitorConfig = Field(default_factory=CompetitorConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)

    # Secrets: filled from environment variables, never from config.yaml.
    anthropic_api_key: str | None = Field(default=None, exclude=True, repr=False)
    meta_ad_library_token: str | None = Field(default=None, exclude=True, repr=False)

    def path(self, relative: str) -> Path:
        """Turn a path from config.yaml into an absolute path inside the project."""
        p = Path(relative)
        return p if p.is_absolute() else PROJECT_ROOT / p


def load_settings(config_path: Path | None = None, env_path: Path | None = None) -> Settings:
    config_path = config_path or PROJECT_ROOT / "config.yaml"
    env_path = env_path or PROJECT_ROOT / ".env"
    load_dotenv(env_path, override=False)

    data: dict = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    settings = Settings(**data)
    settings.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    settings.meta_ad_library_token = os.environ.get("META_AD_LIBRARY_TOKEN") or None
    return settings


@lru_cache
def get_settings() -> Settings:
    """Shared settings for the running app (loaded once)."""
    return load_settings()

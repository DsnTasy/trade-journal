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


class Settings(BaseModel):
    # SAFETY: dry_run=True means nothing real happens (no publishing, emails, orders or ad spend).
    dry_run: bool = True
    database_url: str = "sqlite:///data/dropship.db"
    log_file: str = "logs/app.log"
    llm_cache_dir: str = "data/llm_cache"
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    business: BusinessConfig = Field(default_factory=BusinessConfig)

    # Secrets: filled from environment variables, never from config.yaml.
    anthropic_api_key: str | None = Field(default=None, exclude=True, repr=False)

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
    return settings


@lru_cache
def get_settings() -> Settings:
    """Shared settings for the running app (loaded once)."""
    return load_settings()

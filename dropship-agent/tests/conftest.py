"""Shared test setup: every test gets a fresh in-memory database. No real API calls."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import approvals  # noqa: E402
from app.core.config import BudgetConfig, LLMConfig, ModelPrice, Settings  # noqa: E402
from app.core.db import init_db, make_engine, make_session_factory  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(
        dry_run=True,
        database_url="sqlite://",
        log_file=str(tmp_path / "app.log"),
        llm_cache_dir=str(tmp_path / "llm_cache"),
        budget=BudgetConfig(monthly_cap_gbp=10.0, warn_at_fraction=0.8, approval_threshold_gbp=1.0),
        llm=LLMConfig(
            default_model="test-model",
            usd_to_gbp=1.0,  # 1:1 keeps the maths in tests easy to read
            prices={"test-model": ModelPrice(input_usd_per_mtok=1.0, output_usd_per_mtok=10.0)},
        ),
    )


@pytest.fixture
def engine():
    eng = make_engine("sqlite://")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset_executors():
    approvals.clear_executors()
    yield
    approvals.clear_executors()

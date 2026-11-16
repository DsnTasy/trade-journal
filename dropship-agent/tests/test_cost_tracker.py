from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.cost_tracker import BudgetExceeded, CostTracker
from app.core.models import CostEntry, LogEntry


def test_starts_at_zero(session, settings):
    status = CostTracker(session, settings.budget).status()
    assert status.spent_gbp == 0
    assert not status.warning and not status.blocked


def test_records_and_sums_spend(session, settings):
    t = CostTracker(session, settings.budget)
    t.record(service="anthropic", operation="a", cost_gbp=1.5)
    t.record(service="anthropic", operation="b", cost_gbp=2.0)
    assert t.month_spend() == pytest.approx(3.5)


def test_check_blocks_when_over_cap(session, settings):
    t = CostTracker(session, settings.budget)  # cap £10
    t.record(service="x", operation="y", cost_gbp=9.0)
    t.check(0.99)  # still fits
    with pytest.raises(BudgetExceeded):
        t.check(1.01)
    assert session.scalar(select(LogEntry).where(LogEntry.action == "blocked")) is not None


def test_warning_logged_once_when_crossing_80_percent(session, settings):
    t = CostTracker(session, settings.budget)
    t.record(service="x", operation="y", cost_gbp=7.0)
    assert not t.status().warning
    t.record(service="x", operation="y", cost_gbp=1.5)  # 85%
    t.record(service="x", operation="y", cost_gbp=0.5)  # 90%, no second warning
    assert t.status().warning
    warnings = session.scalars(select(LogEntry).where(LogEntry.action == "budget_warning")).all()
    assert len(warnings) == 1


def test_last_months_spend_does_not_count(session, settings):
    old = CostEntry(service="x", operation="old", cost_gbp=50.0,
                    created_at=datetime.now(UTC).replace(day=1) - timedelta(days=2))
    session.add(old)
    session.commit()
    assert CostTracker(session, settings.budget).month_spend() == 0


def test_needs_approval_above_threshold(session, settings):
    t = CostTracker(session, settings.budget)  # threshold £1
    assert not t.needs_approval(1.0)
    assert t.needs_approval(1.01)

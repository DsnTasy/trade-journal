"""Budget guard for paid API calls.

Rules:
- Before any paid call, call `check(estimated_cost)`. If it would push this month's
  spend over the cap, it raises BudgetExceeded and the call must not happen.
- After the call, call `record(...)` with the real cost.
- When spend reaches the warning level (80% by default) a WARNING is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import BudgetConfig
from app.core.logger import log_event
from app.core.models import CostEntry


class BudgetExceeded(RuntimeError):
    """Raised when a paid call would go over the monthly cap."""


@dataclass
class BudgetStatus:
    spent_gbp: float
    cap_gbp: float
    warn_at_fraction: float

    @property
    def fraction_used(self) -> float:
        return self.spent_gbp / self.cap_gbp if self.cap_gbp else 1.0

    @property
    def remaining_gbp(self) -> float:
        return max(self.cap_gbp - self.spent_gbp, 0.0)

    @property
    def warning(self) -> bool:
        return self.fraction_used >= self.warn_at_fraction

    @property
    def blocked(self) -> bool:
        return self.spent_gbp >= self.cap_gbp


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class CostTracker:
    def __init__(self, session: Session, budget: BudgetConfig, dry_run: bool = True):
        self.session = session
        self.budget = budget
        self.dry_run = dry_run

    def month_spend(self, now: datetime | None = None) -> float:
        total = self.session.scalar(
            select(func.coalesce(func.sum(CostEntry.cost_gbp), 0.0)).where(
                CostEntry.created_at >= _month_start(now)
            )
        )
        return float(total or 0.0)

    def status(self) -> BudgetStatus:
        return BudgetStatus(
            spent_gbp=self.month_spend(),
            cap_gbp=self.budget.monthly_cap_gbp,
            warn_at_fraction=self.budget.warn_at_fraction,
        )

    def check(self, estimated_cost_gbp: float, operation: str = "") -> None:
        """Raise BudgetExceeded if this call would break the monthly cap."""
        spent = self.month_spend()
        if spent + estimated_cost_gbp > self.budget.monthly_cap_gbp:
            msg = (
                f"Blocked '{operation}': would spend £{estimated_cost_gbp:.4f} on top of "
                f"£{spent:.2f} already used this month (cap £{self.budget.monthly_cap_gbp:.2f})."
            )
            log_event(self.session, module="cost_tracker", action="blocked", message=msg,
                      level="ERROR", dry_run=self.dry_run)
            raise BudgetExceeded(msg)

    def needs_approval(self, estimated_cost_gbp: float) -> bool:
        return estimated_cost_gbp > self.budget.approval_threshold_gbp

    def record(self, *, service: str, operation: str, cost_gbp: float, details: dict | None = None) -> CostEntry:
        before = self.status()
        entry = CostEntry(service=service, operation=operation, cost_gbp=cost_gbp, details=details or {})
        self.session.add(entry)
        self.session.commit()
        log_event(self.session, module="cost_tracker", action="spend",
                  message=f"{service} / {operation}: £{cost_gbp:.4f}", source=service,
                  cost_gbp=cost_gbp, dry_run=self.dry_run, details=details)

        after = self.status()
        if after.warning and not before.warning:
            from app.core.notify import notify

            notify(self.session, kind="budget", link="/logs",
                   title=f"Budget warning: {after.fraction_used:.0%} of this month's £{after.cap_gbp:.2f} cap used")
            log_event(
                self.session, module="cost_tracker", action="budget_warning", level="WARNING",
                message=(f"Budget warning: £{after.spent_gbp:.2f} of £{after.cap_gbp:.2f} used "
                         f"({after.fraction_used:.0%}) this month."),
                dry_run=self.dry_run,
            )
        return entry

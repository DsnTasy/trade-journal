"""The approval gate.

Nothing real (publishing, ads, orders, emails, spending) happens directly.
A module creates a *request*; you approve or reject it in the dashboard;
only then can `execute()` run it - and even then, in dry-run mode it only
logs what it WOULD have done.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logger import log_event
from app.core.models import ActionType, ApprovalRequest, ApprovalStatus

# An executor does the real work for one action type. It receives the request
# and returns a dict describing what happened (stored on the request).
Executor = Callable[[ApprovalRequest], dict]
_executors: dict[ActionType, Executor] = {}
# Optional: called after you approve/reject (e.g. to update a product's status).
_decision_hooks: dict[ActionType, Callable[[Session, ApprovalRequest], None]] = {}


class ApprovalError(RuntimeError):
    pass


def register_executor(action_type: ActionType, fn: Executor) -> None:
    """Modules (e.g. store_publisher) register the function that does the real action."""
    _executors[action_type] = fn


def register_decision_hook(action_type: ActionType, fn: Callable[[Session, ApprovalRequest], None]) -> None:
    _decision_hooks[action_type] = fn


def clear_executors() -> None:
    _executors.clear()
    _decision_hooks.clear()


def request_approval(
    session: Session,
    *,
    action_type: ActionType,
    title: str,
    description: str | None = None,
    payload: dict | None = None,
    estimated_cost_gbp: float = 0.0,
    product_id: int | None = None,
    dry_run: bool = True,
) -> ApprovalRequest:
    req = ApprovalRequest(
        action_type=action_type,
        title=title,
        description=description,
        payload=payload or {},
        estimated_cost_gbp=estimated_cost_gbp,
        product_id=product_id,
    )
    session.add(req)
    session.commit()
    log_event(session, module="approvals", action="requested",
              message=f"#{req.id} {action_type.value}: {title}", dry_run=dry_run)
    return req


def pending(session: Session) -> list[ApprovalRequest]:
    return list(session.scalars(
        select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)
        .order_by(ApprovalRequest.created_at)
    ))


def _get(session: Session, request_id: int) -> ApprovalRequest:
    req = session.get(ApprovalRequest, request_id)
    if req is None:
        raise ApprovalError(f"Approval request #{request_id} not found.")
    return req


def decide(session: Session, request_id: int, *, approve: bool, note: str | None = None,
           dry_run: bool = True) -> ApprovalRequest:
    """Approve or reject. Only a PENDING request can be decided."""
    req = _get(session, request_id)
    if req.status != ApprovalStatus.PENDING:
        raise ApprovalError(f"#{req.id} is already {req.status.value}; it can't be changed.")
    req.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    req.decided_at = datetime.now(UTC)
    req.decision_note = note
    session.commit()
    log_event(session, module="approvals", action=req.status.value,
              message=f"#{req.id} {req.title}" + (f" - {note}" if note else ""), dry_run=dry_run)
    hook = _decision_hooks.get(req.action_type)
    if hook is not None:
        hook(session, req)
    return req


def execute(session: Session, request_id: int, *, dry_run: bool = True) -> ApprovalRequest:
    """Run an APPROVED request. In dry-run mode, only log what would happen."""
    req = _get(session, request_id)
    if req.status != ApprovalStatus.APPROVED:
        raise ApprovalError(f"#{req.id} is {req.status.value}; only approved requests can run.")

    if dry_run:
        log_event(session, module="approvals", action="dry_run_execute",
                  message=f"Would run #{req.id} {req.action_type.value}: {req.title} (no real action taken)",
                  dry_run=True, details=req.payload)
        return req  # stays APPROVED so it can run for real once live mode is on

    executor = _executors.get(req.action_type)
    if executor is None:
        raise ApprovalError(f"No executor registered for {req.action_type.value} yet.")

    try:
        result = executor(req)
    except Exception as exc:  # record the failure, never hide it
        req.status = ApprovalStatus.FAILED
        req.result = {"error": str(exc)}
        session.commit()
        log_event(session, module="approvals", action="failed", level="ERROR",
                  message=f"#{req.id} failed: {exc}", dry_run=False)
        raise

    req.status = ApprovalStatus.EXECUTED
    req.executed_at = datetime.now(UTC)
    req.result = result
    session.commit()
    log_event(session, module="approvals", action="executed",
              message=f"#{req.id} {req.action_type.value}: {req.title}", dry_run=False, details=result)
    return req

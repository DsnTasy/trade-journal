import pytest

from app.core import approvals
from app.core.models import ActionType, ApprovalStatus


def _new(session):
    return approvals.request_approval(session, action_type=ActionType.PUBLISH_PRODUCT, title="Test")


def test_new_requests_are_pending(session):
    req = _new(session)
    assert req.status == ApprovalStatus.PENDING
    assert approvals.pending(session) == [req]


def test_cannot_execute_pending_or_rejected(session):
    req = _new(session)
    with pytest.raises(approvals.ApprovalError):
        approvals.execute(session, req.id, dry_run=False)
    approvals.decide(session, req.id, approve=False)
    with pytest.raises(approvals.ApprovalError):
        approvals.execute(session, req.id, dry_run=False)


def test_cannot_decide_twice(session):
    req = _new(session)
    approvals.decide(session, req.id, approve=False)
    with pytest.raises(approvals.ApprovalError):
        approvals.decide(session, req.id, approve=True)


def test_dry_run_never_calls_executor(session):
    calls = []
    approvals.register_executor(ActionType.PUBLISH_PRODUCT, lambda r: calls.append(r) or {})
    req = _new(session)
    approvals.decide(session, req.id, approve=True)
    approvals.execute(session, req.id, dry_run=True)
    assert calls == []
    assert req.status == ApprovalStatus.APPROVED  # still waiting to run for real


def test_live_runs_executor_exactly_once(session):
    calls = []
    approvals.register_executor(ActionType.PUBLISH_PRODUCT, lambda r: calls.append(r.id) or {"ok": True})
    req = _new(session)
    approvals.decide(session, req.id, approve=True)
    approvals.execute(session, req.id, dry_run=False)
    assert calls == [req.id]
    assert req.status == ApprovalStatus.EXECUTED and req.result == {"ok": True}
    with pytest.raises(approvals.ApprovalError):  # can't run twice
        approvals.execute(session, req.id, dry_run=False)


def test_executor_failure_is_recorded(session):
    def boom(_):
        raise ValueError("store unreachable")

    approvals.register_executor(ActionType.PUBLISH_PRODUCT, boom)
    req = _new(session)
    approvals.decide(session, req.id, approve=True)
    with pytest.raises(ValueError):
        approvals.execute(session, req.id, dry_run=False)
    assert req.status == ApprovalStatus.FAILED
    assert "store unreachable" in req.result["error"]


def test_missing_executor_is_an_error(session):
    req = _new(session)
    approvals.decide(session, req.id, approve=True)
    with pytest.raises(approvals.ApprovalError):
        approvals.execute(session, req.id, dry_run=False)

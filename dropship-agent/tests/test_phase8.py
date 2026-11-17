"""Scheduler, notifications, setup checks, policies, the `all` pipeline, and error handling."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.core import notify as notify_mod
from app.core.config import JobSchedule
from app.core.cost_tracker import CostTracker
from app.core.db import make_session_factory
from app.core.doctor import run_checks
from app.core.models import JobRun, Notification
from app.core.pipeline import run_all
from app.core.policies import fill
from app.core.scheduler import is_due, last_due_time, run_job, tick
from tests.test_research_service import research_settings  # noqa: F401  (fixture)

LONDON = ZoneInfo("Europe/London")


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=LONDON)


# ---------------- schedule maths ----------------
def test_daily_due_after_time_once():
    job = JobSchedule(every="daily", at="07:40")
    assert not is_due(job, at(2026, 11, 17, 7, 39), at(2026, 11, 16, 7, 41))
    assert is_due(job, at(2026, 11, 17, 7, 40), at(2026, 11, 16, 7, 41))
    assert not is_due(job, at(2026, 11, 17, 9, 0), at(2026, 11, 17, 7, 41))  # already ran today
    assert is_due(job, at(2026, 11, 17, 9, 0), None)  # never ran


def test_missed_runs_catch_up_once():
    job = JobSchedule(every="daily", at="07:40")
    # Computer was off for 3 days: one run, not three.
    assert is_due(job, at(2026, 11, 20, 10, 0), at(2026, 11, 17, 7, 41))
    assert not is_due(job, at(2026, 11, 20, 10, 5), at(2026, 11, 20, 10, 0))


def test_weekly_and_off():
    job = JobSchedule(every="weekly", at="08:00", weekday="mon")
    assert last_due_time(job, at(2026, 11, 18, 12, 0)) == at(2026, 11, 16, 8, 0)  # Wed → last Monday
    assert last_due_time(job, at(2026, 11, 16, 7, 0)) == at(2026, 11, 9, 8, 0)  # Monday before 8am
    assert not is_due(JobSchedule(every="off"), at(2026, 11, 16, 9, 0), None)


def test_uses_uk_time_across_clock_change():
    job = JobSchedule(every="daily", at="07:40")
    # 06:45 UTC on 1 July = 07:45 BST: due. 07:00 UTC on 1 Dec = 07:00 GMT: not yet.
    assert is_due(job, datetime(2026, 7, 1, 6, 45, tzinfo=UTC).astimezone(LONDON), at(2026, 6, 30, 8, 0))
    assert not is_due(job, datetime(2026, 12, 1, 7, 0, tzinfo=UTC).astimezone(LONDON), at(2026, 11, 30, 8, 0))


def test_bad_schedule_values_rejected():
    with pytest.raises(ValueError):
        JobSchedule(every="hourly")
    with pytest.raises(ValueError):
        JobSchedule(at="7:40pm")


# ---------------- jobs & notifications ----------------
def test_research_job_notifies_new_opportunities_once(session, research_settings):  # noqa: F811
    research_settings.schedule.notify_min_score = 60
    run = run_job("research", session, research_settings)
    assert run.status == "ok" and "researched" in run.summary
    first = session.scalars(select(Notification).where(Notification.kind == "opportunity")).all()
    assert [n.title for n in first] == ["New opportunity: Pet hair roller scored 84"]
    run_job("research", session, research_settings)  # same scores: no repeat notification
    assert len(session.scalars(select(Notification).where(Notification.kind == "opportunity")).all()) == 1


def test_failed_job_notifies_and_does_not_crash(session, research_settings, tmp_path):  # noqa: F811
    research_settings.research.candidates_file = str(tmp_path / "missing.csv")
    run = run_job("research", session, research_settings)
    assert run.status == "error"
    assert session.scalars(select(Notification).where(Notification.kind == "job_failed")).one()


def test_tick_runs_due_jobs_only_once(research_settings, engine):  # noqa: F811
    research_settings.schedule.research = JobSchedule(every="daily", at="07:40")
    factory = make_session_factory(engine)
    now = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    assert tick(factory, research_settings, now) == ["research"]
    assert tick(factory, research_settings, now) == []
    with factory() as s:
        assert len(s.scalars(select(JobRun)).all()) == 1


def test_budget_warning_creates_notification(session, settings):
    t = CostTracker(session, settings.budget)
    t.record(service="x", operation="y", cost_gbp=8.5)  # 85% of £10
    assert session.scalars(select(Notification).where(Notification.kind == "budget")).one()


def test_email_only_to_owner_and_failures_logged(session, settings, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            pass

        def login(self, user, pw):
            assert pw == "secret"

        def send_message(self, msg):
            sent.append(("to", msg["To"], msg["Subject"]))

    monkeypatch.setattr(notify_mod.smtplib, "SMTP", FakeSMTP)
    n = settings.notifications
    n.email_enabled, n.email_to, n.smtp_host, n.smtp_user = True, "me@example.com", "smtp.test", "me@example.com"
    settings.smtp_password = "secret"
    note = notify_mod.notify(session, kind="info", title="Hello", settings=settings)
    assert note.emailed and ("to", "me@example.com", "[Dropship Agent] Hello") in sent

    settings.smtp_password = None  # incomplete setup: saved in dashboard, warning logged, no crash
    note = notify_mod.notify(session, kind="info", title="Again", settings=settings)
    assert not note.emailed


# ---------------- doctor & policies ----------------
def test_doctor_reports_missing_items(settings):
    checks = {c.name: c for c in run_checks(settings)}
    assert checks["Python 3.11+"].ok
    assert not checks["Anthropic API key"].ok and "console.anthropic.com" in checks["Anthropic API key"].fix
    assert not checks["App credentials"].ok


def test_doctor_handles_files_outside_the_project(settings, tmp_path):
    f = tmp_path / "ideas.csv"
    f.write_text("name\n")
    settings.research.candidates_file = str(f)
    checks = {c.name: c for c in run_checks(settings)}
    assert checks["Product ideas list"].ok and str(f) in checks["Product ideas list"].detail


def test_policies_fill_and_flag_gaps():
    brand = {"store_name": "Pawsome", "promises": {"returns_days": 30},
             "business": {"legal_name": "Jo Bloggs", "contact_email": "hi@pawsome.co.uk"}}
    text = fill("{store_name} by {legal_name}{company_number_text} at {trading_address}. {returns_days} days. {contact_email}", brand)
    assert text == "Pawsome by Jo Bloggs at [CHECK: trading address]. 30 days. hi@pawsome.co.uk"
    assert fill("{store_name}", {"store_name": "Your Store Name"}) == "[CHECK: store name]"


def test_real_policy_templates_render():
    from app.core.policies import generate
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        results = generate({"store_name": "Pawsome"}, Path(d))
        names = {p.name for p, _ in results}
        assert names == {"privacy-policy.md", "returns-and-refunds.md", "delivery.md"}
        returns = (Path(d) / "returns-and-refunds.md").read_text()
        assert "14 days of receiving it" in returns and "{" not in returns


# ---------------- pipeline ----------------
def test_all_runs_safe_steps_and_never_sends(session, research_settings):  # noqa: F811
    steps = {s.name: s for s in run_all(session, research_settings)}
    assert steps["Research"].status == "done" and any("Pet hair roller" in x for x in steps["Research"].lines)
    assert steps["Content"].status == "skipped"  # paid: only with --with-content
    assert "Your to-do list" in steps


# ---------------- dashboard ----------------
def test_new_pages_and_error_handler(session, settings, engine):
    app = create_app(settings, engine, start_scheduler=False)

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    web = TestClient(app, raise_server_exceptions=False)
    assert "Setup checklist" in web.get("/setup").text
    assert "Anthropic API key" in web.get("/setup").text
    r = web.get("/boom")
    assert r.status_code == 500 and "kaboom" in r.json()["detail"] and "Logs page" in r.json()["detail"]
    assert "boom" in web.get("/logs").text

    notify_mod.notify(session, kind="opportunity", title="New opportunity: X", link="/products/1")
    home = web.get("/").text
    assert "New opportunity: X" in home and "Setup:" in home
    assert '<span class="badge">1</span>' in web.get("/notifications").text
    assert web.post("/notifications/read", headers={"HX-Request": "true"}).status_code == 204
    session.expire_all()
    assert session.scalars(select(Notification)).one().read_at is not None

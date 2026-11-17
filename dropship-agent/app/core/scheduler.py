"""A small built-in scheduler (no extra service to install).

It checks once a minute whether a job is due, using UK time. It runs while the dashboard is open
(when schedule.enabled is true), or on its own with `python run.py scheduler`.

Only FREE, read-only jobs are scheduled (product research). Nothing paid, published or sent runs
on a timer: those always need you.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import JobSchedule, Settings
from app.core.logger import log_event
from app.core.models import JobRun, Product
from app.core.notify import notify

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def last_due_time(job: JobSchedule, now: datetime) -> datetime | None:
    """The most recent moment the job should have run, at or before `now` (local time)."""
    if job.every == "off":
        return None
    hh, mm = (int(x) for x in job.at.split(":"))
    candidate = datetime.combine(now.date(), time(hh, mm), tzinfo=now.tzinfo)
    if job.every == "daily":
        return candidate if candidate <= now else candidate - timedelta(days=1)
    target = WEEKDAYS.index(job.weekday)
    days_back = (now.weekday() - target) % 7
    candidate -= timedelta(days=days_back)
    return candidate if candidate <= now else candidate - timedelta(days=7)


def is_due(job: JobSchedule, now: datetime, last_run: datetime | None) -> bool:
    """Due if a scheduled time has passed since the last run. Missed runs (computer off) catch up once."""
    due_at = last_due_time(job, now)
    if due_at is None:
        return False
    return last_run is None or last_run < due_at


# ------------------------------------------------------------------ jobs
def research_job(session: Session, settings: Settings) -> str:
    from app.modules.product_research import run_research

    before = {p.id: p.score for p in session.scalars(select(Product))}
    report = run_research(session, settings)
    threshold = settings.schedule.notify_min_score
    fresh = [r for r in report.rows if not r.excluded and r.score >= threshold
             and (before.get(r.product_id) is None or before[r.product_id] < threshold)]
    for r in fresh:
        notify(session, kind="opportunity", title=f"New opportunity: {r.name} scored {r.score:.0f}",
               body="; ".join(r.reasons_for[:2]) or None, link=f"/products/{r.product_id}", settings=settings)
    return f"{len(report.rows)} products researched, {len(fresh)} new at {threshold:.0f}+."


JOBS = {"research": research_job}


def run_job(name: str, session: Session, settings: Settings, now: datetime | None = None) -> JobRun:
    # Record the same "now" that decided the job was due, so it can never run twice for one slot.
    run = JobRun(job=name, started_at=(now or datetime.now(UTC)).astimezone(UTC))
    session.add(run)
    session.commit()
    try:
        run.summary = JOBS[name](session, settings)
        run.status = "ok"
    except Exception as exc:  # a failed job must never crash the dashboard
        session.rollback()
        run.status, run.summary = "error", str(exc)
        notify(session, kind="job_failed", title=f"Scheduled {name} failed", body=str(exc), link="/logs",
               settings=settings)
    run.finished_at = datetime.now(UTC)
    session.commit()
    log_event(session, module="scheduler", action=f"{name}_{run.status}", message=run.summary or "",
              level="INFO" if run.status == "ok" else "ERROR", dry_run=settings.dry_run)
    return run


def last_run_of(session: Session, name: str) -> datetime | None:
    run = session.scalars(select(JobRun).where(JobRun.job == name, JobRun.status != "running")
                          .order_by(JobRun.started_at.desc())).first()
    if run is None:
        return None
    started = run.started_at
    return started if started.tzinfo else started.replace(tzinfo=UTC)


def tick(session_factory: sessionmaker, settings: Settings, now_utc: datetime | None = None) -> list[str]:
    """Run whatever is due now. Returns the job names that ran."""
    tz = ZoneInfo(settings.schedule.timezone)
    now = (now_utc or datetime.now(UTC)).astimezone(tz)
    ran = []
    with session_factory() as session:
        for name in JOBS:
            job: JobSchedule = getattr(settings.schedule, name)
            last = last_run_of(session, name)
            if is_due(job, now, last.astimezone(tz) if last else None):
                run_job(name, session, settings, now=now)
                ran.append(name)
    return ran


class BackgroundScheduler:
    """Checks every minute in a background thread. Stops cleanly when the dashboard stops."""

    def __init__(self, session_factory: sessionmaker, settings: Settings, interval: float = 60):
        self.session_factory, self.settings, self.interval = session_factory, settings, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tick(self.session_factory, self.settings)
            except Exception:  # noqa: BLE001  (keep the loop alive; run_job already records failures)
                pass
            self._stop.wait(self.interval)

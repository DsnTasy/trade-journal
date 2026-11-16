"""Activity log: every action is written to the database (shown in the dashboard)
and to a text file (logs/app.log) as a backup."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.models import LogEntry

_file_logger = logging.getLogger("dropship")


def setup_file_logging(log_file: Path) -> None:
    """Call once at startup. Keeps up to 5 log files of 1 MB each."""
    if any(isinstance(h, RotatingFileHandler) for h in _file_logger.handlers):
        return
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _file_logger.addHandler(handler)
    _file_logger.setLevel(logging.INFO)


def log_event(
    session: Session,
    *,
    module: str,
    action: str,
    message: str,
    level: str = "INFO",
    source: str | None = None,
    cost_gbp: float | None = None,
    dry_run: bool = True,
    details: dict | None = None,
) -> LogEntry:
    entry = LogEntry(
        level=level.upper(),
        module=module,
        action=action,
        message=message,
        source=source,
        cost_gbp=cost_gbp,
        dry_run=dry_run,
        details=details or {},
    )
    session.add(entry)
    session.commit()

    tag = "[DRY RUN] " if dry_run else ""
    _file_logger.log(
        getattr(logging, level.upper(), logging.INFO),
        f"{tag}{module}.{action}: {message}" + (f" (source: {source})" if source else ""),
    )
    return entry

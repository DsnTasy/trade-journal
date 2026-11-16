"""Polite web fetching, used only for allowed, public pages.

Every request:
- checks the site's robots.txt first and refuses if our user agent is disallowed;
- identifies us honestly (user agent in config.yaml);
- waits between requests to the same site (rate limit);
- is cached on disk so we don't ask twice for the same page.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class FetchError(RuntimeError):
    pass


class DisallowedByRobots(FetchError):
    pass


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    content_type: str
    fetched_at: datetime
    from_cache: bool

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        return json.loads(self.text)


class PoliteFetcher:
    def __init__(self, user_agent: str, cache_dir: Path, delay_seconds: float = 3.0, cache_hours: float = 24,
                 timeout: float = 20, client: httpx.Client | None = None, sleep=time.sleep):
        self.user_agent = user_agent
        self.cache_dir = cache_dir
        self.delay = delay_seconds
        self.cache_ttl = timedelta(hours=cache_hours)
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True,
                                             headers={"User-Agent": user_agent})
        self.sleep = sleep
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request: dict[str, float] = {}
        self.log: list[dict] = []  # every URL we touched, for the report's "sources" list

    # ---------- internals ----------
    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _wait_turn(self, origin: str) -> None:
        last = self._last_request.get(origin)
        if last is not None and self.delay:
            remaining = self.delay - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[origin] = time.monotonic()

    def _cache_file(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"

    def _get(self, url: str) -> FetchResult:
        cache = self._cache_file(url)
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(data["fetched_at"])
            if datetime.now(UTC) - fetched < self.cache_ttl:
                return FetchResult(url, data["status"], data["text"], data["content_type"], fetched, True)
        self._wait_turn(self._origin(url))
        try:
            resp = self.client.get(url, headers={"User-Agent": self.user_agent})
        except httpx.HTTPError as exc:
            raise FetchError(f"Couldn't reach {url}: {exc}") from exc
        result = FetchResult(url, resp.status_code, resp.text, resp.headers.get("content-type", ""),
                             datetime.now(UTC), False)
        if resp.status_code < 500:  # don't cache server errors
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"status": result.status, "text": result.text,
                                         "content_type": result.content_type,
                                         "fetched_at": result.fetched_at.isoformat()}), encoding="utf-8")
        return result

    def robots(self, url: str) -> urllib.robotparser.RobotFileParser:
        origin = self._origin(url)
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            r = self._get(origin + "/robots.txt")
            self.log.append({"url": r.url, "status": r.status, "at": r.fetched_at.isoformat(), "cached": r.from_cache})
            if r.status in (401, 403):
                rp.disallow_all = True  # standard rule: access-controlled robots.txt = stay out
            elif r.status >= 400:
                rp.allow_all = True  # standard rule: no robots.txt = no restrictions
            else:
                rp.parse(r.text.splitlines())
            self._robots[origin] = rp
        return self._robots[origin]

    # ---------- public ----------
    def allowed(self, url: str) -> bool:
        return self.robots(url).can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> FetchResult:
        if not self.allowed(url):
            raise DisallowedByRobots(f"robots.txt asks robots not to visit {urlsplit(url).path or '/'}")
        r = self._get(url)
        self.log.append({"url": r.url, "status": r.status, "at": r.fetched_at.isoformat(), "cached": r.from_cache})
        return r

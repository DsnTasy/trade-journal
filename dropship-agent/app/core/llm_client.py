"""The ONE place the app talks to Claude (Anthropic API).

Every call goes through `LLMClient.complete()`, which:
1. Returns a cached answer if we've asked the exact same thing before (free).
2. Estimates the worst-case cost and checks the monthly budget BEFORE calling.
3. Refuses calls estimated above the approval threshold (they need dashboard approval).
4. Records the real cost from the API's token counts afterwards.

It never invents output: if there is no API key or the call fails, it raises an error.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import ModelPrice, Settings
from app.core.cost_tracker import CostTracker
from app.core.logger import log_event


class LLMError(RuntimeError):
    pass


class LLMNotConfigured(LLMError):
    """No API key set in .env."""


class LLMRefused(LLMError):
    """The model declined the request."""


class LLMNeedsApproval(LLMError):
    """The estimated cost is above the per-call approval threshold."""


@dataclass
class LLMResult:
    text: str
    model: str
    cost_gbp: float
    cached: bool
    input_tokens: int = 0
    output_tokens: int = 0


# Rough rule of thumb: ~3 characters per token (deliberately pessimistic so estimates run high).
_CHARS_PER_TOKEN = 3


class LLMClient:
    def __init__(self, settings: Settings, session: Session, client: Any | None = None):
        self.settings = settings
        self.session = session
        self.tracker = CostTracker(session, settings.budget, dry_run=settings.dry_run)
        self._client = client  # tests pass a fake; real client is created lazily
        self.cache_dir = settings.path(settings.llm_cache_dir)

    # ---------- helpers ----------
    def _price(self, model: str) -> ModelPrice:
        price = self.settings.llm.prices.get(model)
        if price is None:
            raise LLMError(f"No price for model '{model}' in config.yaml (llm.prices). Add it before use.")
        return price

    def estimate_cost_gbp(self, model: str, system: str, prompt: str, max_tokens: int) -> float:
        """Worst case: estimated input + the full max_tokens of output."""
        p = self._price(model)
        in_tokens = (len(system) + len(prompt)) / _CHARS_PER_TOKEN
        usd = in_tokens * p.input_usd_per_mtok / 1e6 + max_tokens * p.output_usd_per_mtok / 1e6
        return usd * self.settings.llm.usd_to_gbp

    def actual_cost_gbp(self, model: str, usage: Any) -> float:
        p = self._price(model)
        usd = (
            (getattr(usage, "input_tokens", 0) or 0) * p.input_usd_per_mtok
            + (getattr(usage, "output_tokens", 0) or 0) * p.output_usd_per_mtok
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * p.cache_read_usd_per_mtok
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * p.cache_write_usd_per_mtok
        ) / 1e6
        return usd * self.settings.llm.usd_to_gbp

    def _cache_path(self, key_parts: dict) -> Path:
        digest = hashlib.sha256(json.dumps(key_parts, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.settings.anthropic_api_key:
                raise LLMNotConfigured(
                    "ANTHROPIC_API_KEY is not set. Add it to your .env file (see README: 'API keys')."
                )
            import anthropic  # imported here so the rest of the app works without a key

            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    # ---------- main entry point ----------
    def complete_json(self, *, task: str, system: str, prompt: str, schema: dict, max_tokens: int | None = None,
                      approved: bool = False) -> tuple[dict, LLMResult]:
        """Like complete(), but the answer is guaranteed to be JSON matching `schema`."""
        def parse(text: str) -> dict:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LLMError(f"'{task}' didn't return valid JSON (it may have been cut off).") from exc
            missing = [k for k in schema.get("required", []) if k not in data]
            if missing:
                raise LLMError(f"'{task}' answer is missing: {', '.join(missing)}.")
            return data

        result = self.complete(task=task, system=system, prompt=prompt, max_tokens=max_tokens,
                               approved=approved, json_schema=schema, validate=parse)
        return parse(result.text), result

    def complete(self, *, task: str, system: str, prompt: str, max_tokens: int | None = None,
                 approved: bool = False, json_schema: dict | None = None, validate=None) -> LLMResult:
        """`validate(text)` may raise to reject an answer; rejected answers are never cached."""
        model = self.settings.llm.model_for(task)
        max_tokens = max_tokens or self.settings.llm.max_tokens
        key = {"task": task, "model": model, "system": system, "prompt": prompt,
               "max_tokens": max_tokens, "effort": self.settings.llm.effort, "schema": json_schema}

        # 1. Cache
        cache_file = self._cache_path(key)
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            log_event(self.session, module="llm", action="cache_hit", message=f"{task} ({model})",
                      source="anthropic (cached)", cost_gbp=0.0, dry_run=self.settings.dry_run)
            return LLMResult(text=data["text"], model=data["model"], cost_gbp=0.0, cached=True)

        # 2. Budget + approval checks
        estimate = self.estimate_cost_gbp(model, system, prompt, max_tokens)
        self.tracker.check(estimate, operation=f"llm:{task}")
        if self.tracker.needs_approval(estimate) and not approved:
            raise LLMNeedsApproval(
                f"'{task}' may cost up to £{estimate:.2f}, above your £"
                f"{self.settings.budget.approval_threshold_gbp:.2f} per-call threshold. Approve it first."
            )

        # 3. Call the API
        client = self._get_client()
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.settings.llm.effort,
                           **({"format": {"type": "json_schema", "schema": json_schema}} if json_schema else {})},
            # If the model declines, the API retries on a suitable fallback model automatically.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

        served_model = getattr(response, "model", model) or model
        price_model = served_model if served_model in self.settings.llm.prices else model
        cost = self.actual_cost_gbp(price_model, response.usage)
        self.tracker.record(service="anthropic", operation=f"llm:{task}", cost_gbp=cost,
                            details={"model": served_model,
                                     "input_tokens": response.usage.input_tokens,
                                     "output_tokens": response.usage.output_tokens})

        if response.stop_reason == "refusal":
            raise LLMRefused(f"The model declined the '{task}' request.")

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        if not text.strip():
            raise LLMError(f"Empty response for '{task}' (stop_reason={response.stop_reason}).")
        if response.stop_reason == "max_tokens":
            log_event(self.session, module="llm", action="truncated", level="WARNING",
                      message=f"'{task}' hit max_tokens ({max_tokens}); output may be cut off.",
                      dry_run=self.settings.dry_run)
            if json_schema:  # cut-off JSON is useless: don't cache it
                raise LLMError(f"'{task}' was cut off before finishing. Try again or raise max_tokens.")

        if validate is not None:
            validate(text)  # raises on a bad answer, before it reaches the cache

        # 4. Cache for next time
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"text": text, "model": served_model}), encoding="utf-8")

        return LLMResult(text=text, model=served_model, cost_gbp=cost, cached=False,
                         input_tokens=response.usage.input_tokens,
                         output_tokens=response.usage.output_tokens)

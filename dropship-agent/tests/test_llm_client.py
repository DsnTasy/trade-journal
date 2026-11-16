"""LLM wrapper tests use a fake client, so they cost nothing."""

from types import SimpleNamespace

import pytest

from app.core.cost_tracker import BudgetExceeded, CostTracker
from app.core.llm_client import LLMClient, LLMNeedsApproval, LLMNotConfigured, LLMRefused


class FakeAnthropic:
    def __init__(self, text="Hello", stop_reason="end_turn", input_tokens=1000, output_tokens=500):
        self.calls = []
        self._resp = SimpleNamespace(
            model="test-model",
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def test_records_real_cost(session, settings):
    fake = FakeAnthropic()
    result = LLMClient(settings, session, client=fake).complete(task="t", system="s", prompt="p", max_tokens=1000)
    # 1000 in * $1/M + 500 out * $10/M = $0.006, at 1:1 GBP
    assert result.cost_gbp == pytest.approx(0.006)
    assert result.text == "Hello" and not result.cached
    assert CostTracker(session, settings.budget).month_spend() == pytest.approx(0.006)


def test_second_identical_call_is_cached_and_free(session, settings):
    fake = FakeAnthropic()
    client = LLMClient(settings, session, client=fake)
    client.complete(task="t", system="s", prompt="p", max_tokens=1000)
    again = client.complete(task="t", system="s", prompt="p", max_tokens=1000)
    assert again.cached and again.cost_gbp == 0
    assert len(fake.calls) == 1


def test_blocks_when_budget_would_be_exceeded(session, settings):
    CostTracker(session, settings.budget).record(service="x", operation="y", cost_gbp=9.99)
    fake = FakeAnthropic()
    with pytest.raises(BudgetExceeded):
        LLMClient(settings, session, client=fake).complete(task="t", system="s", prompt="p", max_tokens=4000)
    assert fake.calls == []  # the API was never called


def test_expensive_call_needs_approval(session, settings):
    fake = FakeAnthropic()
    client = LLMClient(settings, session, client=fake)
    # 200k output tokens * $10/M = $2 worst case > £1 threshold
    with pytest.raises(LLMNeedsApproval):
        client.complete(task="t", system="s", prompt="p", max_tokens=200_000)
    assert fake.calls == []
    client.complete(task="t", system="s", prompt="p", max_tokens=200_000, approved=True)
    assert len(fake.calls) == 1


def test_refusal_raises_and_is_not_cached(session, settings):
    fake = FakeAnthropic(stop_reason="refusal", text="")
    client = LLMClient(settings, session, client=fake)
    with pytest.raises(LLMRefused):
        client.complete(task="t", system="s", prompt="p", max_tokens=100)
    with pytest.raises(LLMRefused):
        client.complete(task="t", system="s", prompt="p", max_tokens=100)
    assert len(fake.calls) == 2


def test_no_api_key_gives_clear_error(session, settings):
    settings.anthropic_api_key = None
    with pytest.raises(LLMNotConfigured):
        LLMClient(settings, session).complete(task="t", system="s", prompt="p", max_tokens=100)


def test_uses_task_model_override(session, settings):
    settings.llm.task_models = {"scoring": "test-model"}
    fake = FakeAnthropic()
    LLMClient(settings, session, client=fake).complete(task="scoring", system="s", prompt="p", max_tokens=10)
    assert fake.calls[0]["model"] == "test-model"


def test_complete_json_sends_schema_and_parses(session, settings):
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"],
              "additionalProperties": False}
    fake = FakeAnthropic(text='{"title": "Pet roller"}')
    data, result = LLMClient(settings, session, client=fake).complete_json(
        task="t", system="s", prompt="p", schema=schema, max_tokens=100)
    assert data == {"title": "Pet roller"}
    assert fake.calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": schema}


def test_complete_json_missing_field_or_cut_off(session, settings):
    from app.core.llm_client import LLMError
    schema = {"type": "object", "required": ["title"]}
    with pytest.raises(LLMError, match="missing"):
        LLMClient(settings, session, client=FakeAnthropic(text="{}")).complete_json(
            task="a", system="s", prompt="p", schema=schema, max_tokens=10)
    cut = FakeAnthropic(text='{"title": "Pet', stop_reason="max_tokens")
    with pytest.raises(LLMError, match="cut off"):
        LLMClient(settings, session, client=cut).complete_json(task="b", system="s", prompt="p", schema=schema,
                                                                max_tokens=10)

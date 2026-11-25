"""M1-09: Jev difficulty routing, cascade, provider fallbacks, USD 10 cap, retry budgets (FR-40, NFR-17)."""

import json

import httpx
import httpx2
import pytest

from core.orchestrator.decisions import Answer, DecisionEngine, JevUnavailable, Masked
from cost.router import Router, SpendCap, difficulty, pick_tier
from reliability.fallbacks import (
    AllProvidersFailed,
    Completion,
    Provider,
    ProviderError,
    RetryBudget,
    call_chain,
    claude,
    openai_compatible,
)

STATE = {"message": Masked("My VPN drops every hour since the update")}
MESSAGES = [{"role": "system", "content": "You are support."}, {"role": "user", "content": "VPN drops"}]


class FakeJev:
    def __init__(self, score=None, down=False):
        self.score, self.down = score, down

    def evaluate(self, state, questions):
        if self.down:
            raise JevUnavailable("timeout")
        return {"difficulty": Answer(self.score, 0.9, round(self.score))}


def engine(score=None, down=False):
    return DecisionEngine(FakeJev(score, down))


class Fake:
    """A provider that fails with the given errors in turn, then answers; counts its calls."""

    def __init__(self, name, *failures, paid=False, tokens=(1_000, 500)):
        self.name, self.failures, self.calls, self.tokens = name, list(failures), 0, tokens
        prices = (4.0, 20.0) if paid else (0.0, 0.0)
        self.provider = Provider(name, self.complete, *prices)

    def complete(self, messages, max_tokens):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return Completion(f"answer from {self.name}", self.name, *self.tokens, f"{self.name}-model")


def transient(name):
    return ProviderError(name, "HTTP 503", transient=True)


def setup(small=(), mid=(), frontier=(), cap=None):
    fakes = {"small": Fake("small", *small), "mid": Fake("mid", *mid),
             "claude": Fake("claude", *frontier, paid=True)}
    router = Router([[fakes["small"].provider], [fakes["mid"].provider], [fakes["claude"].provider]],
                    cap=cap, sleep=lambda s: None)
    return router, fakes


@pytest.mark.parametrize("score, tier",
                         [(0.0, 0), (0.34, 0), (0.35, 1), (0.74, 1), (0.75, 2), (1.0, 2), (None, 1)])
def test_difficulty_picks_the_tier(score, tier):
    assert pick_tier(score) == tier


def test_jev_score_is_normalised_and_jev_down_means_mid():
    assert difficulty(engine(3.2), STATE) == pytest.approx(0.8)
    assert difficulty(engine(down=True), STATE) is None


@pytest.mark.parametrize("score, provider", [(0.2, "small"), (2.0, "mid"), (3.6, "claude")])
def test_turns_route_by_difficulty(score, provider):
    router, _ = setup()
    reply = router.answer(MESSAGES, engine(score), STATE, verify=lambda text: True)
    assert (reply.outcome, reply.provider, reply.cascaded) == ("answered", provider, False)
    assert reply.model == f"{provider}-model"  # shown in the customer's trace


def test_a_failed_check_cascades_once_one_tier_up():
    router, fakes = setup()
    checks = iter([False, True])
    reply = router.answer(MESSAGES, engine(0.2), STATE, verify=lambda text: next(checks))
    assert (reply.outcome, reply.provider, reply.cascaded) == ("answered", "mid", True)
    router, fakes = setup()
    failed = router.answer(MESSAGES, engine(0.2), STATE, verify=lambda text: False)
    calls = (fakes["small"].calls, fakes["mid"].calls, fakes["claude"].calls)
    assert failed.outcome == "escalate" and calls == (1, 1, 0)


def test_the_top_tier_has_nowhere_to_cascade():
    router, fakes = setup()
    reply = router.answer(MESSAGES, engine(4.0), STATE, verify=lambda text: False)
    assert reply.outcome == "escalate" and fakes["claude"].calls == 1


def test_when_claude_fails_the_best_free_model_answers():
    router, fakes = setup(frontier=[transient("claude"), transient("claude")])
    reply = router.answer(MESSAGES, engine(4.0), STATE, verify=lambda text: True)
    assert (reply.provider, fakes["claude"].calls, fakes["small"].calls) == ("mid", 2, 0)


def test_claude_spend_limit_is_not_retried():
    router, fakes = setup(frontier=[ProviderError("claude", "credit balance too low", spend_limit=True)])
    reply = router.answer(MESSAGES, engine(4.0), STATE, verify=lambda text: True)
    assert (reply.provider, fakes["claude"].calls) == ("mid", 1)


def test_the_cap_skips_claude_and_spend_is_recorded():
    cap = SpendCap(cap_usd=10.0)
    router, fakes = setup(cap=cap)
    router.answer(MESSAGES, engine(4.0), STATE, verify=lambda text: True)
    assert cap.spent_usd == pytest.approx((1_000 * 4 + 500 * 20) / 1e6)
    full = SpendCap(cap_usd=10.0, spent_usd=9.99)
    router, fakes = setup(cap=full)
    reply = router.answer(MESSAGES, engine(4.0), STATE, verify=lambda text: True)
    assert (reply.provider, fakes["claude"].calls) == ("mid", 0)


def test_free_tiers_never_fall_back_to_claude():
    router, fakes = setup(small=[transient("small"), transient("small")],
                          mid=[transient("mid"), transient("mid")])
    reply = router.answer(MESSAGES, engine(0.1), STATE, verify=lambda text: True)
    assert reply.outcome == "degraded" and fakes["claude"].calls == 0


def test_a_mid_tier_turn_no_free_provider_answers_goes_one_tier_up_to_claude(caplog):
    """SWEEP-05 (register B23): a tier-one turn where every free provider fails cascades to Claude, and each
    provider failure is logged by its code."""
    router, fakes = setup(small=[transient("small")] * 2, mid=[transient("mid")] * 2)
    with caplog.at_level("WARNING", logger="cost.router"):
        reply = router.answer(MESSAGES, engine(2.0), STATE, verify=lambda text: True)
    assert (reply.outcome, reply.provider, reply.cascaded) == ("answered", "claude", True)
    assert "tier 1: no provider answered" in caplog.text and "mid: HTTP 503" in caplog.text


def test_when_claude_cannot_answer_either_the_turn_reads_as_degraded():
    router, fakes = setup(small=[transient("small")] * 4, mid=[transient("mid")] * 4,
                          frontier=[transient("claude")] * 2)
    reply = router.answer(MESSAGES, engine(2.0), STATE, verify=lambda text: True)
    assert reply.outcome == "degraded" and fakes["claude"].calls >= 1


def test_a_refusal_goes_to_a_human_and_is_not_asked_elsewhere():
    router, fakes = setup(frontier=[ProviderError("claude", "refused", refused=True)])
    reply = router.answer(MESSAGES, engine(4.0), STATE, verify=lambda text: True)
    assert (reply.outcome, fakes["mid"].calls, fakes["small"].calls) == ("escalate", 0, 0)


def test_retries_are_capped_per_turn():
    fakes = [Fake(f"p{i}", transient(f"p{i}"), transient(f"p{i}")) for i in range(3)]
    turn = {"retries": 0}
    with pytest.raises(AllProvidersFailed):
        call_chain([f.provider for f in fakes], MESSAGES, 100, RetryBudget(), turn, sleep=lambda s: None)
    assert turn["retries"] == 3 and [f.calls for f in fakes] == [2, 2, 2]
    four = [Fake(f"q{i}", transient(f"q{i}"), transient(f"q{i}")) for i in range(4)]
    turn = {"retries": 0}
    with pytest.raises(AllProvidersFailed):
        call_chain([f.provider for f in four], MESSAGES, 100, RetryBudget(), turn, sleep=lambda s: None)
    assert [f.calls for f in four] == [2, 2, 2, 1]  # the fourth provider gets no retry


def test_a_dependency_over_its_retry_budget_gets_no_retry():
    budget = RetryBudget(min_calls=10)
    for retry in [True] * 2 + [False] * 8:
        budget.record("p", retry)
    flaky = Fake("p", transient("p"))
    with pytest.raises(AllProvidersFailed):
        call_chain([flaky.provider], MESSAGES, 100, budget, {"retries": 0}, sleep=lambda s: None)
    assert flaky.calls == 1


def test_openai_compatible_adapter():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 2}})

    groq = openai_compatible("groq", "https://api.groq.test/openai/v1", "llama", "k",
                             transport=httpx.MockTransport(handler))
    assert groq.complete(MESSAGES, 64) == Completion("hi", "groq", 7, 2, "llama")
    assert seen[0] == {"model": "llama", "messages": MESSAGES, "max_tokens": 64}
    for status, is_transient in ((429, True), (503, True), (400, False)):
        failing = openai_compatible("g", "https://x.test/v1", "m",
                                    transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s)))
        with pytest.raises(ProviderError) as raised:
            failing.complete(MESSAGES, 64)
        assert raised.value.transient is is_transient


def claude_with(handler):
    return claude(api_key="k", base_url="https://api.anthropic.test",
                  http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
           "content": [{"type": "text", "text": "Renew the certificate."}], "stop_reason": "end_turn",
           "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_claude_adapter_request_and_reply():
    seen = []

    def handler(request):
        seen.append((request.headers.get("anthropic-beta"), json.loads(request.content)))
        return httpx2.Response(200, json=MESSAGE)

    expected = Completion("Renew the certificate.", "claude", 10, 5, "claude-opus-5-5")
    assert claude_with(handler).complete(MESSAGES, 512) == expected
    beta, body = seen[0]
    assert "server-side-fallback-2026-07-01" in beta
    assert (body["model"], body["max_tokens"], body["system"], body["fallbacks"]) == (
        "claude-opus-5-5", 512, "You are support.", "default")
    assert body["messages"] == [{"role": "user", "content": "VPN drops"}]
    assert body["output_config"] == {"effort": "high"}


def test_claude_haiku_is_sent_without_server_side_fallbacks():
    """SWEEP-05: Claude Haiku 5.5 takes no server-side fallbacks, so the request leaves them out."""
    seen = []

    def handler(request):
        seen.append((request.headers.get("anthropic-beta") or "", json.loads(request.content)))
        return httpx2.Response(200, json={**MESSAGE, "model": "claude-haiku-5-5"})

    haiku = claude(api_key="k", model="claude-haiku-5-5", base_url="https://api.anthropic.test",
                   http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    assert haiku.complete(MESSAGES, 512).text == "Renew the certificate."
    beta, body = seen[0]
    assert "server-side-fallback" not in beta and "fallbacks" not in body
    assert (body["model"], body["output_config"]) == ("claude-haiku-5-5", {"effort": "high"})


def test_claude_adapter_makes_one_request_and_classifies_failures():
    calls = []

    def overloaded(request):
        calls.append(1)
        error = {"type": "overloaded_error", "message": "busy"}
        return httpx2.Response(529, json={"type": "error", "error": error})

    with pytest.raises(ProviderError) as raised:
        claude_with(overloaded).complete(MESSAGES, 64)
    assert raised.value.transient and len(calls) == 1  # SDK retries are off

    def broke(request):
        return httpx2.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "Your credit balance is too low to access the API."}})

    with pytest.raises(ProviderError) as raised:
        claude_with(broke).complete(MESSAGES, 64)
    assert raised.value.spend_limit and not raised.value.transient

    with pytest.raises(ProviderError) as raised:
        claude_with(lambda r: httpx2.Response(200, json={**MESSAGE, "stop_reason": "refusal"})).complete(
            MESSAGES, 64)
    assert raised.value.refused

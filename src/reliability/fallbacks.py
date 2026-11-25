"""Model providers, the fallback chain and retry budgets (NFR-17 / RP-ADP-02: RP-D4, RP-D13).

One retry layer: SDK retries are switched off and a transient failure (429, 5xx, timeout) gets at most
one retry here, only while the turn has retries left (3 per turn) and the provider's recent calls are not
already more than 10 % retries. Then the chain moves on to the next provider; when every provider has failed
the caller degrades (knowledge-base answer). A refusal never moves on: another model must not answer what one
declined.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

import anthropic
import httpx

TURN_RETRY_LIMIT = 3
MAX_RETRY_RATIO = 0.10
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_USD_PER_MTOK = (4.0, 20.0)  # input, output


class ProviderError(RuntimeError):
    def __init__(self, provider, message, transient=False, spend_limit=False, refused=False):
        super().__init__(f"{provider}: {message}")
        self.provider, self.transient = provider, transient
        self.spend_limit, self.refused = spend_limit, refused


class AllProvidersFailed(RuntimeError):
    def __init__(self, errors):
        super().__init__("; ".join(map(str, errors)) or "no provider available")
        self.errors = errors


@dataclass
class Completion:
    text: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None      # the model that wrote it, shown in the customer's trace


@dataclass(frozen=True)
class Provider:
    """complete(messages, max_tokens) -> Completion; messages: [{"role", "content"}], system role allowed."""

    name: str
    complete: Callable
    usd_per_mtok_in: float = 0.0
    usd_per_mtok_out: float = 0.0

    @property
    def paid(self):
        return bool(self.usd_per_mtok_in or self.usd_per_mtok_out)

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.usd_per_mtok_in + output_tokens * self.usd_per_mtok_out) / 1_000_000


class RetryBudget:
    """Per-dependency share of retries over a sliding window (ponytail: in-process; shared store at scale)."""

    def __init__(self, max_ratio=MAX_RETRY_RATIO, window_seconds=300, min_calls=10, clock=time.monotonic):
        self.max_ratio, self.window, self.min_calls, self.clock = max_ratio, window_seconds, min_calls, clock
        self.calls = defaultdict(deque)  # dependency -> deque of (time, was_retry)

    def _recent(self, dependency):
        calls, cutoff = self.calls[dependency], self.clock() - self.window
        while calls and calls[0][0] < cutoff:
            calls.popleft()
        return calls

    def record(self, dependency, retry):
        self.calls[dependency].append((self.clock(), retry))

    def allows_retry(self, dependency):
        calls = self._recent(dependency)
        if len(calls) < self.min_calls:
            return True
        return sum(retry for _, retry in calls) / len(calls) < self.max_ratio


def call_chain(providers, messages, max_tokens, budget, turn, sleep=time.sleep, backoff=0.5):
    """First provider that answers; raises AllProvidersFailed, or ProviderError for a refusal."""
    errors = []
    for provider in providers:
        for attempt in (1, 2):
            budget.record(provider.name, retry=attempt == 2)
            try:
                return provider.complete(messages, max_tokens)
            except ProviderError as error:
                if error.refused:
                    raise
                errors.append(error)
                if not (error.transient and attempt == 1 and turn["retries"] < TURN_RETRY_LIMIT
                        and budget.allows_retry(provider.name)):
                    break
                turn["retries"] += 1
                sleep(backoff)
    raise AllProvidersFailed(errors)


def openai_compatible(name, base_url, model, api_key=None, timeout=30.0, transport=None):
    """Free tiers that speak the OpenAI chat API: Groq, Gemini's OpenAI endpoint, Ollama (/v1)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout, transport=transport)

    def complete(messages, max_tokens):
        try:
            reply = http.post("/chat/completions", json={"model": model, "messages": messages,
                                                         "max_tokens": max_tokens})
        except httpx.HTTPError as error:
            raise ProviderError(name, f"unreachable: {error}", transient=True) from error
        if reply.status_code == 429 or reply.status_code >= 500:
            raise ProviderError(name, f"HTTP {reply.status_code}", transient=True)
        if reply.status_code != 200:
            raise ProviderError(name, f"HTTP {reply.status_code}: {reply.text[:200]}")
        try:
            data = reply.json()
            usage = data.get("usage") or {}
            return Completion(data["choices"][0]["message"]["content"], name,
                              usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), model)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(name, f"malformed reply: {error}", transient=True) from error

    return Provider(name, complete)


# Models that take server-side refusal fallbacks (fallbacks: "default"); Claude Haiku 5.5 does not, and a
# request naming it with the parameter may be rejected, so it is sent without one.
SERVER_FALLBACKS = ("claude-fable-5", "claude-fable-5-1", "claude-fable-5-2", "claude-mythos-5",
                    "claude-mythos-5-1", "claude-mythos-5-2", "claude-opus-5", "claude-opus-5-5",
                    "claude-sonnet-5-5")


def claude(api_key=None, model=CLAUDE_MODEL, effort="high", http_client=None, base_url=None,
           usd_per_mtok=CLAUDE_USD_PER_MTOK):
    """The frontier tier. SDK retries are off (this module is the retry layer); server-side refusal fallbacks
    are on for the models that take them (SERVER_FALLBACKS), and a refusal that survives them is reported,
    never re-asked elsewhere."""
    fallback = ({"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
                if model in SERVER_FALLBACKS else {})
    client = anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=http_client, base_url=base_url)

    def complete(messages, max_tokens):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        try:
            response = client.beta.messages.create(
                model=model, max_tokens=max_tokens, messages=turns, output_config={"effort": effort},
                **fallback, **({"system": system} if system else {}))
        except anthropic.RateLimitError as error:
            raise ProviderError("claude", "rate limited", transient=True) from error
        except anthropic.BadRequestError as error:
            # ponytail: spend-limit wording matched loosely; confirm against a real capped key
            text = str(error).lower()
            spent = any(phrase in text for phrase in ("credit balance", "spend limit", "usage limit"))
            raise ProviderError("claude", f"bad request: {error}", spend_limit=spent) from error
        except anthropic.APIStatusError as error:
            transient = error.status_code >= 500
            raise ProviderError("claude", f"HTTP {error.status_code}", transient=transient) from error
        except anthropic.APIConnectionError as error:  # includes timeouts
            raise ProviderError("claude", f"unreachable: {error}", transient=True) from error
        if response.stop_reason == "refusal":
            raise ProviderError("claude", "refused", refused=True)
        text = "".join(block.text for block in response.content if block.type == "text")
        # a server-side fallback may have answered with another model: report the one that did
        return Completion(text, "claude", response.usage.input_tokens, response.usage.output_tokens,
                          getattr(response, "model", None) or model)

    return Provider("claude", complete, *usd_per_mtok)

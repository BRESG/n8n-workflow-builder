"""Call a model from production code without it becoming a liability.

A model call is an unreliable network call that also costs money and can
answer with something you did not ask for. This module treats all three as
normal conditions rather than surprises:

  constrained output   the model may only answer with a label from a fixed
                       list, and anything else is rejected before it is used
  low confidence       an unsure answer is routed to a person, not guessed
  retries              transient failures are retried with backoff
  timeouts             a call that hangs is cut off
  cost per call        every call is priced and accumulated, so spend is
                       visible before the invoice is
  exception queue      a request that fails every attempt is captured, not
                       dropped in silence

No API key is needed to run this. The provider is an interface, and the stub
in providers.py answers deterministically so the tests are repeatable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


class AIError(Exception):
    """Base for everything this module raises."""


class ProviderTimeout(AIError):
    """The provider did not answer inside the timeout."""


class ProviderUnavailable(AIError):
    """The provider failed in a way worth retrying."""


class UnusableAnswer(AIError):
    """The provider answered, but not with something we can use."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Pricing:
    """Dollars per million tokens, the unit every provider publishes."""

    input_per_million: float
    output_per_million: float

    def cost(self, usage: Usage) -> float:
        return round(
            usage.input_tokens / 1_000_000 * self.input_per_million
            + usage.output_tokens / 1_000_000 * self.output_per_million,
            8,
        )


@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float
    usage: Usage
    cost_usd: float
    attempts: int
    needs_human: bool = False


class Provider(Protocol):
    """The only thing this module needs from a model vendor."""

    def complete(self, prompt: str, *, timeout: float) -> tuple[str, Usage]:
        ...


@dataclass
class Ledger:
    """Running cost, so spend is answerable at any moment."""

    calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def record(self, usage: Usage, cost: float, *, failed: bool = False) -> None:
        self.calls += 1
        if failed:
            self.failed_calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cost_usd = round(self.cost_usd + cost, 8)

    def cost_per_call(self) -> float:
        if not self.calls:
            return 0.0
        return round(self.cost_usd / self.calls, 8)


@dataclass
class ExceptionQueue:
    """Work that failed every attempt.

    A dropped ticket is worse than a slow one, because nobody finds out. Any
    request that exhausts its retries lands here with the reason attached so
    it can be replayed or looked at by a person.
    """

    items: list[dict] = field(default_factory=list)

    def add(self, payload: dict, reason: str) -> None:
        self.items.append({"payload": payload, "reason": reason})

    def __len__(self) -> int:
        return len(self.items)


PROMPT = """You are labelling a customer support message.

Reply with JSON only, in this exact shape:
{{"label": "<one of the allowed labels>", "confidence": <0.0 to 1.0>}}

Allowed labels, and nothing else is acceptable:
{labels}

If none of them fit, use the label "unknown" with a low confidence.

The message:
{text}
"""


class Classifier:
    """Turn free text into one of a fixed set of labels, or into a human's queue."""

    def __init__(
        self,
        provider: Provider,
        *,
        allowed_labels: list[str],
        pricing: Pricing,
        max_attempts: int = 3,
        timeout: float = 20.0,
        min_confidence: float = 0.6,
        backoff_base: float = 1.5,
        ledger: Ledger | None = None,
        exceptions: ExceptionQueue | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not allowed_labels:
            raise ValueError("A classifier with no allowed labels cannot decide anything.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self.provider = provider
        self.allowed = list(allowed_labels)
        self.allowed_set = set(allowed_labels) | {"unknown"}
        self.pricing = pricing
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.min_confidence = min_confidence
        self.backoff_base = backoff_base
        self.ledger = ledger if ledger is not None else Ledger()
        self.exceptions = exceptions if exceptions is not None else ExceptionQueue()
        self._sleep = sleep

    def classify(self, text: str) -> Classification:
        """Label one message.

        Retries a transient failure. Never returns a label outside the allowed
        list. Marks an unsure answer for a human rather than guessing.
        """
        prompt = PROMPT.format(labels="\n".join(f"- {l}" for l in self.allowed), text=text)
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw, usage = self.provider.complete(prompt, timeout=self.timeout)
            except (ProviderTimeout, ProviderUnavailable) as exc:
                last_error = exc
                self.ledger.record(Usage(0, 0), 0.0, failed=True)
                if attempt < self.max_attempts:
                    self._sleep(self.backoff_base ** attempt)
                continue

            cost = self.pricing.cost(usage)
            self.ledger.record(usage, cost)

            try:
                label, confidence = self._parse(raw)
            except UnusableAnswer as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    self._sleep(self.backoff_base ** attempt)
                continue

            return Classification(
                label=label,
                confidence=confidence,
                usage=usage,
                cost_usd=cost,
                attempts=attempt,
                needs_human=confidence < self.min_confidence or label == "unknown",
            )

        reason = f"{type(last_error).__name__}: {last_error}"
        self.exceptions.add({"text": text}, reason)
        raise AIError(
            f"Gave up after {self.max_attempts} attempts. Queued for a human. Last error: {reason}"
        )

    def _parse(self, raw: str) -> tuple[str, float]:
        """Read the model's answer, refusing anything outside the allowed list."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UnusableAnswer(f"Answer was not JSON: {raw[:120]!r}") from exc

        if not isinstance(data, dict):
            raise UnusableAnswer(f"Answer was not an object: {raw[:120]!r}")

        label = data.get("label")
        if not isinstance(label, str):
            raise UnusableAnswer("Answer had no label.")
        if label not in self.allowed_set:
            raise UnusableAnswer(
                f"Model invented the label {label!r}, which is not on the allowed list."
            )

        confidence = data.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)):
            raise UnusableAnswer("Confidence was not a number.")
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise UnusableAnswer(f"Confidence {confidence} is outside 0 to 1.")

        return label, confidence

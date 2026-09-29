"""Providers, including stubs so the whole thing runs with no API key.

A real provider is a thin adapter: send the prompt, return the text and the
token usage. Everything that makes the call safe lives in client.py, so it is
tested once and applies to whichever vendor is plugged in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .client import ProviderTimeout, ProviderUnavailable, Usage

# Rough enough to price a call before it is made. Real adapters should use the
# token count the provider returns rather than this.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


RULES: list[tuple[str, str, float]] = [
    (r"\b(refund|money back|charge ?back)\b", "refund", 0.94),
    (r"\b(where is|track|tracking|not arrived|delayed|shipping)\b", "shipping", 0.91),
    (r"\b(broken|damaged|faulty|cracked|scratched|arrived damaged)\b", "damaged", 0.93),
    (r"\b(cancel|wrong size|wrong item)\b|\bchange\b.{0,20}\b(order|size|address)\b",
     "order_change", 0.88),
    (r"\b(in stock|availability|restock|back in stock)\b", "stock", 0.86),
]


@dataclass
class StubProvider:
    """Answers deterministically from keyword rules.

    This exists so the tests are repeatable and so anyone can clone the repo
    and run it without a key or a bill. It is not pretending to be a model.
    """

    calls: int = 0

    def complete(self, prompt: str, *, timeout: float) -> tuple[str, Usage]:
        self.calls += 1
        message = prompt.rsplit("The message:", 1)[-1].strip().lower()

        label, confidence = "unknown", 0.25
        for pattern, candidate, score in RULES:
            if re.search(pattern, message):
                label, confidence = candidate, score
                break

        answer = json.dumps({"label": label, "confidence": confidence})
        return answer, Usage(
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(answer),
        )


@dataclass
class ScriptedProvider:
    """Replays a fixed list of outcomes, for testing the unhappy paths.

    Each entry is either a string to return, or an exception to raise.
    """

    script: list[object] = field(default_factory=list)
    calls: int = 0

    def complete(self, prompt: str, *, timeout: float) -> tuple[str, Usage]:
        if self.calls >= len(self.script):
            raise ProviderUnavailable("Script exhausted.")
        step = self.script[self.calls]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        text = str(step)
        return text, Usage(
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(text),
        )


@dataclass
class SlowProvider:
    """Always times out. Used to prove the timeout path is handled."""

    def complete(self, prompt: str, *, timeout: float) -> tuple[str, Usage]:
        raise ProviderTimeout(f"No answer within {timeout}s.")

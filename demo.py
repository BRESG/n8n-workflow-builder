#!/usr/bin/env python3
"""Run the classifier over a handful of messages and print what it cost.

No API key. The stub provider answers from keyword rules so the output is the
same every time.

    python3 demo.py
"""

from __future__ import annotations

from aiclient import AIError, Classifier, ExceptionQueue, Ledger, Pricing, StubProvider

TAGS = ["refund", "shipping", "damaged", "order_change", "stock"]

MESSAGES = [
    "Hi, I would like a refund on order 10482 please",
    "Where is my parcel? It was due Tuesday",
    "The table arrived damaged, one leg is cracked",
    "Can I change the size on my order before it ships",
    "Is the walnut shelf back in stock yet",
    "Just wanted to say the chair looks lovely in our hallway",
]


def main() -> int:
    ledger = Ledger()
    queue = ExceptionQueue()
    classifier = Classifier(
        StubProvider(),
        allowed_labels=TAGS,
        pricing=Pricing(input_per_million=3.0, output_per_million=15.0),
        ledger=ledger,
        exceptions=queue,
    )

    print(f"{'tag':<14}{'conf':>6}  {'to a human':<12}message")
    print("-" * 78)
    for message in MESSAGES:
        try:
            result = classifier.classify(message)
        except AIError as exc:
            print(f"{'FAILED':<14}{'':>6}  {'yes':<12}{exc}")
            continue
        print(
            f"{result.label:<14}{result.confidence:>6.2f}  "
            f"{('yes' if result.needs_human else ''):<12}{message[:44]}"
        )

    print("-" * 78)
    print(f"calls          {ledger.calls}")
    print(f"failed calls   {ledger.failed_calls}")
    print(f"tokens in/out  {ledger.input_tokens} / {ledger.output_tokens}")
    print(f"total cost     ${ledger.cost_usd:.6f}")
    print(f"cost per call  ${ledger.cost_per_call():.6f}")
    print(f"exception queue {len(queue)} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

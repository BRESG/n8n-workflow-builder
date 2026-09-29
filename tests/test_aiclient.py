"""What has to hold when the model misbehaves.

The happy path is one test. The rest are the reasons this module exists:
invented labels, unsure answers, timeouts, flaky calls, and knowing what it
all cost.
"""

import json
import unittest

from aiclient import (
    AIError,
    Classifier,
    ExceptionQueue,
    Ledger,
    Pricing,
    ProviderTimeout,
    ProviderUnavailable,
    ScriptedProvider,
    SlowProvider,
    StubProvider,
    Usage,
)

TAGS = ["refund", "shipping", "damaged", "order_change", "stock"]
PRICE = Pricing(input_per_million=3.0, output_per_million=15.0)


def make(provider, **kwargs):
    """A classifier that never really sleeps, so the suite stays fast."""
    kwargs.setdefault("sleep", lambda _seconds: None)
    return Classifier(
        provider, allowed_labels=TAGS, pricing=PRICE, **kwargs
    )


class TestHappyPath(unittest.TestCase):
    def test_a_clear_message_gets_a_label(self):
        result = make(StubProvider()).classify("I want a refund for this order")
        self.assertEqual(result.label, "refund")
        self.assertFalse(result.needs_human)
        self.assertEqual(result.attempts, 1)

    def test_every_call_is_priced(self):
        result = make(StubProvider()).classify("where is my parcel")
        self.assertGreater(result.cost_usd, 0)
        self.assertEqual(result.cost_usd, PRICE.cost(result.usage))


class TestTheModelMisbehaving(unittest.TestCase):
    def test_an_invented_label_is_refused(self):
        """The failure this module exists to stop: a tag nobody defined."""
        provider = ScriptedProvider(
            script=[json.dumps({"label": "please_escalate", "confidence": 0.99})] * 3
        )
        with self.assertRaises(AIError):
            make(provider).classify("anything")

    def test_an_invented_label_never_reaches_the_caller(self):
        provider = ScriptedProvider(
            script=[
                json.dumps({"label": "not_a_real_tag", "confidence": 0.99}),
                json.dumps({"label": "refund", "confidence": 0.9}),
            ]
        )
        result = make(provider).classify("anything")
        self.assertEqual(result.label, "refund")
        self.assertEqual(result.attempts, 2)

    def test_a_non_json_answer_is_retried_not_trusted(self):
        provider = ScriptedProvider(
            script=["Sure! Here you go:", json.dumps({"label": "stock", "confidence": 0.8})]
        )
        result = make(provider).classify("is this back in stock")
        self.assertEqual(result.label, "stock")

    def test_confidence_outside_zero_to_one_is_refused(self):
        provider = ScriptedProvider(
            script=[json.dumps({"label": "refund", "confidence": 4.2})] * 3
        )
        with self.assertRaises(AIError):
            make(provider).classify("anything")

    def test_an_unsure_answer_goes_to_a_person(self):
        provider = ScriptedProvider(
            script=[json.dumps({"label": "refund", "confidence": 0.2})]
        )
        result = make(provider).classify("anything")
        self.assertTrue(result.needs_human)
        self.assertEqual(result.label, "refund")

    def test_unknown_always_goes_to_a_person_however_sure_the_model_is(self):
        provider = ScriptedProvider(
            script=[json.dumps({"label": "unknown", "confidence": 1.0})]
        )
        self.assertTrue(make(provider).classify("...").needs_human)

    def test_a_message_that_matches_nothing_is_not_guessed(self):
        result = make(StubProvider()).classify("just saying hello")
        self.assertEqual(result.label, "unknown")
        self.assertTrue(result.needs_human)


class TestTheNetworkMisbehaving(unittest.TestCase):
    def test_a_flaky_call_is_retried_and_succeeds(self):
        provider = ScriptedProvider(
            script=[
                ProviderUnavailable("502"),
                ProviderUnavailable("502"),
                json.dumps({"label": "damaged", "confidence": 0.9}),
            ]
        )
        result = make(provider).classify("it arrived broken")
        self.assertEqual(result.label, "damaged")
        self.assertEqual(result.attempts, 3)

    def test_it_stops_at_max_attempts_rather_than_retrying_forever(self):
        provider = ScriptedProvider(script=[ProviderUnavailable("502")] * 10)
        with self.assertRaises(AIError):
            make(provider, max_attempts=3).classify("anything")
        self.assertEqual(provider.calls, 3)

    def test_backoff_grows_between_attempts(self):
        waits = []
        provider = ScriptedProvider(script=[ProviderUnavailable("502")] * 3)
        classifier = Classifier(
            provider,
            allowed_labels=TAGS,
            pricing=PRICE,
            max_attempts=3,
            sleep=waits.append,
        )
        with self.assertRaises(AIError):
            classifier.classify("anything")
        self.assertEqual(waits, sorted(waits))
        self.assertEqual(len(waits), 2)

    def test_a_timeout_is_handled_like_any_other_failure(self):
        with self.assertRaises(AIError):
            make(SlowProvider(), max_attempts=2).classify("anything")

    def test_nothing_is_dropped_in_silence(self):
        """A ticket that fails every attempt must be findable afterwards."""
        queue = ExceptionQueue()
        provider = ScriptedProvider(script=[ProviderTimeout("slow")] * 3)
        with self.assertRaises(AIError):
            make(provider, max_attempts=3, exceptions=queue).classify("help me")
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue.items[0]["payload"]["text"], "help me")
        self.assertIn("ProviderTimeout", queue.items[0]["reason"])


class TestCost(unittest.TestCase):
    def test_pricing_maths(self):
        self.assertEqual(
            Pricing(3.0, 15.0).cost(Usage(1_000_000, 1_000_000)), 18.0
        )

    def test_the_ledger_adds_up_across_calls(self):
        ledger = Ledger()
        classifier = make(StubProvider(), ledger=ledger)
        for message in ["refund please", "where is it", "it is damaged"]:
            classifier.classify(message)
        self.assertEqual(ledger.calls, 3)
        self.assertGreater(ledger.cost_usd, 0)
        self.assertAlmostEqual(
            ledger.cost_per_call(), ledger.cost_usd / 3, places=8
        )

    def test_a_failed_call_is_counted_so_waste_is_visible(self):
        ledger = Ledger()
        provider = ScriptedProvider(script=[ProviderUnavailable("502")] * 2)
        with self.assertRaises(AIError):
            make(provider, max_attempts=2, ledger=ledger).classify("anything")
        self.assertEqual(ledger.failed_calls, 2)


class TestSetup(unittest.TestCase):
    def test_a_classifier_with_no_labels_is_refused(self):
        with self.assertRaises(ValueError):
            Classifier(StubProvider(), allowed_labels=[], pricing=PRICE)

    def test_zero_attempts_is_refused(self):
        with self.assertRaises(ValueError):
            Classifier(
                StubProvider(), allowed_labels=TAGS, pricing=PRICE, max_attempts=0
            )


if __name__ == "__main__":
    unittest.main()

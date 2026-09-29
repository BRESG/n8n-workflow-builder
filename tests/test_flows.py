"""Rules every flow in this repo has to follow.

These run against each flow rather than against one, so a flow added later
inherits the same standards without anyone remembering to write the tests.
"""

import json
import pathlib
import unittest

import build as build_script
from builder.workflow import has_extra_outputs, is_trigger

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

FLOWS = {name: builder() for name, builder in build_script.FLOWS.items()}


class TestEveryFlow(unittest.TestCase):
    def test_they_all_validate(self):
        for name, flow in FLOWS.items():
            with self.subTest(flow=name):
                flow.validate()

    def test_each_has_exactly_one_way_in(self):
        for name, flow in FLOWS.items():
            with self.subTest(flow=name):
                self.assertEqual(len(flow.triggers()), 1)

    def test_every_outbound_call_has_a_timeout(self):
        """A call with no timeout can hang the run until someone notices."""
        for name, flow in FLOWS.items():
            for node in flow.nodes:
                if node.type != "n8n-nodes-base.httpRequest":
                    continue
                with self.subTest(flow=name, node=node.name):
                    self.assertIn("timeout", node.parameters["options"])
                    self.assertGreater(node.parameters["options"]["timeout"], 0)

    def test_every_outbound_call_retries(self):
        for name, flow in FLOWS.items():
            for node in flow.nodes:
                if node.type != "n8n-nodes-base.httpRequest":
                    continue
                with self.subTest(flow=name, node=node.name):
                    retry = node.parameters["options"]["retry"]
                    self.assertTrue(retry["retryOnFail"])
                    self.assertGreaterEqual(retry["maxTries"], 2)

    def test_every_outbound_call_sends_its_failures_somewhere(self):
        """The rule that stops work disappearing when a call fails."""
        for name, flow in FLOWS.items():
            wired = {(c.source, c.source_output) for c in flow.connections}
            for node in flow.nodes:
                if node.type != "n8n-nodes-base.httpRequest":
                    continue
                with self.subTest(flow=name, node=node.name):
                    self.assertTrue(
                        node.continue_on_fail,
                        f"{node.name} would stop the run instead of handling the error",
                    )
                    self.assertIn(
                        (node.name, 1),
                        wired,
                        f"{node.name} has an error output that goes nowhere",
                    )

    def test_no_node_is_left_without_a_position(self):
        for name, flow in FLOWS.items():
            data = flow.to_dict()
            with self.subTest(flow=name):
                for node in data["nodes"]:
                    self.assertEqual(len(node["position"]), 2)

    def test_node_ids_are_unique_inside_a_flow(self):
        for name, flow in FLOWS.items():
            ids = [n["id"] for n in flow.to_dict()["nodes"]]
            with self.subTest(flow=name):
                self.assertEqual(len(ids), len(set(ids)))

    def test_rebuilding_produces_identical_bytes(self):
        for name, builder in build_script.FLOWS.items():
            with self.subTest(flow=name):
                self.assertEqual(builder().to_json(), builder().to_json())

    def test_a_branch_is_only_taken_from_a_node_that_has_one(self):
        for name, flow in FLOWS.items():
            for conn in flow.connections:
                if conn.source_output == 0:
                    continue
                with self.subTest(flow=name, node=conn.source):
                    self.assertTrue(has_extra_outputs(flow.node(conn.source)))

    def test_the_trigger_is_the_only_node_nothing_feeds(self):
        for name, flow in FLOWS.items():
            fed = {c.target for c in flow.connections}
            for node in flow.nodes:
                if node.name in fed:
                    continue
                with self.subTest(flow=name, node=node.name):
                    self.assertTrue(is_trigger(node.type))


class TestCommittedOutput(unittest.TestCase):
    def test_the_json_in_out_matches_the_source(self):
        """Catches someone editing a flow and forgetting to run build.py."""
        self.assertEqual(
            build_script.main(["--check"]),
            0,
            "out/ is stale. Run: python3 build.py",
        )

    def test_every_committed_file_is_valid_json(self):
        for name in build_script.FLOWS:
            path = OUT / name
            with self.subTest(file=name):
                self.assertTrue(path.exists(), f"{name} has never been built")
                json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

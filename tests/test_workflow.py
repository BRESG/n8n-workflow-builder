"""What the builder must refuse to produce.

These tests are mostly about failure. A builder that only proves the happy
path would let every mistake it was written to prevent straight through.
"""

import json
import unittest

from builder import Node, Workflow, WorkflowError, stable_id
from builder.nodes import filter_node, http_request, no_op, schedule_trigger


def tiny() -> Workflow:
    flow = Workflow("Tiny")
    start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
    end = flow.add(no_op("End"))
    flow.chain(start, end)
    return flow


class TestIds(unittest.TestCase):
    def test_same_node_gets_the_same_id_every_build(self):
        self.assertEqual(
            stable_id("Flow", "Node"),
            stable_id("Flow", "Node"),
        )

    def test_a_different_node_gets_a_different_id(self):
        self.assertNotEqual(stable_id("Flow", "A"), stable_id("Flow", "B"))

    def test_the_same_node_name_in_another_flow_differs(self):
        self.assertNotEqual(stable_id("Flow A", "Node"), stable_id("Flow B", "Node"))

    def test_id_looks_like_a_uuid_so_n8n_accepts_it(self):
        parts = stable_id("Flow", "Node").split("-")
        self.assertEqual([len(p) for p in parts], [8, 4, 4, 4, 12])


class TestRefusals(unittest.TestCase):
    def test_duplicate_node_names_are_refused(self):
        flow = Workflow("Dupes")
        flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        with self.assertRaises(WorkflowError) as caught:
            flow.add(no_op("Start"))
        self.assertIn("connections by name", str(caught.exception))

    def test_a_wire_to_a_node_that_does_not_exist_is_refused(self):
        flow = tiny()
        flow.connect("End", "Nowhere")
        with self.assertRaises(WorkflowError) as caught:
            flow.validate()
        self.assertIn("Nowhere", str(caught.exception))

    def test_a_node_wired_to_itself_is_refused(self):
        flow = tiny()
        flow.connect("End", "End")
        with self.assertRaises(WorkflowError):
            flow.validate()

    def test_a_flow_with_no_trigger_is_refused(self):
        flow = Workflow("No way in")
        a = flow.add(no_op("A"))
        b = flow.add(no_op("B"))
        flow.chain(a, b)
        with self.assertRaises(WorkflowError) as caught:
            flow.validate()
        self.assertIn("trigger", str(caught.exception))

    def test_an_orphan_node_is_refused(self):
        flow = tiny()
        flow.add(no_op("Forgotten"))
        with self.assertRaises(WorkflowError) as caught:
            flow.validate()
        self.assertIn("Forgotten", str(caught.exception))

    def test_an_empty_flow_is_refused(self):
        with self.assertRaises(WorkflowError):
            Workflow("Empty").validate()

    def test_a_flow_needs_a_name(self):
        with self.assertRaises(WorkflowError):
            Workflow("   ")

    def test_wiring_from_an_output_a_node_does_not_have_is_refused(self):
        """The bug this exists for: in n8n that wire silently never fires."""
        flow = Workflow("Bad output")
        start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        middle = flow.add(no_op("Middle"))
        end = flow.add(no_op("End"))
        flow.chain(start, middle)
        flow.connect(middle, end, source_output=1)
        with self.assertRaises(WorkflowError) as caught:
            flow.validate()
        self.assertIn("only has one output", str(caught.exception))

    def test_an_error_output_is_allowed_when_the_node_continues_on_fail(self):
        flow = Workflow("Good output")
        start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        call = flow.add(http_request("Call", url="https://example.test"))
        ok = flow.add(no_op("Ok"))
        failed = flow.add(no_op("Failed"))
        flow.chain(start, call, ok)
        flow.connect(call, failed, source_output=1)
        flow.validate()

    def test_a_branching_node_may_use_its_second_output(self):
        flow = Workflow("Branching")
        start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        gate = flow.add(filter_node("Gate", expression="={{ true }}"))
        yes = flow.add(no_op("Yes"))
        no = flow.add(no_op("No"))
        flow.chain(start, gate, yes)
        flow.connect(gate, no, source_output=1)
        flow.validate()


class TestOutput(unittest.TestCase):
    def test_output_is_the_shape_n8n_imports(self):
        data = tiny().to_dict()
        self.assertEqual(
            sorted(data), ["connections", "name", "nodes", "pinData", "settings"]
        )
        for node in data["nodes"]:
            self.assertEqual(
                sorted(k for k in node if k in
                       {"id", "name", "parameters", "position", "type", "typeVersion"}),
                ["id", "name", "parameters", "position", "type", "typeVersion"],
            )
            self.assertIsInstance(node["position"], list)
            self.assertEqual(len(node["position"]), 2)

    def test_connections_are_keyed_by_node_name(self):
        data = tiny().to_dict()
        self.assertIn("Start", data["connections"])
        wire = data["connections"]["Start"]["main"][0][0]
        self.assertEqual(wire, {"node": "End", "type": "main", "index": 0})

    def test_two_builds_produce_identical_bytes(self):
        """If this fails, a diff between versions stops meaning anything."""
        self.assertEqual(tiny().to_json(), tiny().to_json())

    def test_output_is_valid_json(self):
        json.loads(tiny().to_json())

    def test_no_two_nodes_land_on_the_same_spot(self):
        flow = Workflow("Layout")
        start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        a = flow.add(no_op("A"))
        b = flow.add(no_op("B"))
        flow.chain(start, a)
        flow.connect(start, b)
        positions = [tuple(n["position"]) for n in flow.to_dict()["nodes"]]
        self.assertEqual(len(positions), len(set(positions)))

    def test_a_hand_placed_position_is_kept(self):
        flow = Workflow("Manual")
        start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        end = flow.add(Node(name="End", type="n8n-nodes-base.noOp", position=(999, 42)))
        flow.chain(start, end)
        placed = {n["name"]: n["position"] for n in flow.to_dict()["nodes"]}
        self.assertEqual(placed["End"], [999, 42])

    def test_a_loop_back_to_an_earlier_node_does_not_hang_the_layout(self):
        flow = Workflow("Loop")
        start = flow.add(schedule_trigger("Start", cron="0 9 * * *"))
        a = flow.add(no_op("A"))
        b = flow.add(no_op("B"))
        flow.chain(start, a, b)
        flow.connect(b, a)
        flow.to_dict()


if __name__ == "__main__":
    unittest.main()

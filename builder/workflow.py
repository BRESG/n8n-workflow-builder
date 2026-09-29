"""Build an n8n workflow as data, then emit the JSON that n8n imports.

Nothing in this module talks to n8n. It produces the same document shape the
n8n editor produces when you export a workflow, which means the output can be
imported straight into an instance, and can also be asserted against in tests
before it ever gets there.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

COLUMN_WIDTH = 220
ROW_HEIGHT = 150
ORIGIN = (0, 0)


class WorkflowError(ValueError):
    """Raised when a workflow is structurally wrong.

    Every message names the node so the failure is actionable without opening
    the generated JSON.
    """


def stable_id(*parts: str) -> str:
    """Return a deterministic node id.

    n8n assigns a random uuid to every node. Random ids mean every rebuild
    produces a different file, so `git diff` between two builds is noise and
    you cannot see what actually changed. Hashing the workflow name and the
    node name gives the same id for the same node forever, so a diff shows
    only real changes. That is the whole reason for generating the file
    instead of clicking it together.
    """
    digest = hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()
    return "-".join(
        [digest[0:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]]
    )


@dataclass
class Node:
    """One node on the canvas."""

    name: str
    type: str
    type_version: float = 1.0
    parameters: dict[str, Any] = field(default_factory=dict)
    position: tuple[int, int] | None = None
    always_output_data: bool = False
    continue_on_fail: bool = False
    notes: str = ""

    def to_dict(self, workflow_name: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "parameters": self.parameters,
            "id": stable_id(workflow_name, self.name),
            "name": self.name,
            "type": self.type,
            "typeVersion": self.type_version,
            "position": list(self.position or ORIGIN),
        }
        if self.always_output_data:
            out["alwaysOutputData"] = True
        if self.continue_on_fail:
            out["onError"] = "continueErrorOutput"
        if self.notes:
            out["notes"] = self.notes
            out["notesInFlow"] = True
        return out


@dataclass
class Connection:
    """A wire from one node's output to another node's input."""

    source: str
    target: str
    source_output: int = 0
    target_input: int = 0


class Workflow:
    """A workflow you can build, check, and write out."""

    def __init__(self, name: str, *, execution_order: str = "v1") -> None:
        if not name.strip():
            raise WorkflowError("A workflow needs a name.")
        self.name = name
        self.execution_order = execution_order
        self._nodes: list[Node] = []
        self._connections: list[Connection] = []

    # ---------------------------------------------------------------- build

    def add(self, node: Node) -> Node:
        """Add a node. Duplicate names are rejected.

        n8n keys its connections by node NAME, not by id, so two nodes sharing
        a name silently merges their wiring. That is a genuinely nasty bug to
        chase in the editor, and it is free to prevent here.
        """
        if any(existing.name == node.name for existing in self._nodes):
            raise WorkflowError(
                f"Two nodes are both named {node.name!r}. "
                "n8n keys connections by name, so the wiring would merge."
            )
        self._nodes.append(node)
        return node

    def connect(
        self,
        source: Node | str,
        target: Node | str,
        *,
        source_output: int = 0,
        target_input: int = 0,
    ) -> None:
        """Wire one node to another."""
        self._connections.append(
            Connection(
                source=source.name if isinstance(source, Node) else source,
                target=target.name if isinstance(target, Node) else target,
                source_output=source_output,
                target_input=target_input,
            )
        )

    def chain(self, *nodes: Node) -> None:
        """Wire a straight line of nodes, each output into the next input."""
        for left, right in zip(nodes, nodes[1:]):
            self.connect(left, right)

    def layout(self) -> None:
        """Place any node that has no position on a simple grid.

        Position is cosmetic to n8n but not to a human opening the workflow.
        A generated file that lands every node on top of the others is
        technically valid and practically unreadable.
        """
        depth = self._depths()
        rows: dict[int, int] = {}
        for node in self._nodes:
            if node.position is not None:
                continue
            column = depth.get(node.name, 0)
            row = rows.get(column, 0)
            rows[column] = row + 1
            node.position = (
                ORIGIN[0] + column * COLUMN_WIDTH,
                ORIGIN[1] + row * ROW_HEIGHT,
            )

    # ---------------------------------------------------------------- check

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes)

    @property
    def connections(self) -> list[Connection]:
        return list(self._connections)

    def node(self, name: str) -> Node:
        for candidate in self._nodes:
            if candidate.name == name:
                return candidate
        raise WorkflowError(f"No node named {name!r} in {self.name!r}.")

    def triggers(self) -> list[Node]:
        """Nodes n8n treats as entry points."""
        return [n for n in self._nodes if is_trigger(n.type)]

    def _depths(self) -> dict[str, int]:
        """How far each node sits from a trigger, for layout only."""
        incoming: dict[str, list[str]] = {n.name: [] for n in self._nodes}
        for conn in self._connections:
            if conn.target in incoming:
                incoming[conn.target].append(conn.source)

        depth: dict[str, int] = {}

        def resolve(name: str, seen: frozenset[str]) -> int:
            if name in depth:
                return depth[name]
            if name in seen:
                return 0
            parents = incoming.get(name, [])
            value = 0 if not parents else 1 + max(
                resolve(parent, seen | {name}) for parent in parents
            )
            depth[name] = value
            return value

        for node in self._nodes:
            resolve(node.name, frozenset())
        return depth

    def validate(self) -> None:
        """Raise on anything that would break, or quietly misbehave, in n8n."""
        if not self._nodes:
            raise WorkflowError(f"{self.name!r} has no nodes.")

        names = {n.name for n in self._nodes}

        for conn in self._connections:
            if conn.source not in names:
                raise WorkflowError(
                    f"Connection from {conn.source!r} goes nowhere: no such node."
                )
            if conn.target not in names:
                raise WorkflowError(
                    f"Connection into {conn.target!r} goes nowhere: no such node."
                )
            if conn.source == conn.target:
                raise WorkflowError(f"Node {conn.source!r} is wired to itself.")

        if not self.triggers():
            raise WorkflowError(
                f"{self.name!r} has no trigger node, so nothing would ever start it."
            )

        wired = {c.source for c in self._connections} | {
            c.target for c in self._connections
        }
        for node in self._nodes:
            if node.name in wired:
                continue
            if is_trigger(node.type) and len(self._nodes) == 1:
                continue
            raise WorkflowError(
                f"Node {node.name!r} is not wired to anything. "
                "An orphan node never runs and is almost always a mistake."
            )

        for conn in self._connections:
            if conn.source_output == 0:
                continue
            source = self.node(conn.source)
            if has_extra_outputs(source):
                continue
            raise WorkflowError(
                f"Node {conn.source!r} is wired from output {conn.source_output}, "
                "but that node only has one output. In n8n this wire silently "
                "never fires. Set continue_on_fail for an error output, or use "
                "a node that branches."
            )

    # ----------------------------------------------------------------- emit

    def to_dict(self) -> dict[str, Any]:
        self.layout()
        self.validate()

        connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}
        for conn in self._connections:
            main = connections.setdefault(conn.source, {}).setdefault("main", [])
            while len(main) <= conn.source_output:
                main.append([])
            main[conn.source_output].append(
                {"node": conn.target, "type": "main", "index": conn.target_input}
            )

        return {
            "name": self.name,
            "nodes": [n.to_dict(self.name) for n in self._nodes],
            "connections": connections,
            "settings": {"executionOrder": self.execution_order},
            "pinData": {},
        }

    def to_json(self) -> str:
        """The exact bytes written to disk. Sorted and indented so it diffs."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"


TRIGGER_MARKERS = ("trigger", "webhook", "n8n-nodes-base.cron")
MULTI_OUTPUT_TYPES = (
    "n8n-nodes-base.if",
    "n8n-nodes-base.switch",
    "n8n-nodes-base.filter",
    "n8n-nodes-base.splitInBatches",
)


def is_trigger(node_type: str) -> bool:
    lowered = node_type.lower()
    return any(marker in lowered for marker in TRIGGER_MARKERS)


def has_extra_outputs(node: "Node") -> bool:
    """True when this node really does expose an output beyond index 0.

    Two things create one: a node type that branches, such as an If or a
    Filter or a batch loop, and a node set to continue on failure, which adds
    an error output.
    """
    return node.type in MULTI_OUTPUT_TYPES or node.continue_on_fail

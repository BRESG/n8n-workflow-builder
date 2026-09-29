"""Factories for the n8n nodes these flows use.

Each function returns a Node with the real n8n type string and a typeVersion
that matches what the editor writes today. Keeping them in one file means a
version bump is one edit, not a search through every flow.
"""

from __future__ import annotations

from typing import Any

from .workflow import Node


def schedule_trigger(name: str, *, cron: str) -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.scheduleTrigger",
        type_version=1.2,
        parameters={
            "rule": {"interval": [{"field": "cronExpression", "expression": cron}]}
        },
    )


def webhook(name: str, *, path: str, method: str = "POST") -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.webhook",
        type_version=2,
        parameters={
            "path": path,
            "httpMethod": method,
            "responseMode": "responseNode",
            "options": {},
        },
    )


def google_sheets_read(name: str, *, document_id: str, sheet_name: str) -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.googleSheets",
        type_version=4.5,
        parameters={
            "documentId": {"__rl": True, "value": document_id, "mode": "id"},
            "sheetName": {"__rl": True, "value": sheet_name, "mode": "name"},
            "options": {},
        },
    )


def google_sheets_update(name: str, *, document_id: str, sheet_name: str,
                         matching_column: str) -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.googleSheets",
        type_version=4.5,
        parameters={
            "operation": "appendOrUpdate",
            "documentId": {"__rl": True, "value": document_id, "mode": "id"},
            "sheetName": {"__rl": True, "value": sheet_name, "mode": "name"},
            "columns": {
                "mappingMode": "autoMapInputData",
                "matchingColumns": [matching_column],
            },
            "options": {},
        },
    )


def filter_node(name: str, *, expression: str, description: str = "") -> Node:
    """A filter that drops items failing the expression.

    The expression is stored as a single boolean condition rather than n8n's
    builder UI structure, because a generated file is read as text far more
    often than it is opened in the editor.
    """
    return Node(
        name=name,
        type="n8n-nodes-base.filter",
        type_version=2.2,
        parameters={
            "conditions": {
                "options": {"caseSensitive": True, "version": 2},
                "conditions": [
                    {
                        "leftValue": expression,
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        notes=description,
    )


def split_in_batches(name: str, *, batch_size: int = 25) -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.splitInBatches",
        type_version=3,
        parameters={"batchSize": batch_size, "options": {}},
    )


def http_request(
    name: str,
    *,
    url: str,
    method: str = "POST",
    body: dict[str, Any] | None = None,
    retries: int = 3,
    timeout_ms: int = 20000,
    continue_on_fail: bool = True,
) -> Node:
    """An HTTP call with retries, a timeout, and an error output.

    Every one of these three is off by default in n8n. A generated file is the
    right place to make the safe setting the default, because it applies to
    every flow built from it rather than to whichever node someone remembered
    to tick.
    """
    parameters: dict[str, Any] = {
        "method": method,
        "url": url,
        "sendBody": body is not None,
        "options": {
            "timeout": timeout_ms,
            "response": {"response": {"neverError": False}},
        },
    }
    if body is not None:
        parameters["specifyBody"] = "json"
        parameters["jsonBody"] = body
    node = Node(
        name=name,
        type="n8n-nodes-base.httpRequest",
        type_version=4.2,
        parameters=parameters,
        continue_on_fail=continue_on_fail,
    )
    node.parameters["options"]["retry"] = {
        "retryOnFail": retries > 0,
        "maxTries": max(retries, 1),
        "waitBetweenTries": 2000,
    }
    return node


def code_node(name: str, *, js: str, description: str = "") -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.code",
        type_version=2,
        parameters={"jsCode": js},
        notes=description,
    )


def set_fields(name: str, *, fields: dict[str, str]) -> Node:
    assignments = [
        {
            "id": f"assign-{index}",
            "name": key,
            "value": value,
            "type": "string",
        }
        for index, (key, value) in enumerate(sorted(fields.items()))
    ]
    return Node(
        name=name,
        type="n8n-nodes-base.set",
        type_version=3.4,
        parameters={
            "mode": "manual",
            "assignments": {"assignments": assignments},
            "options": {},
        },
    )


def no_op(name: str, *, description: str = "") -> Node:
    return Node(
        name=name,
        type="n8n-nodes-base.noOp",
        type_version=1,
        parameters={},
        notes=description,
    )

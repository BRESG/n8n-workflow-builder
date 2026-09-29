"""Tag an incoming support ticket with a model, safely.

The first thing worth saying is that a Shopify store does not hold support
tickets. They live in a helpdesk, or they arrive as email. So this flow takes
the ticket from wherever it already is by webhook, and only reaches into
Shopify for the order context that makes the tag better.

The design is the same shape as aiclient/client.py, which is the tested
reference implementation of the classify step:

  a fixed list of allowed tags, so the model cannot invent one
  an unsure answer goes to a person instead of being written
  retries and a timeout on every outbound call
  an error output on each call, wired to a queue
  the cost of each call written to a log, so spend is visible daily
"""

from __future__ import annotations

from builder import Workflow
from builder.nodes import (
    code_node,
    filter_node,
    http_request,
    no_op,
    set_fields,
    webhook,
)

ALLOWED_TAGS = ["refund", "shipping", "damaged", "order_change", "stock"]


def build() -> Workflow:
    flow = Workflow("Support ticket tagging")

    incoming = flow.add(webhook("New ticket", path="support-ticket"))

    context = flow.add(
        http_request(
            "Recent order from Shopify",
            url="={{ $env.SHOPIFY_ADMIN_URL }}/orders.json?email={{ $json.from_email }}&limit=1",
            method="GET",
            retries=2,
            timeout_ms=10000,
        )
    )

    classify = flow.add(
        http_request(
            "Classify the ticket",
            url="={{ $env.MODEL_ENDPOINT }}",
            body={
                "message": "={{ $json.body }}",
                "order_context": "={{ $json.order }}",
                "allowed_labels": ALLOWED_TAGS,
                "response_format": "json",
            },
            retries=3,
            timeout_ms=20000,
        )
    )

    check = flow.add(
        code_node(
            "Reject an invented tag",
            js=(
                "const allowed = new Set(" + repr(ALLOWED_TAGS).replace("'", '"') + ");\n"
                "return items.map(item => {\n"
                "  const label = item.json.label;\n"
                "  const confidence = Number(item.json.confidence ?? 0);\n"
                "  const usable = allowed.has(label) && confidence >= 0.6;\n"
                "  return { json: { ...item.json, usable } };\n"
                "});"
            ),
            description=(
                "The model is asked for one of a fixed list. This step checks "
                "the answer is on that list before anything is written. An "
                "unsure answer is marked unusable rather than guessed."
            ),
        )
    )

    usable = flow.add(
        filter_node(
            "Confident and on the list",
            expression="={{ $json.usable === true }}",
            description="Everything else falls through to a person.",
        )
    )

    write_tag = flow.add(
        http_request(
            "Write the tag back",
            url="={{ $env.HELPDESK_URL }}/tickets/{{ $json.ticket_id }}/tags",
            body={"tags": "={{ [$json.label] }}"},
            retries=2,
            timeout_ms=10000,
        )
    )

    human = flow.add(
        set_fields(
            "Send to a human",
            fields={
                "queue": "manual_review",
                "reason": "={{ $json.usable ? 'call failed' : 'low confidence or unknown tag' }}",
                "ticket_id": "={{ $json.ticket_id }}",
            },
        )
    )

    log = flow.add(
        code_node(
            "Log tag and cost",
            js=(
                "return items.map(item => ({ json: {\n"
                "  ticket_id: item.json.ticket_id,\n"
                "  label: item.json.label ?? null,\n"
                "  confidence: item.json.confidence ?? null,\n"
                "  cost_usd: item.json.cost_usd ?? null,\n"
                "  at: new Date().toISOString(),\n"
                "} }));"
            ),
            description=(
                "One row per ticket. Volume is what makes this expensive, so "
                "the cost per call is recorded rather than discovered on the bill."
            ),
        )
    )

    done = flow.add(no_op("Done"))

    flow.chain(incoming, context, classify, check, usable, write_tag, log, done)
    flow.connect(usable, human, source_output=1)
    flow.connect(human, log)

    # Every outbound call has somewhere to put a failure.
    flow.connect(context, human, source_output=1)
    flow.connect(classify, human, source_output=1)
    flow.connect(write_tag, human, source_output=1)

    return flow

"""Dead-lead reactivation.

An old lead list is worth something only if someone contacts it again. This
flow walks the list on a schedule, skips anyone it should not touch, drafts a
message per lead, sends it, and writes back what happened so the same lead is
never worked twice.

The parts worth noticing are the guards, not the happy path:

  the filter runs before the model, so nothing is spent on a lead that was
  never eligible

  leads move in batches, so one bad row does not take the run down and a
  rate limit is survivable

  the send step has an error output wired to a queue, so a failure is
  captured rather than lost

  the sheet is updated whether the send worked or not, so a rerun does not
  contact the same person again
"""

from __future__ import annotations

from builder import Workflow
from builder.nodes import (
    filter_node,
    google_sheets_read,
    google_sheets_update,
    http_request,
    no_op,
    schedule_trigger,
    set_fields,
    split_in_batches,
)

SHEET_ID = "REPLACE_WITH_SHEET_ID"
QUIET_DAYS = 90


def build() -> Workflow:
    flow = Workflow("Dead lead reactivation")

    trigger = flow.add(schedule_trigger("Every weekday 9am", cron="0 9 * * 1-5"))

    read = flow.add(
        google_sheets_read("Read lead list", document_id=SHEET_ID, sheet_name="leads")
    )

    eligible = flow.add(
        filter_node(
            "Only eligible leads",
            expression=(
                "={{ $json.email "
                "&& $json.status !== 'contacted' "
                "&& $json.opted_out !== true "
                f"&& $json.days_since_contact >= {QUIET_DAYS} }}}}"
            ),
            description=(
                "Runs before the model so nothing is spent on a lead that was "
                "never eligible. Opt-out is checked here, not later."
            ),
        )
    )

    batches = flow.add(split_in_batches("In batches of 25", batch_size=25))

    draft = flow.add(
        http_request(
            "Draft the message",
            url="={{ $env.DRAFTING_ENDPOINT }}",
            body={
                "lead": "={{ $json }}",
                "allowed_tone": "plain",
                "max_words": 90,
            },
            retries=3,
            timeout_ms=20000,
        )
    )

    send = flow.add(
        http_request(
            "Send it",
            url="={{ $env.SEND_ENDPOINT }}",
            body={"to": "={{ $json.email }}", "body": "={{ $json.draft }}"},
            retries=2,
            timeout_ms=15000,
        )
    )

    mark = flow.add(
        set_fields(
            "Mark contacted",
            fields={
                "status": "contacted",
                "contacted_at": "={{ $now.toISO() }}",
                "channel": "email",
            },
        )
    )

    write = flow.add(
        google_sheets_update(
            "Write back to the sheet",
            document_id=SHEET_ID,
            sheet_name="leads",
            matching_column="lead_id",
        )
    )

    failures = flow.add(
        google_sheets_update(
            "Exception queue",
            document_id=SHEET_ID,
            sheet_name="exceptions",
            matching_column="lead_id",
        )
    )

    more = flow.add(
        no_op("Next batch", description="Loops back until the list is done.")
    )

    flow.chain(trigger, read, eligible, batches)
    flow.connect(batches, draft, source_output=1)
    flow.chain(draft, send, mark, write)
    flow.connect(write, more)
    flow.connect(more, batches)

    # Error outputs. Without these a failed call disappears.
    flow.connect(draft, failures, source_output=1)
    flow.connect(send, failures, source_output=1)

    return flow

# n8n workflows as code

Build an n8n workflow with Python, check it with tests, and commit the JSON.
Then a change to a workflow shows up as a diff you can read, instead of a
canvas someone rearranged.

No dependencies. Python 3.10 or newer, standard library only.

```bash
python3 -m unittest discover -s tests -t .   # 52 tests
python3 build.py                             # regenerate out/*.json
python3 demo.py                              # run the AI client, see the cost
```

## Why not just click it

Clicking a workflow together in the editor works right up until you need to
answer one of these:

- What changed between the version that worked and the version that does not?
- This client needs the same flow with different credentials. Do I rebuild it by hand?
- How do I know this change did not break the branch nobody looks at?
- Which node is missing a timeout?

A workflow built as code answers all four. It is a value you construct, so you
can assert things about it before it ever reaches an instance, and the output
is a file, so `git diff` tells you what moved.

The ids matter here. n8n gives every node a random uuid, so a rebuild produces
a different file even when nothing changed, and the diff becomes noise. This
builder derives each id from the workflow name and the node name, so the same
node keeps the same id forever and a diff only shows real changes.

## What refuses to build

Most of the value is in what the builder will not let you produce. Each of
these is a real mistake that is quiet and annoying to find in the editor.

| It refuses | Because in n8n |
|---|---|
| Two nodes with the same name | Connections are keyed by name, so the wiring silently merges |
| A wire to a node that does not exist | The connection is dropped without a word |
| A node wired to itself | Runs forever or not at all |
| A flow with no trigger | Nothing would ever start it |
| A node wired to nothing | It never runs, and it looks like it does |
| A wire from output 1 of a node that has one output | That wire never fires, and nothing tells you |

Every one of those has a test named after the mistake it prevents.

## The flows

**`flows/dead_lead_reactivation.py`** walks an old lead list on a schedule and
contacts the ones still worth contacting. The parts worth reading are the
guards: the eligibility filter runs before the model so nothing is spent on a
lead that was never eligible, leads move in batches so one bad row cannot take
the run down, and both outbound calls have their error output wired to a queue.

**`flows/support_ticket_tagging.py`** labels an incoming support ticket.

It starts by not answering the question as asked. A Shopify store does not hold
support tickets. They live in a helpdesk, or they arrive as email. So the flow
takes the ticket by webhook from wherever it already is, and only reaches into
Shopify for the order context that makes the tag better.

## Calling a model without it becoming a liability

`aiclient/` is the tested reference for the classify step in that second flow.
A model call is an unreliable network call that also costs money and can answer
with something you did not ask for. All three are treated as normal:

- **Constrained output.** The model may only answer with a label from a fixed
  list. Anything else is rejected before it is written anywhere.
- **Unsure goes to a person.** Below the confidence floor, or the label
  `unknown`, and it routes to a human rather than being guessed.
- **Retries with backoff**, capped, so a flaky provider does not become an
  outage and does not retry forever either.
- **Timeouts**, so a hung call is cut off.
- **Cost per call.** Every call is priced and accumulated. Volume is what makes
  this expensive, so spend is answerable at any moment rather than at invoice.
- **An exception queue.** A request that fails every attempt is captured with
  the reason. A dropped ticket is worse than a slow one, because nobody finds out.

It runs with no API key. The provider is an interface and the stub answers
deterministically, so the tests are repeatable and the demo costs nothing.

```
tag             conf  to a human  message
refund          0.94              Hi, I would like a refund on order 10482
shipping        0.91              Where is my parcel? It was due Tuesday
damaged         0.93              The table arrived damaged, one leg is cracked
order_change    0.88              Can I change the size on my order
stock           0.86              Is the walnut shelf back in stock yet
unknown         0.25  yes         Just wanted to say the chair looks lovely

calls 6 | total cost $0.002634 | cost per call $0.000439
```

## The tests

52, all standard library, all fast. They fall into three groups.

The builder tests are mostly about refusal. A builder that only proved the
happy path would let every mistake it was written to prevent straight through.

The AI client tests are mostly about the model and the network misbehaving:
invented labels, answers that are not JSON, confidence outside 0 to 1, flaky
calls, timeouts, and whether anything is dropped in silence.

The flow tests run against every flow rather than one, so a flow added later
inherits the same standards without anyone remembering to write new tests.
They assert that every outbound call has a timeout, retries, and an error
output that goes somewhere. One of them runs `build.py --check` and fails if
the committed JSON in `out/` does not match the source, which catches editing
a flow and forgetting to rebuild.

## Layout

```
builder/     Workflow, Node, validation, deterministic ids
builder/nodes.py   factories for the real n8n node types these flows use
flows/       one module per workflow
aiclient/    calling a model with retries, constraints and cost tracking
tests/       52 tests, standard library only
out/         the generated JSON, committed so the diff is reviewable
build.py     regenerate out/, or --check that it is current
demo.py      run the classifier over sample messages
```

## What this is

A public, runnable version of how I build automations. The dead-lead
reactivation flow follows a workflow I built for a client as a Python build
script with tests; the code here is written from scratch to be shared, with no
client data, credentials or sheet ids in it. The support ticket flow and the AI
client are written to the same pattern.

Written by Bryan Esguerra. Happy to walk through any of it.

MIT licensed.

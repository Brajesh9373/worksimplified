# ba_chat — standalone BA interview chatbot

Conversational requirements elicitation that works like a real business
analyst: viability first, outcomes before features, one requirement at a time,
every requirement played back for confirmation, sign-off before anything
builds. **Zero ADK imports** — plain Python + FastAPI + LiteLLM, mounted into
the connectors service by `my_agents/connectors/app.py`.

## How one turn works

```
browser ──► POST /api/ba/chat ──► engine.py ──► catalogue.py (what to ask)
                                      │  ▲        (groups A–G, coded checks)
                                      │  │
                                   store.py (SQLite: transcript + board)
                                      │
                    ┌─────────────────┴──────────────────┐
                    ▼                                    ▼
              flow.py ──► diagram data        frappe_client.py ──► Frappe
```

The engine is a deterministic state machine
(`INTAKE → CONTEXT → ELICIT → REVIEW → READY → DONE`); the LLM only does
language — questions, extraction, project-type classification. Coded
validators decide what counts as *fulfilled*; coded yes-patterns accept
confirmations (a "Yes" turn costs zero LLM calls); at most two probes per
requirement, then an assumption is recorded and flagged at review.

## Requirement catalogue (`catalogue.py`)

Groups A–G, 41 items drawn from four practices (BABOK/BRD, Agile stories +
Definition of Ready, ERP/Frappe implementation, agency discovery):

| Group | What the BA takes |
|---|---|
| A · Viability | project name, problem, goal + metric, budget, deadline, approver, constraints |
| B · Business | objectives + KPIs, scope in/out, stakeholders + RACI, as-is, to-be, value |
| C · Stakeholder | roles + permissions, user stories, acceptance criteria, MoSCoW |
| D · Functional | features, business rules, workflows + approvals, reports, notifications, integrations, data + masters |
| E · Non-functional | performance + scale, security, compliance, usability, availability + backup, hosting |
| F · Transition | migration scope, training, UAT, cutover + support (ERP/integration jobs) |
| G · Governance | assumptions, risks, dependencies, change control, sign-off, glossary |

## Endpoints

| Route | Purpose |
|---|---|
| `POST /api/ba/chat` | one turn → `{reply, items, coverage, flow, quick_replies, done}` |
| `GET /api/ba/state` | checklist + coverage + diagram + recent transcript (restores the page) |
| `GET /api/ba/flow` | diagram data only |
| `GET /api/ba/catalogue` | groups + item labels |
| `POST /api/ba/confirm` | explicit Yes/Change from the quick-reply buttons |
| `POST /api/ba/create-project` | sign-off gate (409 until READY) → background setup job, idempotent |
| `GET /api/ba/job` | job progress log (poll like `/api/progress`) |
| `GET /api/ba/export` | approved pack as a BRD-draft download |
| `GET /api/ba/llm-status` | model/base/key-tail (never the key) |

## Frappe setup (`frappe_client.py`)

Session login → get-or-create `Project` by name → one `Task` per
story/feature (idempotent by subject + project) → custom DocTypes **only**
from explicit `Entity (field, field)` lists in the data answer (never
guessed) → read-back verification. Every step is logged to the job record
the console polls.

## Configuration (all call-time env, no restart needed)

`LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL` (+ `LLM_MODEL_BA` override),
`FRAPPE_BASE_URL`, `FRAPPE_USERNAME`, `FRAPPE_PASSWORD`, `BA_CHAT_DB`
(SQLite path; defaults to `ba_chat/.state/sessions.db`, git-ignored).

## Tests

`my_agents/tests/test_ba_chat_{catalogue,engine,api}.py` — catalogue
consistency, the state machine with a stubbed LLM, and the routes with stubbed
LLM + Frappe. Run: `.venv/bin/python -m pytest my_agents/tests -q`

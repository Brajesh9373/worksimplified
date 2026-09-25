# Connectors — Phase 1 (Telegram · WhatsApp · Web)

Three front doors, one intake path, one pipeline.

```
Telegram  ─┐
WhatsApp  ─┼─→  connectors service (FastAPI)  ─→  ChannelCore  ─→  delivery_pipeline (ADK)
Web page  ─┘                                              │
                                                          └─→  projects/<id>/  (workspace evidence)
```

Each external conversation maps to exactly one ADK session and one project
workspace, so a second message never creates a second project.

## Run

```bash
cd /home/brajesh_kurkure/Projects/worksimplified/adk-python

# 1. connectors service (web + telegram + whatsapp inbound):
#    asks for any credential that is missing, then runs it in the foreground
./start.sh

# 2. open the web page
#    http://127.0.0.1:8765/     (Discover interview chat + run view)

# 3. health
curl -s localhost:8765/health
```

The console starts on 8765 and walks up (8766, 8767, …) to the first free port:
8000 and the neighbouring well-known ports belong to other services on the
server. `./start.sh` prints the port it settled on.

Use `./start.sh` rather than `python -m connectors.app`: it runs the service
launcher (`my_agents/connectors_service.py`), which this checkout's virtualenv
has an editable `.pth` pointing at a missing path, so `google.adk` only resolves
when `src/` is added explicitly — the launcher does that, and loads the pipeline's
`.env` (LLM_*, FRAPPE_*) the way `adk web` does.

### Which model the connectors use

The connectors drive the same pipeline, so the same config applies — the launcher
loads `delivery_pipeline/.env`, which is where the provider lives:

```env
LLM_API_BASE=https://api.commandcode.ai/provider/v1
LLM_MODEL=openai/deepseek/deepseek-v4.1-flash
LLM_API_KEY=user_…
```

Keep the **`openai/` prefix**. LiteLLM reads the first path segment as the
*provider*, so a bare `deepseek/…` routes to DeepSeek's own API and ignores
`LLM_API_BASE`. `openai/<id>` selects the OpenAI-compatible provider and sends
`<id>` to this gateway unchanged.

The same three lines are repeated in each agent's `.env` (`ba_agent`,
`project_agent`, …) because ADK loads the *first* `.env` found walking up from an
agent's directory — a per-agent file wins over the pipeline's.

If a model call fails, discovery falls back to a plain "tell me more" reply rather
than erroring, and `POST /intake` still seeds generation mode directly,
skipping the discovery turn.

### Telegram

Create a bot with **@BotFather** (`/newbot`), put the token in
`connectors/.env` as `TELEGRAM_BOT_TOKEN=…`, restart the service. The worker
long-polls — nothing needs to be publicly reachable. With no token it idles.

### WhatsApp (Baileys bridge)

```bash
cd my_agents/connectors/whatsapp_bridge
npm install
node index.js          # holds the pairing code; scan it in the console or the terminal
```

Then set `WHATSAPP_ENABLED=true` and an allowlist
(`WHATSAPP_ALLOWED_USERS=*` for anyone, or digits with country code, e.g.
`919812345678,447700900123`). **Unset means deny all** — that is deliberate.

The bridge posts inbound messages to the Python service and exposes `POST /send`
for replies. Its session lives in `whatsapp_bridge/session/` — treat it like a
password (`chmod 700`), never commit it.

**Pairing from the console.** The Connectors tab shows the pairing QR directly while
the bridge is up and not yet linked, so it does not have to be read out of the
bridge's terminal. The bridge keeps the current code (`GET /qr`), the service renders
it to an SVG with `segno`
(`uv pip install --python .venv/bin/python segno`), and the page re-reads it every few
seconds because Baileys rotates it until a phone scans it. Without `segno` the page
says so and the terminal remains the fallback. The code is a credential — whoever
scans it links their own device to the number — so it is only served while
`WHATSAPP_ENABLED` is on.

## Files

| File | Purpose |
|---|---|
| `config.py` | env reading; every accessor is total |
| `identity.py` | (channel, external id) → user_id / session_id / workspace |
| `core.py` | `ChannelCore` — drives the pipeline, acks, pushes replies, resumes pauses |
| `ingest.py` | structured fields + document uploads → workspace evidence |
| `telegram.py` | Bot API long polling (httpx, no extra dependency) |
| `whatsapp.py` | allowlist + client for the Node bridge (`/health`, `/qr`, `/send`) |
| `web.py` | the console page, intake/progress/download endpoints |
| `admin.py` | the Connectors tab API: channel status, saving settings, the pairing QR |
| `static/chat.html` | the console (Discover · Preview · Connectors tabs) |
| `app.py` | the FastAPI service |
| `whatsapp_bridge/` | Node Baileys sidecar |

## Behaviour worth knowing

- **Discovery then generation.** A new conversation is a chat; the pipeline only
  produces documents when you ask (`generate my BRD`).
- **Discover tab (no ADK).** The console's entry point is a standalone BA
  interview (`my_agents/ba_chat/` — plain FastAPI + LiteLLM, zero ADK imports):
  it elicits, confirms and signs off requirements, draws the live coverage
  diagram, and builds the signed-off scope straight into Frappe. See
  `my_agents/ba_chat/README.md`.
- **Long turns.** `submit` acknowledges first, then pushes the reply when the
  turn finishes — Telegram/WhatsApp are never blocked by a multi-minute run.
- **Human pauses resume.** When the pipeline waits for a human, the next message
  in that conversation is delivered as the answer (the same protocol `adk web`
  uses), not as new text.
- **Uploads.** `.txt .md .csv .json .log .yaml` decode directly; **PDF** and
  **Word (.docx)** are extracted with `pypdf` / `python-docx`
  (`uv pip install --python .venv/bin/python pypdf python-docx`). A scanned PDF has
  no text layer, so it is refused with that reason rather than ingested as an empty
  document; legacy `.doc` / `.ppt` / `.xls` are refused too — save them as
  `.docx` or PDF first. Every upload is stored as `artifacts/uploaded_*`, named in
  the handoff brief so the agent knows to read it, and citable as BA evidence
  (`link_evidence(source_type="upload", source_ref="<file>")`).

## Caveats

- **WhatsApp via Baileys is unofficial.** There is a small account-restriction
  risk and WhatsApp protocol updates can break the bridge. Use a dedicated
  number. The official Cloud API needs a Meta business account and a public
  webhook — not worth it for a demo.
- The connector service keeps its **own** session database; the project
  workspace on disk is what stays shared with `adk web`.

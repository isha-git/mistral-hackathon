# WhatsApp + OpenCode Coding Agent

A WhatsApp bridge that lets you chat with OpenCode to write code. All projects persist on your local filesystem.
The idea is that this can run on a homelab at home to help you code. Long running agents that can help you maintain, contribute or review the repos you add it to. You control it through whatsapp, voice or text whichever you prefer. It uses ElevenLabs for speech-to-text and OpenCode for coding agents.

## How It Works

**One WhatsApp number = One persistent coding project**

- Send any message → OpenCode creates/modifies code in your project
- Files saved to `./vibe_repos/` (visible on your host machine)
- Session history tracked automatically via OpenCode sessions
- Type `/new_project` to start fresh

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Edit .env and add your OPENCODE_PROVIDER_ID, OPENCODE_MODEL_ID, and provider API keys

# 2. Start everything
docker compose up --build

# 3. Scan QR code with bot's phone
# WhatsApp > Settings > Linked Devices > Link a Device
```

## Usage

Send messages from **your personal WhatsApp** to the bot's number:

| Message | What Happens |
|---------|--------------|
| `create a fastapi app` | OpenCode creates `main.py`, `requirements.txt` |
| `add a /users endpoint` | OpenCode adds endpoint to existing `main.py` |
| `run uvicorn and test it` | OpenCode starts server, sends curl requests |
| `/new_project` | Clears history, starts fresh project |

## Project Structure

```
vibe_repos/
└── user-<phone_number>/          # Your project directory
    ├── main.py                   # Code files
    ├── requirements.txt
    └── calculator.py
```

**Files are visible on your host machine** at `./vibe_repos/`

## Session Continuity

OpenCode sessions are managed via the SDK:

1. **First message** → Creates a new OpenCode session for the user
2. **Follow-up messages** → Reuses the same session, maintaining full conversation history
3. **Working directory** reused → Code files persist across runs
4. **`/new_project`** → Creates a fresh session and working directory

## Architecture

```
                              Docker Compose
                 ┌──────────────────────────────────────────┐
                 │                                          │
 ┌───────────┐   │  ┌──────────┐  POST /webhook  ┌───────┐  │
 │  User's   │───┼─>│ WhatsApp │ ──────────────> │  API  │  │
 │  Phone    │   │  │  Bridge  │                 │(Fast- │  │
 │ (WhatsApp)│<──┼──│  (Node)  │ <────────────── │ API)  │  │
 └───────────┘   │  └──────────┘   webhook/ack   └───┬───┘  │
                 │                                    │      │
                 │                              enqueue job  │
                 │                                    │      │
                 │                                    ▼      │
                 │  ┌──────────┐               ┌──────────┐  │
                 │  │  Redis   │<──────────────│  Celery   │  │
                 │  │  (state) │               │  Worker   │  │
                 │  └──────────┘               └────┬─────┘  │
                 │                                  │        │
                 │  ┌──────────┐                    │        │
                 │  │ OpenCode │<───────────────────┘        │
                 │  │  Server  │  (SDK calls via HTTP)       │
                 │  └──────────┘                             │
                 └───────────────────────────────────────────┘
```

The bridge receives WhatsApp messages via a persistent connection and forwards them to the API over HTTP. Celery workers call OpenCode via the Python SDK to execute coding tasks asynchronously.

## Testing

```bash
# Check API health
curl http://localhost:8000/health

# List all jobs
curl http://localhost:8000/tasks \
  -H "Authorization: Bearer <API_KEY>"

# Check specific job
curl http://localhost:8000/tasks/<job_id> \
  -H "Authorization: Bearer <API_KEY>"
```

## Commands

- **Any text** → OpenCode will create/modify code based on your request
- **`/new_project`** → Start fresh (clears conversation history)

## Key Features

✅ **Persistent Projects** - One WhatsApp number = one project
✅ **Host Access** - Files saved to `./vibe_repos/` on your machine
✅ **Session Continuity** - OpenCode sees full conversation history
✅ **Configurable LLM** - Use any provider/model supported by OpenCode
✅ **Git Integration** - Each working dir is a git repo

## Logs

```bash
docker compose logs -f              # All services
docker compose logs -f whatsapp   # WhatsApp bridge only
docker compose logs -f worker     # Celery worker only
docker compose logs -f api        # API only
docker compose logs -f opencode   # OpenCode server only
```

## Troubleshooting

**Permission denied on vibe_repos files:**
```bash
# Files created as root in container
sudo chown -R $USER:$USER ./vibe_repos/
```

**WhatsApp not connecting:**
```bash
# Force re-pairing
docker compose down -v
docker compose up --build
```

**Restart with fresh state:**
```bash
docker compose down -v
docker compose up --build
```

## Implementation Notes

- Connects to `opencode serve` via REST API for interactive coding sessions
- OpenCode sessions handle conversation history automatically
- Permission requests from the agent can be relayed to the user via WhatsApp
- Working directory determined by sender ID: `vibe_repos/user-<sender>/`
- Celery handles async job processing
- Redis for job queue, state persistence, and OpenCode session ID mapping

## Project Structure

```
docker-compose.yml          — orchestrates all services

src/whatsapp/               — WhatsApp bridge (Node/TypeScript)
  index.ts                  — entry point, wires socket + message loop
  connection.ts             — Baileys socket, QR display, credential persistence
  message.ts                — parses incoming messages into structured format
  media.ts                  — download/send media (audio, images, text)
  handler.ts                — forwards messages to the API, parses replies
  Dockerfile

src/api/                    — API service (Python/FastAPI)
  main.py                   — FastAPI app, mounts routes
  models.py                 — request/response schemas (Pydantic)
  routes/
    webhook.py              — POST /webhook — receives messages, returns replies
    health.py               — GET /health
  services/
    pipeline.py             — orchestrates message routing to OpenCode jobs
    opencode_wrapper.py     — OpenCode REST API client
    elevenlabs/
      client.py             — shared ElevenLabs API client
      stt.py                — speech-to-text (voice → text)
      tts.py                — text-to-speech (placeholder)
  Dockerfile
```

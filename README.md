# WhatsApp + Mistral Vibe Coding Agent

A WhatsApp bridge that lets you chat with Mistral Vibe to write code. All projects persist on your local filesystem.

## How It Works

**One WhatsApp number = One persistent coding project**

- Send any message → Vibe creates/modifies code in your project
- Files saved to `./vibe_repos/` (visible on your host machine)
- Session history tracked automatically via vibe's sessions
- Type `/new_project` to start fresh

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Edit .env and add your MISTRAL_VIBE_API_KEY

# 2. Start everything
docker compose up --build

# 3. Scan QR code with bot's phone
# WhatsApp > Settings > Linked Devices > Link a Device
```

## Usage

Send messages from **your personal WhatsApp** to the bot's number:

| Message | What Happens |
|---------|--------------|
| `create a fastapi app` | Vibe creates `main.py`, `requirements.txt` |
| `add a /users endpoint` | Vibe adds endpoint to existing `main.py` |
| `run uvicorn and test it` | Vibe starts server, sends curl requests |
| `/new_project` | Clears history, starts fresh project |

## Project Structure

```
vibe_repos/
└── user-<phone_number>/          # Your project directory
    ├── main.py                   # Code files
    ├── requirements.txt
    ├── calculator.py
    └── sessions/                 # Vibe conversation history
        └── session_20260228.../  # Each run gets session dir
            ├── messages.jsonl    # Full conversation
            └── meta.json         # Session metadata
```

**Files are visible on your host machine** at `./vibe_repos/`

## Session Continuity

We follow vibe's session design:

1. **First message** → Creates `sessions/session_<timestamp>/`
2. **Follow-up messages** → Load previous session messages, continue conversation
3. **New session dir** created for each run (vibe's telemetry)
4. **Working directory** reused → Code files persist across runs

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
                 └──────────────────────────────────┼────────┘
                                                    │
                                          runs: vibe --prompt
                                                    │
                                                    ▼
                                             ┌─────────────┐
                                             │ Mistral Vibe │
                                             │   CLI        │
                                             │              │
                                             │ • clones repo│
                                             │ • writes code│
                                             │ • commits    │
                                             │ • pushes     │
                                             │ • creates PR │
                                             └─────────────┘
```

The bridge receives WhatsApp messages via a persistent connection and forwards them to the API over HTTP. Celery workers run Mistral Vibe to execute coding tasks asynchronously.

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

- **Any text** → Vibe will create/modify code based on your request
- **`/new_project`** → Start fresh (clears conversation history)

## Key Features

✅ **Persistent Projects** - One WhatsApp number = one project  
✅ **Host Access** - Files saved to `./vibe_repos/` on your machine  
✅ **Session Continuity** - Vibe sees full conversation history  
✅ **Git Integration** - Each working dir is a git repo  
✅ **No Subprocess** - Uses vibe's direct Python API  

## Logs

```bash
docker compose logs -f              # All services
docker compose logs -f whatsapp   # WhatsApp bridge only
docker compose logs -f worker     # Vibe worker only
docker compose logs -f api        # API only
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

- Uses `vibe.core.programmatic.run_programmatic()` for non-interactive execution
- Loads previous messages from `sessions/session_*/messages.jsonl`
- Working directory determined by sender ID: `vibe_repos/user-<sender>/`
- Celery handles async job processing
- Redis for job queue and state persistence

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
    pipeline.py             — orchestrates: STT → Mistral (later) → TTS (later)
    elevenlabs/
      client.py             — shared ElevenLabs API client
      stt.py                — speech-to-text (voice → text)
      tts.py                — text-to-speech (placeholder)
  Dockerfile
```


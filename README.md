# VibeBunny

A WhatsApp bridge that lets you chat with [OpenCode](https://opencode.ai) to write code. Send a message, get working code back — all projects persist on your local filesystem.

Designed to run on a homelab. Long-running coding agents you control through WhatsApp, voice or text. Uses OpenCode for coding agents with any LLM provider.

## How It Works

**One WhatsApp number = One persistent coding project**

- Send any message → OpenCode creates/modifies code in your project
- Files saved to `./vibe_repos/` (visible on your host machine)
- Session history tracked automatically via OpenCode sessions
- Type `/new_project` to start fresh

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)
- A WhatsApp account for the bot (separate from your personal account)
- An API key for your chosen LLM provider (Anthropic, OpenAI, OpenRouter, etc.)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/isha-git/VibeBunny.git
cd VibeBunny

# 2. Configure environment
cp .env.example .env
# Edit .env — set API_KEY, WEBHOOK_SECRET, your LLM provider key,
# and OPENCODE_PROVIDER_ID / OPENCODE_MODEL_ID

# 3. Start all services
docker compose up --build

# 4. Scan the QR code that appears in the terminal
# On the bot's phone: WhatsApp > Settings > Linked Devices > Link a Device
```

Once linked, send a message from your personal WhatsApp to the bot's number and start coding.

## Usage

Send messages from **your personal WhatsApp** to the bot's number:

| Message | What Happens |
|---------|--------------|
| `create a fastapi app` | OpenCode creates project files |
| `add a /users endpoint` | OpenCode modifies existing code |
| `fix the bug in main.py` | OpenCode reads, diagnoses, and patches |
| `/new_project` | Clears history, starts a fresh project |

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
                 │  │  Server  │  (REST API calls)           │
                 │  └──────────┘                             │
                 └───────────────────────────────────────────┘
```

The WhatsApp bridge maintains a persistent connection to WhatsApp and forwards messages to the FastAPI backend over HTTP. The API enqueues jobs in Celery, which calls OpenCode's REST API to execute coding tasks asynchronously. Redis stores job state and session mappings.

## Project Structure

```
VibeBunny/
├── docker-compose.yml              # Orchestrates all services
├── Dockerfile                      # API + worker image
├── pyproject.toml                  # Python dependencies
├── opencode.json                   # OpenCode configuration
│
├── src/api/                        # Python/FastAPI backend
│   ├── main.py                     # App entry point, middleware
│   ├── exceptions.py               # Custom exception hierarchy
│   ├── config/
│   │   ├── settings.py             # Pydantic settings from env
│   │   ├── redis.py                # Redis client factory
│   │   └── celery.py               # Celery app configuration
│   ├── models/
│   │   └── job.py                  # Pydantic request/response schemas
│   ├── routes/
│   │   ├── webhook.py              # POST /webhook — receives WhatsApp messages
│   │   ├── tasks.py                # CRUD for coding tasks
│   │   └── health.py               # Health, readiness, liveness checks
│   ├── services/
│   │   ├── pipeline.py             # Message routing and job orchestration
│   │   ├── opencode_wrapper.py     # OpenCode REST API client
│   │   ├── job_service.py          # Redis-backed job persistence
│   │   ├── notifier.py             # Notification interface
│   │   ├── whatsapp_notifier.py    # WhatsApp message delivery
│   │   ├── file_delivery.py        # Safe file extraction and sending
│   │   ├── file_detector.py        # Detect changed files in working dir
│   │   ├── code_extractor.py       # Extract code blocks from responses
│   │   ├── question_extractor.py   # Detect agent questions for user
│   │   └── circuit_breaker.py      # Circuit breaker for external calls
│   └── tasks/
│       └── jobs.py                 # Celery task definitions
│
├── src/whatsapp/                   # Node.js/TypeScript WhatsApp bridge
│   ├── index.ts                    # Entry point, HTTP server
│   ├── connection.ts               # Baileys socket, QR pairing, credentials
│   ├── message.ts                  # Parse incoming messages
│   ├── media.ts                    # Media download/upload
│   ├── handler.ts                  # Forward messages to API
│   └── Dockerfile
│
└── tests/                          # Pytest test suite
    ├── conftest.py                 # Shared fixtures (fakeredis, settings)
    ├── test_job_service.py
    ├── test_pipeline.py
    ├── test_circuit_breaker.py
    ├── test_code_extractor.py
    ├── test_question_extractor.py
    ├── test_file_detector.py
    ├── test_webhook.py
    └── test_settings.py
```

## Configuration

All configuration is done through environment variables. See [`.env.example`](.env.example) for the full list.

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | Yes | API key for authenticating requests to the task endpoints |
| `WEBHOOK_SECRET` | Yes | Shared secret for WhatsApp bridge → API authentication |
| `OPENCODE_PROVIDER_ID` | Yes | LLM provider (`anthropic`, `openai`, `openrouter`, etc.) |
| `OPENCODE_MODEL_ID` | Yes | Model to use (e.g. `claude-sonnet-4-20250514`) |
| Provider API key | Yes | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. depending on provider |
| `GITHUB_BOT_TOKEN` | No | For push/PR creation via a bot account |

## API Endpoints

All `/tasks` endpoints require authentication via `Authorization: Bearer <API_KEY>` or `X-API-Key` header.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Full health check (API + Redis + Celery) |
| `GET` | `/health/ready` | Readiness probe |
| `GET` | `/health/live` | Liveness probe |
| `POST` | `/webhook` | Receive messages from WhatsApp bridge |
| `POST` | `/tasks` | Create a new coding task |
| `GET` | `/tasks` | List all tasks (paginated) |
| `GET` | `/tasks/{job_id}` | Get task status and details |
| `POST` | `/tasks/{job_id}/continue` | Send user response to a waiting task |
| `GET` | `/tasks/session/{session_id}` | Get all tasks in a session |

When running in debug mode (`DEBUG=true`), interactive API docs are available at `/docs`.

## Development

### Running Tests

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src
```

### Local Development (without Docker)

```bash
# Install dependencies
uv sync

# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Start the API
uv run uvicorn src.api.main:app --reload --port 8000

# Start a Celery worker
uv run celery -A src.api.config.celery worker --loglevel=info

# Start the WhatsApp bridge (in src/whatsapp/)
cd src/whatsapp && npm install && npm start
```

## Security

- API and WhatsApp bridge only bind to `127.0.0.1` — not exposed to the internet by default
- API container runs as non-root user with `no-new-privileges` and a read-only filesystem
- Webhook requests authenticated via HMAC-SHA1 (`X-Webhook-Secret`)
- Task endpoints authenticated via API key with constant-time comparison
- Rate limiting (sliding window, configurable) on all endpoints
- Path traversal protection on file delivery
- See [SECURITY.md](SECURITY.md) for vulnerability disclosure policy

## Logs

```bash
docker compose logs -f              # All services
docker compose logs -f whatsapp     # WhatsApp bridge only
docker compose logs -f worker       # Celery worker only
docker compose logs -f api          # API only
docker compose logs -f opencode     # OpenCode server only
```

## Troubleshooting

**WhatsApp not connecting:**
```bash
# Force re-pairing by clearing auth data
docker compose down -v
docker compose up --build
```

**Permission denied on vibe_repos files:**
```bash
# Container runs as UID 1000 — match ownership on host
sudo chown -R 1000:1000 ./vibe_repos/
```

**Celery worker not processing jobs:**
```bash
# Check worker is running and connected to Redis
docker compose logs worker
# Verify Redis is healthy
docker compose exec redis redis-cli ping
```

**OpenCode not responding:**
```bash
# Check OpenCode health
curl http://localhost:4096/global/health
# Check logs for configuration issues
docker compose logs opencode
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

[MIT](LICENSE)

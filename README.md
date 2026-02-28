# mistral-hackathon

Mistral hackathon 2026 — WhatsApp bridge that forwards messages to an API and sends responses back.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed and running
- A spare phone number with WhatsApp (this becomes the bot's number)
- An [ElevenLabs API key](https://elevenlabs.io/app/settings/api-keys)

## Quick Start

```bash
# Set up your environment
cp .env.example .env
# Edit .env and add your ELEVENLABS_API_KEY

# Build and start everything (API + WhatsApp bridge)
docker compose up --build
```

A QR code will appear in the terminal. On the **bot's phone**:

1. Open WhatsApp > Settings > Linked Devices > Link a Device
2. Scan the QR code

The terminal will log `Connected!` once paired. Credentials are saved — you won't need to scan again on restart.

## Architecture

```
                         Docker Compose
                 ┌──────────────────────────────┐
                 │                               │
 ┌───────────┐   │  ┌──────────┐   ┌──────────┐  │
 │  User's   │───┼─>│ WhatsApp │──>│   API    │  │
 │  Phone    │   │  │  Bridge  │   │ (FastAPI) │  │
 │ (WhatsApp)│<──┼──│  (Node)  │<──│          │  │
 └───────────┘   │  └──────────┘   └────┬─────┘  │
                 │       │              │         │
                 │       v              v         │
                 │       │              │         │
                 │       v              v         │
                 │  ┌──────────┐  ┌───────────┐  │
                 │  │   Auth   │  │ ElevenLabs │  │
                 │  │  Volume  │  │ STT / TTS  │  │
                 │  └──────────┘  └───────────┘  │
                 └──────────────────────────────┘

 ── text / voice / image ──>     ── POST /webhook ──>
 <── reply (text/audio/image) ── <── JSON response ──
```

The bridge receives WhatsApp messages via a persistent connection and forwards them to the API over HTTP. The API processes the message and returns a reply. Both services run in Docker on the same network.

## Testing

Send messages **from your personal WhatsApp** to the bot's phone number:

| You send | Bot replies |
|---|---|
| `hello` (text) | `hello` (echo, Mistral integration later) |
| A voice note | Transcribed text (via ElevenLabs STT) |
| An image with caption "test" | `test` (echo for now) |

You can also test the API directly:

```bash
# Health check
curl http://localhost:8000/health

# Send a test message
curl -X POST http://localhost:8000/webhook \
  -H 'Content-Type: application/json' \
  -d '{"sender":"test","type":"text","text":"hello"}'
```

## Stopping and Restarting

```bash
# Stop
docker compose down

# Restart (no QR scan needed — credentials persist in Docker volume)
docker compose up

# Force a fresh QR pairing
docker compose down -v
docker compose up --build
```

## Logs

```bash
docker compose logs -f                # all services
docker compose logs -f whatsapp-bridge # bridge only
docker compose logs -f api             # API only
```

## Project Structure

```
docker-compose.yml          — orchestrates both services

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

## Next Steps

1. Add Mistral integration in `src/api/services/mistral.py` — send transcribed text, get a response
2. Add ElevenLabs TTS in `src/api/services/elevenlabs/tts.py` — convert Mistral's response to audio
3. Wire both into `src/api/services/pipeline.py` to complete the chain: voice → text → Mistral → audio → WhatsApp

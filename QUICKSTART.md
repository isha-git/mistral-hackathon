# Quick Start

Get the WhatsApp-to-Mistral Vibe API running in 5 minutes.

## Prerequisites

- Python 3.12+
- Docker & Docker Compose
- Mistral API key ([get one here](https://console.mistral.ai/))
- Vibe CLI installed (`pip install mistral-vibe`)

## 1. Clone & Configure

```bash
git clone <your-repo>
cd mistral-hackathon

# Copy environment template
cp .env.example .env

# Edit with your keys
nano .env
```

Required in `.env`:
```bash
API_KEY=your-secure-api-key-here
MISTRAL_VIBE_API_KEY=your-mistral-api-key-here
```

## 2. Start Services

```bash
# Start Redis, API, and Celery worker
docker-compose up -d

# Check health
curl http://localhost:8000/health
```

## 3. Create Your First Task

```bash
# Submit a coding task
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "prompt": "Add a README with project description",
    "repo_url": "https://github.com/yourusername/yourrepo.git",
    "branch_name": "feature-readme"
  }'

# Response: {"id": "uuid-here", "status": "pending", ...}
```

## 4. Check Status

```bash
# Get job status
curl http://localhost:8000/tasks/{job_id} \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## 5. Answer Questions

If the agent needs clarification, status will be `waiting_for_input`:

```bash
# Continue with your answer
curl -X POST http://localhost:8000/tasks/{job_id}/continue \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"response": "Use Python with FastAPI framework"}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tasks` | POST | Create new coding task |
| `/tasks` | GET | List all tasks (paginated) |
| `/tasks/{id}` | GET | Get task status & details |
| `/tasks/{id}/continue` | POST | Answer agent question |
| `/session/{id}` | GET | Get all jobs in session |
| `/health` | GET | Full health check |
| `/health/live` | GET | Liveness check |

## Stateful Sessions

Jobs are **stateful** - they share working directories by repo/branch:

```bash
# Job 1: Create README
curl -X POST http://localhost:8000/tasks \
  -d '{"prompt": "Create README", "repo_url": "...", "branch_name": "feature-x"}'

# Job 2: Add to same branch (same working directory!)
curl -X POST http://localhost:8000/tasks \
  -d '{"prompt": "Add installation section to README", "repo_url": "...", "branch_name": "feature-x"}'
```

## Webhook Notifications

Provide a webhook URL to receive status updates:

```bash
curl -X POST http://localhost:8000/tasks \
  -d '{
    "prompt": "Add authentication",
    "repo_url": "...",
    "branch_name": "feature-auth",
    "webhook_url": "https://your-service.com/webhook"
  }'
```

Webhook payload when agent asks question:
```json
{
  "status": "needs_input",
  "question": "What auth method should I use?",
  "job_id": "uuid",
  "working_dir": "/path/to/work",
  "branch_name": "feature-auth"
}
```

## Local Development

Without Docker:

```bash
# Terminal 1: Redis
docker run -p 6379:6379 redis:7-alpine

# Terminal 2: API
uvicorn src.api.main:app --reload

# Terminal 3: Celery Worker
celery -A src.api.config.celery worker --loglevel=info
```

## Working Directories

Files are stored in:
```
./vibe_repos/{repo-name}_{branch-name}/
```

Example:
```
./vibe_repos/Hello-World_feature-readme/
  └── Hello-World/
      ├── README.md
      └── ...
```

## Common Issues

**"API key required"** - Add `Authorization: Bearer YOUR_API_KEY` header

**"Job stuck in pending"** - Celery worker not running. Start with `docker-compose up -d`

**"Redis connection refused"** - Redis not running. Check with `docker ps`

## Next Steps

- See [DEPLOYMENT.md](DEPLOYMENT.md) for production deployment
- See [SECURITY.md](SECURITY.md) for security considerations
- WhatsApp integration: POST to `/tasks` from your WhatsApp service

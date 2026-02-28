# Deployment Guide

This guide covers deploying the Mistral Vibe API to a Digital Ocean droplet using Docker Compose.

## Prerequisites

- Digital Ocean account
- Docker and Docker Compose installed on your droplet
- Domain name (optional, but recommended)
- API keys for:
  - Mistral Vibe CLI

## Environment Variables

Create a `.env` file with the following variables:

```bash
# Required
API_KEY=your_secure_api_key_here
MISTRAL_VIBE_API_KEY=your_mistral_vibe_key_here

# Mistral Vibe CLI Configuration
MISTRAL_VIBE_BASE_URL=http://host.docker.internal:8080  # If running CLI locally
# Or use the actual CLI service URL if external

# Optional (have sensible defaults)
HOST=0.0.0.0
PORT=8000
DEBUG=false
REDIS_URL=redis://redis:6379/0
REDIS_JOB_TTL=86400
JOB_MAX_TIMEOUT=1800
JOB_RETRY_COUNT=3
JOB_RETRY_DELAY=5
```

## Deployment Steps

### 1. Provision Droplet

Create a Digital Ocean droplet with:
- **OS**: Ubuntu 22.04 LTS
- **Plan**: Basic (at least 2GB RAM, 1 CPU)
- **Region**: Closest to your users
- **SSH Keys**: Add your SSH key

### 2. Install Docker

SSH into your droplet and run:

```bash
# Update package index
sudo apt-get update

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 3. Deploy Application

```bash
# Clone your repository
git clone <your-repo-url>
cd mistral-hackathon

# Create environment file
cp .env.example .env
# Edit .env with your actual values
nano .env

# Build and start services
docker-compose up -d --build

# Check logs
docker-compose logs -f
```

### 4. Verify Deployment

```bash
# Health check
curl http://localhost:8000/health

# Test creating a task
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"prompt": "Create a Python function to calculate fibonacci"}'
```

### 5. Set Up Nginx (Optional, for domain + SSL)

If using a domain:

```bash
# Install Nginx
sudo apt-get install nginx

# Install Certbot for SSL
sudo apt-get install certbot python3-certbot-nginx

# Get SSL certificate
sudo certbot --nginx -d your-domain.com
```

Nginx configuration (`/etc/nginx/sites-available/vibe-api`):

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/vibe-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Webhook Integration

For bidirectional communication (agent → user):

1. **Configure webhook URL**: When creating a task, include `webhook_url`:
   ```json
   {
     "prompt": "Create a Python API",
     "webhook_url": "https://your-whatsapp-service.com/webhook"
   }
   ```

2. **Webhook payload** when agent needs input:
   ```json
   {
     "status": "needs_input",
     "job_id": "uuid-here",
     "question": "What endpoints should the API have?",
     "conversation": [...]
   }
   ```

3. **Continue task** with user response:
   ```bash
   curl -X POST http://localhost:8000/tasks/{job_id}/continue \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -d '{"response": "Users and Posts endpoints"}'
   ```

## Monitoring

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f api
docker-compose logs -f worker
docker-compose logs -f redis
```

### Health Endpoints

- `/health` - Full health check (Redis + Celery)
- `/health/ready` - Readiness check
- `/health/live` - Liveness check

### Restart Services

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart worker

# Rebuild and restart
docker-compose up -d --build
```

## Troubleshooting

### Redis Connection Issues

```bash
# Check Redis is running
docker-compose ps redis

# Test Redis connection
docker-compose exec redis redis-cli ping
```

### Worker Not Processing Jobs

```bash
# Check worker logs
docker-compose logs worker

# Restart worker
docker-compose restart worker
```

### API Not Responding

```bash
# Check if containers are running
docker-compose ps

# Check API logs
docker-compose logs api

# Restart API
docker-compose restart api
```

## Updates

To update the application:

```bash
# Pull latest code
git pull origin main

# Rebuild and restart
docker-compose up -d --build

# Verify
docker-compose ps
```

## Backup

Backup Redis data:

```bash
# Create backup
docker-compose exec redis redis-cli SAVE
docker cp $(docker-compose ps -q redis):/data/dump.rdb ./backup-$(date +%Y%m%d).rdb
```

## Security Considerations

1. **API Key**: Use a strong, random API key
2. **Firewall**: Only expose port 80/443, not 8000 directly
3. **Updates**: Keep Docker and base images updated
4. **SSL**: Always use HTTPS in production
5. **Redis**: Not exposed externally by default (good)

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Client    │────▶│   Nginx     │────▶│   FastAPI   │
│  (WhatsApp  │     │   (SSL)     │     │   :8000     │
│   service)  │◀────│             │◀────│             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                                        ┌──────▼──────┐
                                        │    Redis    │
                                        │   :6379     │
                                        └──────┬──────┘
                                               │
                                        ┌──────▼──────┐
                                        │   Celery    │
                                        │   Worker    │
                                        └──────┬──────┘
                                               │
                                        ┌──────▼──────┐
                                        │   Mistral   │
                                        │   Vibe CLI  │
                                        └─────────────┘
```

The WhatsApp service (handled by another developer) will:
1. Receive messages from users via Baileys
2. POST to `/tasks` to create jobs
3. Receive webhooks when agents need input
4. Send responses back via `/tasks/{id}/continue`

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2025-03-14

### Added
- WhatsApp integration via Baileys for text-based coding through chat
- OpenCode agent backend with session persistence
- Celery-based async job processing with Redis
- Multi-turn conversation support with agent question detection
- File change detection and WhatsApp document delivery
- Docker Compose orchestration for all services
- API key authentication for task endpoints
- Webhook secret authentication for WhatsApp bridge
- Health check endpoints (readiness + liveness probes)
- Rate limiting with Redis sliding window

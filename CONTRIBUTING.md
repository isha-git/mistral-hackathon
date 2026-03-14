# Contributing to VibeBunny

Thank you for your interest in contributing! Here's how to get started.

## Development Setup

1. **Prerequisites**: Python 3.12+, Node.js 18+, Docker & Docker Compose
2. **Clone** the repo and copy `.env.example` to `.env`, filling in your API keys
3. **Start services**: `make dev` (or `docker compose up --build`)
4. **Run tests**: `make test`
5. **Lint**: `make lint`

## Making Changes

1. Fork the repo and create a feature branch from `main`
2. Make your changes, following existing code conventions
3. Add tests for new functionality
4. Run `make lint` and `make test` to verify
5. Submit a pull request with a clear description

## Code Style

- **Python**: Formatted with [Ruff](https://docs.astral.sh/ruff/). Run `ruff check src/` and `ruff format src/`.
- **TypeScript**: Follow existing patterns in `src/whatsapp/`.
- Keep functions focused and small. Prefer extracting helpers over long functions.
- Use type hints in Python code.

## Reporting Issues

- Use [GitHub Issues](../../issues) to report bugs or request features.
- Include steps to reproduce for bugs.
- Check existing issues before creating a new one.

## Pull Request Guidelines

- Keep PRs focused on a single change
- Include a clear description of what and why
- Reference related issues with `Fixes #123` or `Closes #123`
- Ensure CI passes before requesting review

# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT** open a public GitHub issue for security vulnerabilities.
2. Email the maintainers with details of the vulnerability.
3. Include steps to reproduce if possible.
4. Allow reasonable time for a fix before public disclosure.

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Security Best Practices for Deployment

- Never commit `.env` files or API keys to version control
- Use strong, unique values for `API_KEY` and `WEBHOOK_SECRET`
- Run behind a reverse proxy (nginx) in production
- Keep Docker images updated
- Restrict network access: API and WhatsApp bridge should only be accessible internally

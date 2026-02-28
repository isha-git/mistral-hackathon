# Security Guide

## Home Server Security

### Current Configuration

The provided `docker-compose.yml` is configured for **safe home server deployment**:

- ✅ **Redis not exposed** - Internal Docker network only
- ✅ **API binds to localhost** - `127.0.0.1:8000` only, not accessible from internet
- ✅ **Non-root containers** - Services run as unprivileged users
- ✅ **Read-only filesystem** - API container filesystem is read-only
- ✅ **Rate limiting** - 30 requests per minute per IP
- ✅ **API key authentication** - Required for all endpoints
- ✅ **Restricted CORS** - No wildcard origins in production

### What's Protected

| Component | Exposure | Risk Level |
|-----------|----------|------------|
| Redis | Internal only | ✅ None |
| FastAPI | localhost only | ✅ Low |
| API Keys | Required | ✅ Low |
| Celery Worker | Internal only | ✅ None |

### Accessing from Outside

If you need external access, you have **three secure options**:

#### Option 1: SSH Tunnel (Recommended for personal use)

```bash
# From your local machine, tunnel to home server
ssh -L 8000:localhost:8000 user@your-home-server-ip

# Now access via localhost:8000 on your machine
# No ports exposed to internet!
```

#### Option 2: VPN

Run the API on your home server, access via VPN (WireGuard, Tailscale, etc.)

#### Option 3: Reverse Proxy with SSL (If exposing to internet)

```yaml
# docker-compose.override.yml
services:
  api:
    ports:
      - "8000:8000"  # Exposes to internet - ONLY with nginx + SSL
```

**Requirements for internet exposure:**
1. Use nginx with SSL (Let's Encrypt)
2. Strong firewall rules (only 443 open)
3. Strong API key (32+ chars, random)
4. Consider adding Cloudflare

### Firewall Setup

If exposing to internet, use UFW (Ubuntu):

```bash
# Install and enable
sudo apt-get install ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (don't lock yourself out!)
sudo ufw allow 22/tcp

# If using nginx reverse proxy:
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Block direct access to API
# (already done by binding to 127.0.0.1 in docker-compose)

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status verbose
```

### API Key Security

**Generate a strong key:**
```bash
openssl rand -hex 32
```

**Environment file permissions:**
```bash
chmod 600 .env
```

### Testing Security

1. **Verify localhost-only binding:**
```bash
# From home server - should work
curl http://localhost:8000/health

# From another machine - should FAIL (connection refused)
curl http://your-server-ip:8000/health
```

2. **Verify API key required:**
```bash
# Should fail - no API key
curl http://localhost:8000/tasks

# Should work - with API key
curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:8000/tasks
```

3. **Verify Redis not exposed:**
```bash
# From another machine - should fail
telnet your-server-ip 6379
```

### Security Checklist

Before exposing to internet:

- [ ] API key is 32+ random characters
- [ ] .env file has 600 permissions
- [ ] Using HTTPS (not HTTP)
- [ ] Firewall blocks port 8000 from outside
- [ ] Only ports 80/443 (or VPN) open
- [ ] Redis password set (optional, since internal only)
- [ ] Logging enabled to monitor access
- [ ] Fail2ban installed (optional)

### Logs and Monitoring

```bash
# Monitor API access logs
docker-compose logs -f api | grep -E "(POST|GET|401|403)"

# Check for failed auth attempts
docker-compose logs api | grep "401"
```

### Incident Response

If you suspect compromise:

1. **Immediately revoke API key:**
   ```bash
   # Generate new key
   openssl rand -hex 32
   
   # Update .env
   nano .env
   
   # Restart services
   docker-compose restart
   ```

2. **Check logs:**
   ```bash
   docker-compose logs api | tail -100
   ```

3. **Review active jobs:**
   ```bash
   curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:8000/tasks
   ```

### Network Architecture

**Home Server (Safe):**
```
Internet → ❌ (blocked)
SSH/VPN  → ✅ (allowed)
Localhost → ✅ API (:8000)
Internal → ✅ Redis (:6379)
```

**With Reverse Proxy (If needed):**
```
Internet → 443 → Nginx → 127.0.0.1:8000 → API
         → 80  → (redirect to 443)
         → ❌ 8000 (blocked by firewall)
```

## Summary

**Current setup is SAFE for home server** because:
1. API only listens on localhost
2. Redis is internal-only
3. Requires API key
4. Rate limiting enabled
5. Non-root containers

**To access remotely:**
- Use SSH tunnel (safest)
- Or use VPN
- Or nginx + SSL (if you must expose)

**Never expose port 8000 directly to the internet.**

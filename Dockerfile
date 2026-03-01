FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first
COPY pyproject.toml ./

# Install Python dependencies as root (needed for compilation)
RUN pip install --no-cache-dir uv
RUN pip install --no-cache-dir -e .

# Copy application code
COPY src/ ./src/

# Create directories for persistence
RUN mkdir -p /app/vibe_repos /app/.vibe

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Configure git identity for agent auto-commits (safe_write_file)
RUN git config --global user.name "Bunny Bot" && \
    git config --global user.email "bunny@bot.local"

# Set environment variables
ENV VIBE_HOME=/app/.vibe

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health/live')" || exit 1

# Run the application
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

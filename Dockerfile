FROM python:3.13-slim AS builder

# Install build dependencies for compiling packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . .

# Install uv and use it to install dependencies from pyproject.toml/uv.lock
RUN pip install --no-cache-dir uv
ENV PATH="/app/.venv/bin:${PATH}"
RUN uv sync --no-dev

FROM python:3.13-slim

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}"

# Copy venv and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/scripts /app/scripts

EXPOSE 9001

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9001/health || exit 1

CMD ["python", "src/server.py", "--host", "0.0.0.0", "--port", "9001", "--transport", "sse"]

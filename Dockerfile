FROM python:3.13-slim AS builder

# Install build dependencies for compiling packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . .

# Create virtual environment and install dependencies using pip
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# Upgrade pip and install project dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir \
    asyncmy>=0.2.10 \
    fastmcp[websockets]==2.12.1 \
    google-genai>=1.15.0 \
    openai>=1.78.1 \
    python-dotenv>=1.1.0 \
    sentence-transformers>=4.1.0 \
    tokenizers==0.21.2

FROM python:3.13-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}"

# Copy venv and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/.env* /app/

EXPOSE 9001

CMD ["python", "src/server.py", "--host", "0.0.0.0", "--transport", "sse"]

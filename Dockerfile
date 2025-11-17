FROM ghcr.io/astral-sh/uv:debian-slim AS builder

ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked
# Copy project files
COPY . .
# COPY build/env.tmpl /app/.env
# Install project dependencies into a local venv


FROM ghcr.io/astral-sh/uv:debian-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# Copy venv and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app .

EXPOSE 9001

VOLUME [ "/var/log/bondlink" ]
ENTRYPOINT ["uv", "run", "src/server.py", "--host", "0.0.0.0", "--port", "9001", "--transport", "http"]

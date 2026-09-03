# Image for the scheduled worker (`bitcap-worker`) and the read-only API.
#
# One image, two entrypoints: the platform's cron job overrides CMD with
# `bitcap-worker`, the web service overrides it with uvicorn. They share a
# database, so building them separately would only mean maintaining two
# Dockerfiles that must not drift.

FROM python:3.12-slim

# ca-certificates is required for every HTTPS fetch and is not in slim by
# default. Nothing here shells out to git.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a source edit does not invalidate the dependency layer.
# --frozen refuses to update the lockfile: the deployed environment must be the
# one that was tested, not whatever resolves at build time.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# config/ and prompts/ are read at runtime, not imported — the register, the
# scoring rule and the classifier prompt all live there. research/ carries the
# harvesters the orchestrator adapts.
COPY app/ ./app/
COPY api/ ./api/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY config/ ./config/
COPY prompts/ ./prompts/
COPY research/ ./research/
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# The per-URL fetch caches live here, and production does not depend on them.
# Everything expensive is in Postgres — classifications, GitHub commit history —
# and an article already stored and classified is never requested again, so
# there is nothing for a page cache to save. Mounting a volume is optional;
# Render's cron jobs cannot, and do not need to (see README, "Deploying").
VOLUME ["/app/research/docs"]

CMD ["bitcap-worker"]

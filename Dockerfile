FROM ghcr.io/astral-sh/uv:0.10.3 AS uv

FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MIA_STANDARDS_ROOT=/app/standards/idta-submodel-templates

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system mia && \
    useradd --system --create-home --gid mia --home-dir /app mia && \
    mkdir -p /data && \
    chown mia:mia /data

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./backend/
# Vercel deployments use PostgreSQL checkpoints/catalogue storage and Vercel Blob. Keep those
# optional production adapters in the image; a later plain sync would otherwise prune them.
RUN uv sync --project backend --locked --no-dev --no-install-project --extra production
RUN /app/backend/.venv/bin/python -m playwright install --with-deps --only-shell chromium

COPY backend/src ./backend/src
RUN uv sync --project backend --locked --no-dev --extra production

COPY ["standards/idta-submodel-templates/published/Digital nameplate/3/0/1/IDTA 02006-3-0-1_Template_Digital Nameplate.json", "/app/standards/idta-submodel-templates/published/Digital nameplate/3/0/1/IDTA 02006-3-0-1_Template_Digital Nameplate.json"]
COPY ["standards/idta-submodel-templates/published/Technical_Data/2/0/1/IDTA 02003_2-0-1_Template_TechnicalData.json", "/app/standards/idta-submodel-templates/published/Technical_Data/2/0/1/IDTA 02003_2-0-1_Template_TechnicalData.json"]

USER mia

EXPOSE 8000

CMD ["sh", "-c", "exec /app/backend/.venv/bin/uvicorn mia_dpp.main:app --host ${MIA_BIND_HOST:-0.0.0.0} --port ${PORT:-8000}"]

# Keep the local Agent Server CLI out of the production backend image.
FROM app AS agent-server
USER root
RUN mkdir -p /app/.langgraph_api && chown -R mia:mia /app/.langgraph_api
RUN uv sync --project backend --locked --no-dev --extra production --extra agent-server
USER mia
EXPOSE 2025
CMD ["sh", "-c", "exec /app/backend/.venv/bin/langgraph dev --host 127.0.0.1 --port ${AGENT_SERVER_PORT:-2025} --no-browser --no-reload --config /app/langgraph.json"]

# Keep ordinary `docker build .` consumers (including Vercel) on the production runtime.
FROM app AS production

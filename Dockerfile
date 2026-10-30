FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS backend
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17
COPY pyproject.toml uv.lock ./
COPY router/ ./router/
RUN uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLM_ROUTER_DATA_DIR=/data \
    LLM_ROUTER_WEB_DIST=/app/web/dist \
    LLM_ROUTER_HOST=127.0.0.1 \
    LLM_ROUTER_PORT=18080
COPY --from=backend /app/.venv /app/.venv
COPY --from=frontend /build/dist /app/web/dist
USER 1000:1000
EXPOSE 18080
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('LLM_ROUTER_PORT','18080')+'/healthz',timeout=3)"
ENTRYPOINT ["llm-router"]
CMD ["serve"]

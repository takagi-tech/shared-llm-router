FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17
COPY runner/requirements.lock /app/requirements.lock
RUN uv pip install --system --no-cache -r requirements.lock --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
COPY router/ /app/router/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/cache/huggingface HOME=/tmp OMP_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false
USER 1000:1000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18083/healthz',timeout=3)"
CMD ["python", "-m", "uvicorn", "router.runner:app", "--host", "127.0.0.1", "--port", "18083", "--no-access-log"]

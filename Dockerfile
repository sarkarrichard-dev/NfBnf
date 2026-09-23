# One QuantHawk instance: API + dashboard + all lanes, single tenant.
# The same image runs Richard's personal EC2 box now and, later, one isolated
# container per paying customer. Secrets and trading data are NOT in the image:
#   /app/.env     bind-mounted (the dashboard's settings writes persist to it)
#   /app/memory   volume (journals, market log, models, caches)
# See deploy/README-cloud.md.

FROM node:22-slim AS dashboard
WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Kolkata PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY index_ai ./index_ai
# tzdata: slim images ship no zoneinfo; boto3: the S3 backup of memory/
RUN pip install . tzdata boto3
COPY crypto ./crypto
COPY commodities ./commodities
COPY investing ./investing
COPY scripts ./scripts
COPY --from=dashboard /build/dist ./dashboard/dist
RUN useradd --create-home algo && mkdir -p memory && touch .env && chown -R algo /app
USER algo
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "-m", "uvicorn", "index_ai.server:app", "--host", "0.0.0.0", "--port", "8000"]

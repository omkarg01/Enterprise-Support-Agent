# The app image for the VM (AC-15), built on the VM itself (arm64) by deploy/vm/release.py from the repo root:
#   docker build -f deploy/vm/Dockerfile -t support-agent-app:<commit> .
# deploy/vm/Dockerfile.dockerignore keeps .env, keys, .git, tests and docs out of the build context.
# The code runs from /app/src (not installed), so data.migrate finds /app/migrations.
FROM python:3.11.13-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 agent

WORKDIR /app
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
      > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY src ./src
COPY evals ./evals
COPY migrations ./migrations
COPY policies ./policies
COPY deploy/vm ./deploy/vm

ENV PYTHONPATH=/app/src:/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["python", "deploy/vm/serve.py"]

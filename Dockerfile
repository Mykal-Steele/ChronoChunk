FROM python:3.15-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ffmpeg + libopus are for voice and music
# pandoc, pango and the fonts are for building DOCX and PDF files (Noto covers Thai)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    pandoc \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    fonts-noto-core \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# The bot does not run as root. 1000 is also the first user on the host, who owns the data folders.
RUN useradd --create-home --uid 1000 bot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Adds pytest. The tests are not in the image, so the checkout is mounted over /app:
#   docker build --target test -t chronochunk:dev .
FROM base AS test

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

# pyright runs on Node. Its [nodejs] extra still ships Node 24.19.0, which has
# CVE-2026-111497 and has no fixed wheel yet, so the type check gets a patched LTS.
ARG NODE_VERSION=24.22.1
RUN python -m nodeenv --node="${NODE_VERSION}" /opt/node
ENV PATH="/opt/node/bin:${PATH}"


# The image the bot runs from. It is the last stage, so a plain `docker build .` makes it.
FROM base

# The code stays owned by root, so the bot can write to its data folders and nothing else
COPY . .
RUN chmod -R go-w /app && mkdir -p logs user_data state && chown bot:bot logs user_data state

USER bot

# Health check server from src/web_server.py
EXPOSE 8080

CMD ["python", "run.py"]

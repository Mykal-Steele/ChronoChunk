FROM python:3.12-slim

# ffmpeg + libopus required for discord.py[voice] and yt-dlp
# pandoc, pango and the fonts are for building DOCX and PDF files (Noto covers Thai)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    libopus-dev \
    gcc \
    pandoc \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    fonts-noto-core \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs user_data state

# Health check server from src/web_server.py
EXPOSE 8080

CMD ["python", "run.py"]

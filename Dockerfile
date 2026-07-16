FROM python:3.12-slim

# ffmpeg + libopus required for discord.py[voice] and yt-dlp
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    libopus-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs user_data

# Northflank injects PORT; web_server.py reads it via os.getenv("PORT", 10000)
EXPOSE 10000

CMD ["python", "run.py"]

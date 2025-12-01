FROM python:3.11-slim

# Install system dependencies including ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

# Expose port (default 8000, Railway will override)
EXPOSE 8000

# Use Railway's PORT env variable
CMD ["bash", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]

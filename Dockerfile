# ---------------------------------------------------
#  Dockerfile for FastAPI downloader backend
# ---------------------------------------------------

FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

# Install system dependencies for yt-dlp & lxml
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Fly.io uses this command to run the app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
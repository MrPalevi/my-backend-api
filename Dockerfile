FROM python:3.11-slim

# ===========================================
# Install dependencies (ffmpeg, lib magic)
# ===========================================
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ===========================================
# Set workdir
# ===========================================
WORKDIR /app

# ===========================================
# Copy requirements
# ===========================================
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ===========================================
# Copy project files
# ===========================================
COPY . .

# ===========================================
# Expose port (Railway uses $PORT)
# ===========================================
EXPOSE 8000

# ===========================================
# Command
# ===========================================
CMD ["python3", "main.py"]

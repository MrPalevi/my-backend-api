# Gunakan Python image yang ringan
FROM python:3.11-slim

# Install dependency OS termasuk ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set folder kerja
WORKDIR /app

# Copy semua file ke container
COPY . .

# Install dependency Python
RUN pip install --no-cache-dir -r requirements.txt

# Expose port untuk Railway
EXPOSE 8000

# Command run server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

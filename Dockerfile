FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code into a subdirectory to support 'polybot' package imports
COPY . polybot/

# Set python path to include /app, so 'from polybot...' works
ENV PYTHONPATH=/app

# Default command
CMD ["python", "polybot/main.py"]

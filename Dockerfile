FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first to avoid legacy resolver issues
RUN pip install --no-cache-dir --upgrade pip

# Pre-install Mako explicitly to unblock alembic dependency resolution
RUN pip install --no-cache-dir "Mako>=1.3.0"

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=300 --retries=10 -r requirements.txt

# Copy application source
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
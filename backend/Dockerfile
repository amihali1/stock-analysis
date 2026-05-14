FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy all source first
COPY pyproject.toml .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY trained_models/ trained_models/
COPY scripts/ scripts/

# Install package (includes src as a package) and remove the duplicate
# build-context copies. After install, the authoritative source lives only at
# /usr/local/lib/python3.10/site-packages/src/...  — keeping /app/src and
# /app/build/ around had created a triple-copy layout that caused hot-patch
# confusion in Sessions 23 / 25.
RUN pip install --no-cache-dir . \
    && rm -rf src build

EXPOSE 8000

# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port 8000"]

# Long-lived FastAPI + uvicorn web process (not serverless).
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    MAX_JOBS_PER_RUN=5

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sql ./sql
COPY docs ./docs
COPY tests ./tests
COPY README.md .env.example ./

EXPOSE 8000

# Fly injects PORT; bind to all interfaces.
CMD uvicorn main:app --app-dir src --host 0.0.0.0 --port ${PORT}

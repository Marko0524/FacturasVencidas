FROM python:3.11-slim

# Unbuffered stdout is mandatory: without it the container shows no logs at all.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first, so code changes do not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY sample_data/ ./sample_data/
COPY main.py ./

# Non-root user. /app/state must exist and be writable for idempotency.
RUN useradd --create-home --uid 1001 appuser \
    && mkdir -p /app/state \
    && chown -R appuser:appuser /app
USER appuser

# No secrets in the image: everything comes from the runtime environment.
CMD ["python", "main.py"]

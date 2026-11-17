FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SLIDE_NAVIGATOR_DATA_DIR=/data \
    SLIDE_NAVIGATOR_RENDERING_ENABLED=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-impress fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app

VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

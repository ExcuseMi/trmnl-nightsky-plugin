FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["hypercorn", "--bind", "0.0.0.0:8080", "--workers", "1", "app:app"]

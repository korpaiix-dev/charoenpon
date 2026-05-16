FROM python:3.11-slim
WORKDIR /app
# Install docker CLI for managing other containers
RUN apt-get update && apt-get install -y --no-install-recommends docker.io ca-certificates && rm -rf /var/lib/apt/lists/*
COPY dashboard/requirements.txt /app/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/dashboard/requirements.txt
COPY . .
CMD ["uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8010"]

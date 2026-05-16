FROM python:3.11-slim
WORKDIR /app
# Install Docker CLI from official static binary (avoids package manager issues)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz | tar -xzC /tmp && \
    mv /tmp/docker/docker /usr/local/bin/docker && \
    chmod +x /usr/local/bin/docker && \
    rm -rf /tmp/docker /var/lib/apt/lists/*
COPY dashboard/requirements.txt /app/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/dashboard/requirements.txt
COPY . .
CMD ["uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8010"]

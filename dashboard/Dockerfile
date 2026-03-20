FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY dashboard/requirements.txt /app/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/dashboard/requirements.txt

# Copy entire project (needed for module imports)
COPY . .

CMD ["uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8010"]

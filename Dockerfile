FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2 / asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev postgresql-client tesseract-ocr tesseract-ocr-tha fonts-thai-tlwg ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "bots.sales_bot"]

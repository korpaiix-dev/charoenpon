FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] asyncpg pydantic httpx
COPY gacha_api.py /app/
EXPOSE 8001
CMD ["uvicorn", "gacha_api:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]

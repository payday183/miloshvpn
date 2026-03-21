FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install uvloop

COPY . .

CMD ["uvicorn", "backend.api.routes:create_app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop", "--workers", "2"]

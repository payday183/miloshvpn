FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install uvloop

COPY . .

CMD ["/bin/sh", "-c", "python -m backend.bootstrap && exec uvicorn backend.api.routes:create_app --factory --host 0.0.0.0 --port 8000 --loop uvloop --workers ${UVICORN_WORKERS:-2}"]

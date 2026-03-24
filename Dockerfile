FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY data/ data/

ENV SIDANTRIP_DB_PATH=/app/data
ENV PORT=8001

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn sidantrip.server:app --host 0.0.0.0 --port ${PORT}"]

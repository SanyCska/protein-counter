FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PROTEIN_DB_PATH=/app/data/protein.sqlite3

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ai_protein.py storage.py .

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["python", "bot.py"]

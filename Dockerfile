FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 STATE_FILE=/data/state.json

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pusher ./pusher

VOLUME ["/data"]
CMD ["python", "-m", "pusher"]

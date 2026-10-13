FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
# one worker: route incident delays live in memory; threads serve the long-lived WebSocket connections
CMD ["gunicorn", "-w", "1", "--threads", "100", "-b", "0.0.0.0:5000", "wsgi:app"]

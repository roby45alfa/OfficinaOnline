# OfficinaOnline: sito Flask (gunicorn) + bot Telegram
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Rome

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 5000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:5000'+os.environ.get('URL_PREFIX','/gestioneveicoli')+'/healthz', timeout=4)"

ENTRYPOINT ["./entrypoint.sh"]

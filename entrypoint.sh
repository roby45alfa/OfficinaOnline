#!/bin/sh
set -e

# Aggiorna lo schema del database (non distruttivo) e crea la chiave di sessione
# una volta sola, prima che partano i worker di gunicorn
python -c "import app"

# Bot Telegram in background, solo se c'è il token
if [ -n "$TELEGRAM_TOKEN" ]; then
  python telegram_bot.py &
else
  echo "TELEGRAM_TOKEN non impostato: bot disattivato."
fi

# Sito con gunicorn (server di produzione, non quello di sviluppo di Flask)
exec gunicorn --bind 0.0.0.0:5000 --workers 2 --threads 4 --timeout 120 \
     --access-logfile - app:app

#!/bin/sh
# inizializza db se non esiste
python - <<'PY'
from db import init_db
init_db()
PY

# avvia bot in background
python telegram_bot.py &

# avvia sito Flask
exec python app.py

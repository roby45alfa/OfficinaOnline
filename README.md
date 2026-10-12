# OfficinaOnline – tagliandi e scadenze veicoli

Web-app Flask + bot Telegram per tenere lo storico degli interventi, sapere quando
tocca il prossimo tagliando e non dimenticare bollo, assicurazione e revisione.

## Cosa fa

- **Riepilogo**: una lista unica di cosa scade (superato / entro 30 giorni / in regola),
  con il prossimo tagliando stimato per ogni veicolo e i pulsanti «Rinnova» e «Registra».
- **Prossimo tagliando**: calcolato dall'ultimo intervento di tipo «Tagliando» con
  l'intervallo del veicolo (default 15.000 km o 12 mesi, modificabile). Vale la prima soglia.
- **Interventi**: tipo, data, km, costo, officina, note e 18 voci di ricambio (olio, filtri,
  distribuzione, freni, gomme, batteria…) con marca e codice. «Come l'ultima volta» ricopia
  i ricambi dell'intervento precedente. Modifica ed eliminazione.
- **Contachilometri** aggiornato da solo quando registri un intervento con più km.
- **Scadenze**: bollo, assicurazione, revisione, tagliando, gomme invernali o testo libero.
  «Rinnova» sposta la data di 12 mesi (revisione 24).
- **Veicoli**: ricerca, foto con galleria, libretto (PDF o foto), note, esportazione CSV
  dello storico (si apre in Excel), stampa scheda.
- **Utenti**: utente / amministratore / amministratore principale. Gli utenti vedono solo
  i propri veicoli; un admin può assegnare un veicolo a un altro utente.
- **Tema chiaro/scuro**, utilizzabile da smartphone (barra di navigazione in basso).
- **Bot Telegram**: scheda veicolo, «Da fare», registrazione guidata di interventi,
  scadenze e km, promemoria giornaliero in ora italiana.

## Avvio con Docker

```bash
cp .env.example .env      # poi compila TELEGRAM_TOKEN e SECRET_KEY
docker compose up -d --build
docker compose logs -f
```

Sito: `http://IP-DEL-SERVER:5000/gestioneveicoli/` (anche `http://IP:5000/` reindirizza lì).
Primo accesso: `admin` / `admin`, poi viene chiesto di cambiare password.

### Aggiornare un'installazione esistente

Il database esistente viene aggiornato da solo all'avvio: si aggiungono colonne e tabelle,
nessun dato viene cancellato. Fai comunque una copia di `database.db` prima.

1. `docker compose down`
2. copia `database.db` in un posto sicuro
3. sostituisci i file del progetto (tieni `database.db`, `.env` e la cartella `static/`)
4. `docker compose up -d --build`

Dopo l'aggiornamento tutti devono rifare il login (cambia la gestione delle sessioni) e
sul bot va rifatto `/login` una volta: da quel momento la sessione resta anche ai riavvii.

### Dietro Nginx Proxy Manager

Inoltra `/gestioneveicoli` a `http://IP:5000` mantenendo il percorso. L'app legge gli header
`X-Forwarded-*`. Se il sito è solo in HTTPS imposta `SESSION_COOKIE_SECURE=1`.
Per servirlo alla radice di un sottodominio usa `URL_PREFIX=` (vuoto).

## Avvio senza Docker (sviluppo)

```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
FLASK_DEBUG=1 python app.py            # sito su :5000
TELEGRAM_TOKEN=... python telegram_bot.py
```

## Variabili d'ambiente

| Variabile | Default | Uso |
|---|---|---|
| `TELEGRAM_TOKEN` | – | token del bot; se vuoto il bot non parte |
| `SECRET_KEY` | generata in `.secret_key` | firma delle sessioni |
| `URL_PREFIX` | `/gestioneveicoli` | percorso del sito |
| `DB_PATH` | `database.db` | file SQLite |
| `SESSION_COOKIE_SECURE` | `0` | `1` se solo HTTPS |
| `ORA_NOTIFICHE` | `08:00` | ora del promemoria Telegram |
| `TZ` | `Europe/Rome` | fuso orario |

## Bot Telegram

`/login utente password` (il messaggio con la password viene cancellato dalla chat),
`/veicoli`, `/dafare`, `/notifiche on|off`, `/annulla`, `/logout`, oppure i pulsanti del menu.

## Sicurezza

- Password con hash, cambio obbligatorio al primo accesso, minimo 6 caratteri.
- Protezione CSRF su tutti i form; tutte le azioni che modificano dati sono POST.
- Ogni pagina, foto e libretto controlla che il veicolo appartenga all'utente.
- Upload con nomi univoci (niente sovrascritture) e solo formati ammessi.
- Blocco temporaneo dopo 8 login falliti in 10 minuti dallo stesso IP.
- `.env`, database e caricamenti sono esclusi da Git.

## Struttura

```
app.py              sito Flask (rotte, permessi, upload)
db.py               database SQLite, migrazioni, calcolo scadenze e tagliandi
telegram_bot.py     bot Telegram
templates/          pagine HTML (Jinja2 + Bootstrap 5)
static/css/app.css  stile
static/uploads|libretti|profiles   file caricati (non su Git)
```

## Backup

Basta copiare `database.db` e la cartella `static/` (foto, libretti, foto profilo).

## Licenza

MIT

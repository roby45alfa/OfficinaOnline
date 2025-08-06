<!--
File: README.md
Documentazione completa per il progetto “Officina”
-->

# Gestionale Officina – Flask + Telegram Bot

Una soluzione full-stack per gestire veicoli, manutenzioni, scadenze e utenti con ruoli differenziati. Pensata per piccole officine o privati che vogliono una web-app leggera e un bot Telegram per interazione quick-access.

---

## Indice

1. [Panoramica](#panoramica)  
2. [Caratteristiche Principali](#caratteristiche-principali)  
3. [Prerequisiti](#prerequisiti)  
4. [Installazione & Configurazione](#installazione--configurazione)  
5. [Esecuzione via Docker](#esecuzione-via-docker)  
6. [Esecuzione in Locale](#esecuzione-in-locale)  
7. [Struttura del Progetto](#struttura-del-progetto)  
8. [Endpoint Web](#endpoint-web)  
9. [Bot Telegram](#bot-telegram)  
10. [Gestione Utenti e Sicurezza](#gestione-utenti-e-sicurezza)  
11. [Backup & Manutenzione](#backup--manutenzione)  
12. [Possibili Estensioni](#possibili-estensioni)  
13. [Licenza](#licenza)  

---

## Panoramica

Questo progetto fornisce:

- **Interfaccia Web**: realizzata con Flask, Bootstrap 5 e SQLite per il database.  
- **Telegram Bot**: implementato con python-telegram-bot, per comandi e notifiche.  
- **Container Docker**: `Dockerfile` e `docker-compose.yml` pronti all’uso.  

Ogni utente (standard o admin) può gestire i propri veicoli, registrare manutenzioni e aggiungere scadenze (bollo, assicurazione, revisione). Un superadmin iniziale (`admin`/`admin`) può amministrare anche gli altri utenti.

---

## Caratteristiche Principali

- **Autenticazione e Ruoli**  
  - Superadmin (`admin`) non demovibile né rimovibile.  
  - Admin possono creare/modificare/eliminare utenti (tranne il superadmin).  
  - Utenti standard vedono solo i propri veicoli e dati.  
  - Cambio password obbligatorio al primo login o quando forzato.  

- **Gestione Veicoli**  
  - CRUD veicoli con campi: proprietario, targa, marca, modello, anno, km.  
  - Upload multiplo di immagini con galleria lightbox.  
  - Upload/download PDF del libretto.  

- **Manutenzioni**  
  - Storico con data, km e dettagli JSON-encoded.  
  - Checkbox granulari per filtri e ricambi, con codice e marca.  
  - Tipo olio a parte e campo note libero.  

- **Scadenze**  
  - Aggiunta di scadenze (tipo + data).  
  - Filtro prossime 7 giorni, scadute o tutte.  

- **Telegram Bot**  
  - `/login`, `/logout`, `/help`  
  - Menu a pulsanti per Veicoli, Manutenzioni, Scadenze  
  - Inline keyboard per selezionare veicolo e visualizzare/elencare o aggiungere record  
  - Form interattivo per aggiungere manutenzioni (tipo, data, note)  
  - Notifiche giornaliere UTC con report scadenze (configurabili).  

- **Docker Ready**  
  - Immagine basata su Python 3.11-slim.  
  - `docker compose up` lancia web server e bot in parallelo.  

---

## Prerequisiti

- Docker ≥ 20.10  
- Docker Compose ≥ 1.29  
- (Opzionale) Python 3.11 se si esegue in locale  
- File `.env` con variabili d’ambiente  

---

## Installazione & Configurazione

1. **Clona il repository**  
   ```bash
   git clone https://github.com/roby45alfa/OfficinaOnline.git
   cd officina
   ```

2. **Crea file `.env`**  
   ```ini
   TELEGRAM_TOKEN=il_tuo_token
   FLASK_ENV=production
   SECRET_KEY=una_chiave_super_segreta
   ```

3. **Verifica `app.py`**  
   - Imposta `app.secret_key` con `SECRET_KEY`.  
   - (Opzionale) Modifica percorsi upload in `UPLOAD_FOLDER`, `PDF_FOLDER`, `PROFILE_FOLDER`.  

4. **Database**  
   Al primo avvio `init_db()` crea le tabelle e l’utente superadmin:  
   - Username: `admin`  
   - Password: `admin`  

---

## Esecuzione via Docker

```bash
# Costruisci le immagini
docker compose build

# Avvia in background
docker compose up -d

# Monitora i log
docker compose logs -f
```

- Web UI: `http://localhost:5000`  
- Il bot risponde via polling al token configurato.  

---

## Esecuzione in Locale (senza Docker)

```bash
# Virtualenv
python3 -m venv venv
source venv/bin/activate

# Dipendenze
pip install -r requirements.txt

# Variabili
export TELEGRAM_TOKEN=il_tuo_token
export FLASK_ENV=development
export SECRET_KEY=supersegreta

# Avvia web
python app.py

# In un altro terminale, avvia bot
python telegram_bot.py
```

---

## Struttura del Progetto

```
.
├── app.py
├── db.py
├── telegram_bot.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── README.md
├── static/
│   ├── uploads/
│   ├── libretti/
│   └── profiles/
└── templates/
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── veicoli.html
    ├── aggiungi_veicolo.html
    ├── dettaglio_veicolo.html
    ├── modifica_veicolo.html
    ├── manutenzione.html
    ├── scadenze.html
    └── users/
        ├── users.html
        ├── add_user.html
        └── edit_user.html
```

---

## Endpoint Web

| Metodo   | URL                           | Descrizione                                |
| -------- | ----------------------------- | ------------------------------------------ |
| GET      | `/`                           | Login o redirect a dashboard               |
| GET/POST | `/change_password`            | Cambio password (first login)              |
| GET      | `/dashboard`                  | Panoramica veicoli, manutenzioni, scadenze |
| GET      | `/veicoli`                    | Lista veicoli                              |
| GET/POST | `/aggiungi_veicolo`           | Form / salva nuovo veicolo                 |
| GET      | `/veicolo/<id>`               | Dettaglio veicolo                          |
| GET/POST | `/modifica_veicolo/<id>`      | Form / aggiorna veicolo                    |
| GET/POST | `/manutenzione/<id>`          | Form / salva manutenzione                  |
| GET/POST | `/scadenze/<id>`              | Form / salva scadenza                      |
| GET      | `/users`                      | Lista utenti (admin)                       |
| GET/POST | `/users/add`                  | Form / salva nuovo utente (admin)          |
| GET/POST | `/users/edit/<id>`            | Form / aggiorna utente (admin)             |
| GET      | `/users/delete/<id>`          | Elimina utente (admin)                     |
| POST     | `/veicolo/delete/<id>`        | Elimina veicolo (solo proprietario o admin)|

---

## Bot Telegram

- **Slash commands**  
  - `/start`, `/help`, `/login`, `/logout`, `/veicoli`  
  - `/attiva_notifiche`, `/disattiva_notifiche`, `/imposta_notifiche HH:MM`

- **Menu a pulsanti**  
  - 🚗 Veicoli → elenco o inline  
  - 🔧 Manutenzioni → selezione + elenco + aggiungi  
  - ⏰ Scadenze → selezione + filtri + aggiungi  

- **Form interattivo**  
  1. Clicca “➕ Aggiungi”  
  2. Scegli campo (tipo/data/note)  
  3. Inserisci valore  
  4. Salva  

- **Notifiche**  
  Report quotidiano UTC su scadenze scadute/prossime.

---

## Gestione Utenti e Sicurezza

- **Superadmin**  
  - `admin`/`admin` (cambio obbligatorio)  
  - Non rimovibile  

- **Admin**  
  - Gestione utenti (esclude superadmin)  

- **Standard**  
  - Accesso limitato ai propri dati  

- **Protezione**  
  - Password hashate (`werkzeug.security`)  
  - Sessioni server-side (`secret_key`)  
  - Delete via POST + confirm JS  

---

## Backup & Manutenzione

- Salva regolarmente `database.db`.  
- Pulisci `static/uploads` periodicamente.  
- Monitora i log con `docker compose logs -f`.  

---

## Possibili Estensioni

- Email o push notifications  
- API REST/GraphQL  
- 2FA / OAuth2  
- Reportistica (grafici, CSV/PDF)  
- i18n / multi-lingua  
- Ruoli più granulari  

---

## Licenza

Rilasciato sotto **MIT License**. Vedi [LICENSE](LICENSE) per i dettagli.  

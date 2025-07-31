# Gestionale Veicoli con Utenti e Superadmin

Sistema in Flask + SQLite per gestire veicoli, manutenzioni, scadenze e utenti con isolamento dati. Pensato per essere eseguito via Docker (es. su Raspberry OS) o in locale.

## Caratteristiche

- **Utenti**
  - Superadmin iniziale (`admin`/`admin`) non demotabile né eliminabile da altri.
  - Admin normali possono creare, modificare ed eliminare utenti (tranne il superadmin).
  - Utenti standard vedono solo i propri dati.
  - Cambio password obbligatorio al primo accesso o quando forzato.
  - Profilo con avatar visibile nella navbar.

- **Veicoli**
  - Aggiungi / modifica / elimina veicoli.
  - Foto multiple con galleria cliccabile (modal con frecce e indicatori).
  - Upload PDF libretto e download.
  - Dettagli chiari: proprietario, targa, marca, modello, anno, km.

- **Manutenzioni**
  - Flag granulari (filtro olio, gasolio, aria, freni, ecc.) con codice e marca.
  - Tipo olio separato.
  - Storico espandibile nella pagina del veicolo con i dettagli.

- **Scadenze**
  - Aggiunta di bollo, assicurazione, revisione con date.

- **Eliminazione**
  - Veicoli cancellabili da proprietario o admin. Conferma via form.

- **Isolamento**
  - Ogni utente ha i propri veicoli/manutenzioni/scadenze legati tramite owner_id.

- **Docker-ready**
  - `Dockerfile` e `docker-compose.yml` inclusi.

## Quickstart

```bash
# Avvia il servizio
docker compose down
docker compose up --build
```

Apri `http://localhost:5000`.

## Credenziali iniziali

- Username: `admin`
- Password: `admin`
  - Al primo login viene richiesto il cambio password.

## Endpoints principali

- `/` - login  
- `/change_password` - cambio password forzato  
- `/dashboard` - panoramica veicoli, manutenzioni e scadenze  
- `/veicoli` - lista veicoli  
- `/aggiungi_veicolo` - form nuovo veicolo  
- `/veicolo/<id>` - dettagli con galleria, manutenzioni, scadenze  
- `/modifica_veicolo/<id>` - modifica veicolo  
- `/manutenzione/<id>` - aggiungi manutenzione  
- `/scadenze/<id>` - aggiungi scadenza  
- `/users` - lista utenti (solo admin)  
- `/users/add` - aggiungi utente (solo admin)  
- `/users/edit/<id>` - modifica utente  
- `/users/delete/<id>` - elimina utente  
- `/veicolo/delete/<id>` - elimina veicolo (POST)  

## Git

```bash
git init
git add .
git commit -m "feat: setup gestionale veicoli con utenti, manutenzioni e galleria"
git branch -M main
git remote add origin <tuo-repo>
git push -u origin main
```

## Note

- Cambia `app.secret_key` in `app.py` prima di esporre pubblicamente.  
- Fai backup regolari di `database.db`.  
- Aggiungi HTTPS e hardening per deploy reale.  

## Estensioni possibili

- Audit/log azioni  
- Recupero password  
- Notifiche scadenze  
- Ruoli più fini  
- API REST per app mobile  
- 2FA  


"""
File: db.py
Modulo per accesso SQLite:
• Definisce schema tabelle se non esistente
• CRUD utenti, veicoli, manutenzioni, scadenze
• Utilizza JSON per campi avanzati (immagini, details)
"""

import sqlite3
from werkzeug.security import generate_password_hash
import json

# Percorso del database SQLite
DB = 'database.db'

def get_db():
    """
    Restituisce una nuova connessione SQLite con row_factory che permette
    di accedere alle colonne come dizionario.
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Crea tabelle se non esistono:
    • users, veicoli, manutenzioni, scadenze
    Inserisce utente admin di default (username=admin, pw=admin).
    """
    conn = get_db()
    c = conn.cursor()
    # Tabella utenti
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        profile_img TEXT,
        is_admin INTEGER,
        must_change_password INTEGER DEFAULT 1,
        is_superadmin INTEGER DEFAULT 0
    )""")
    # Tabella veicoli
    c.execute("""CREATE TABLE IF NOT EXISTS veicoli (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        nome TEXT, cognome TEXT,
        targa TEXT, marca TEXT,
        modello TEXT, anno TEXT,
        km INTEGER,
        immagini TEXT,      -- JSON list
        libretto TEXT,
        FOREIGN KEY(owner_id) REFERENCES users(id)
    )""")
    # Tabella manutenzioni
    c.execute("""CREATE TABLE IF NOT EXISTS manutenzioni (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        veicolo_id INTEGER,
        data TEXT,          -- YYYY-MM-DD
        km INTEGER,
        details TEXT,       -- JSON dict
        FOREIGN KEY(veicolo_id) REFERENCES veicoli(id)
    )""")
    # Tabella scadenze
    c.execute("""CREATE TABLE IF NOT EXISTS scadenze (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        veicolo_id INTEGER,
        tipo TEXT,
        data TEXT,          -- YYYY-MM-DD
        FOREIGN KEY(veicolo_id) REFERENCES veicoli(id)
    )""")
    # Utente superadmin di default
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        hashed = generate_password_hash('admin')
        c.execute("""INSERT INTO users
            (username,password,profile_img,is_admin,must_change_password,is_superadmin)
            VALUES (?,?,?,?,?,?)""",
            ('admin', hashed, None, 1, 1, 1)
        )
    conn.commit()
    conn.close()

# ──────────────────────────────────────────────────────────────────────────────
# GESTIONE UTENTI
# ──────────────────────────────────────────────────────────────────────────────
def get_user_by_username(username):
    """Restituisce riga utente per username, oppure None."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    u = c.fetchone()
    conn.close()
    return u

def get_user_by_id(uid):
    """Restituisce riga utente per id."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (uid,))
    u = c.fetchone()
    conn.close()
    return u

def get_users():
    """Restituisce lista di tutti gli utenti (cols selezionate)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT id,username,profile_img,is_admin,
                        must_change_password,is_superadmin
                 FROM users""")
    rows = c.fetchall()
    conn.close()
    return rows

def add_user(username, password_hash, profile_img, is_admin, must_change_password=1):
    """Aggiunge un nuovo utente con hash password e flag."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO users
        (username,password,profile_img,is_admin,must_change_password)
        VALUES (?,?,?,?,?)""",
        (username, password_hash, profile_img, is_admin, must_change_password)
    )
    conn.commit()
    conn.close()

def delete_user(uid):
    """
    Elimina utente:
    • Se superadmin, non fa nulla
    • Altrimenti cancella il record
    """
    conn = get_db()
    c    = conn.cursor()
    c.execute("SELECT is_superadmin FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    if row and row['is_superadmin']:
        conn.close()
        return
    c.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()

def aggiorna_utente(uid, username=None, password_hash=None,
                   profile_img=None, is_admin=None, must_change_password=None):
    """
    Aggiorna campi utente:
    • Costruisce dinamicamente l’UPDATE in base ai parametri non-None
    """
    conn = get_db()
    c    = conn.cursor()
    updates = []
    params  = []
    if username is not None:
        updates.append("username=?"); params.append(username)
    if password_hash is not None:
        updates.append("password=?"); params.append(password_hash)
    if profile_img is not None:
        updates.append("profile_img=?"); params.append(profile_img)
    if is_admin is not None:
        updates.append("is_admin=?"); params.append(is_admin)
    if must_change_password is not None:
        updates.append("must_change_password=?"); params.append(must_change_password)
    if not updates:
        conn.close()
        return
    params.append(uid)
    sql = f"UPDATE users SET {', '.join(updates)} WHERE id=?"
    c.execute(sql, tuple(params))
    conn.commit()
    conn.close()

def set_password(username, password_hash):
    """
    Cambia password utente e leva flag must_change_password.
    Usato al primo cambio password.
    """
    conn = get_db()
    c    = conn.cursor()
    c.execute("""UPDATE users
                 SET password=?, must_change_password=0
                 WHERE username=?""",
              (password_hash, username))
    conn.commit()
    conn.close()

# ──────────────────────────────────────────────────────────────────────────────
# VEICOLI – MANUTENZIONI – SCADENZE
# ──────────────────────────────────────────────────────────────────────────────
def get_veicoli(user_id=None, is_admin=False):
    """
    Restituisce lista di veicoli:
    • Se is_admin=True restituisce tutti
    • Altrimenti solo quelli con owner_id=user_id
    """
    conn = get_db(); c = conn.cursor()
    if is_admin:
        c.execute("SELECT * FROM veicoli")
    else:
        c.execute("SELECT * FROM veicoli WHERE owner_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_veicolo_by_id(vid, user_id=None, is_admin=False):
    """
    Restituisce dizionario veicolo con campi e immagini JSON-decoded.
    • Se non trova o non permesso, ritorna None.
    """
    conn = get_db(); c = conn.cursor()
    if is_admin:
        c.execute("SELECT * FROM veicoli WHERE id=?", (vid,))
    else:
        c.execute("SELECT * FROM veicoli WHERE id=? AND owner_id=?", (vid, user_id))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    # Decodifica lista immagini da JSON
    immagini = json.loads(row['immagini']) if row['immagini'] else []
    return {
        'id': row['id'],
        'nome': row['nome'],
        'cognome': row['cognome'],
        'targa': row['targa'],
        'marca': row['marca'],
        'modello': row['modello'],
        'anno': row['anno'],
        'km': row['km'],
        'immagini': immagini,
        'libretto': row['libretto']
    }

def aggiungi_veicolo(owner_id, nome, cognome, targa,
                     marca, modello, anno, km, imgs, lib):
    """
    Inserisce un nuovo veicolo nel DB.
    • imgs: lista di filename JSON-dumped
    • lib: nome file libretto
    """
    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO veicoli
        (owner_id,nome,cognome,targa,marca,modello,anno,km,immagini,libretto)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (owner_id, nome, cognome, targa, marca, modello, anno, km,
         json.dumps(imgs), lib)
    )
    conn.commit()
    conn.close()

def aggiorna_veicolo(vid, nome, cognome, targa,
                     marca, modello, anno, km, imgs, lib):
    """
    Aggiorna dati di un veicolo esistente.
    """
    conn = get_db(); c = conn.cursor()
    c.execute("""UPDATE veicoli SET
        nome=?, cognome=?, targa=?, marca=?, modello=?,
        anno=?, km=?, immagini=?, libretto=?
        WHERE id=?""",
        (nome, cognome, targa, marca, modello, anno, km,
         json.dumps(imgs), lib, vid)
    )
    conn.commit()
    conn.close()

def elimina_veicolo(vid):
    """Elimina un veicolo per id."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM veicoli WHERE id=?", (vid,))
    conn.commit()
    conn.close()

def get_manutenzioni(vid):
    """
    Restituisce tutte le manutenzioni di un veicolo,
    ordinate per data discendente.
    """
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM manutenzioni WHERE veicolo_id=? ORDER BY data DESC", (vid,))
    rows = c.fetchall()
    conn.close()
    return rows

def aggiungi_manutenzione(vid, data, km, details):
    """
    Inserisce nuova manutenzione:
    • details: dict JSON-dumped dei filtri/pezzi sostituiti
    """
    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO manutenzioni
        (veicolo_id,data,km,details) VALUES (?,?,?,?)""",
        (vid, data, km, json.dumps(details))
    )
    conn.commit()
    conn.close()

def get_scadenze(vid):
    """
    Restituisce tutte le scadenze di un veicolo,
    ordinate per data ascendente.
    """
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM scadenze WHERE veicolo_id=? ORDER BY data ASC", (vid,))
    rows = c.fetchall()
    conn.close()
    return rows

def aggiungi_scadenza(vid, tipo, data):
    """Inserisce una nuova scadenza per un veicolo."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO scadenze (veicolo_id,tipo,data) VALUES (?,?,?)", (vid, tipo, data))
    conn.commit()
    conn.close()

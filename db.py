"""
File: db.py
Accesso SQLite per OfficinaOnline.

• Crea lo schema e applica le migrazioni (aggiunge colonne mancanti ai DB esistenti)
• CRUD utenti, veicoli, manutenzioni, scadenze, chat Telegram
• Tutte le funzioni restituiscono dict (o liste di dict), non tuple
• Usabile sia da Flask sia dal bot (nessuna dipendenza dal contesto Flask)
"""

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

from werkzeug.security import generate_password_hash

# Percorso del database (configurabile con la variabile d'ambiente DB_PATH)
DB = os.environ.get("DB_PATH", "database.db")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get_db():
    """Nuova connessione SQLite con accesso alle colonne per nome."""
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _conn():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _row(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def _to_int(value):
    """Converte in int valori da form ('' o None → None)."""
    if value is None:
        return None
    try:
        return int(str(value).replace(".", "").replace(" ", "").strip())
    except ValueError:
        return None


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA E MIGRAZIONI
# ──────────────────────────────────────────────────────────────────────────────
def _columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _add_column(c, table, coldef):
    name = coldef.split()[0]
    if name not in _columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")


def init_db():
    """
    Crea le tabelle se non esistono e aggiunge le colonne nuove ai DB già in uso.
    I dati esistenti non vengono toccati (tranne la riparazione delle righe
    salvate in modo errato dal vecchio bot, vedi sotto).
    """
    with _conn() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            profile_img TEXT,
            is_admin INTEGER,
            must_change_password INTEGER DEFAULT 1,
            is_superadmin INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS veicoli (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            nome TEXT, cognome TEXT,
            targa TEXT, marca TEXT,
            modello TEXT, anno TEXT,
            km INTEGER,
            immagini TEXT,
            libretto TEXT,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS manutenzioni (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veicolo_id INTEGER,
            data TEXT,
            km INTEGER,
            details TEXT,
            FOREIGN KEY(veicolo_id) REFERENCES veicoli(id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS scadenze (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veicolo_id INTEGER,
            tipo TEXT,
            data TEXT,
            FOREIGN KEY(veicolo_id) REFERENCES veicoli(id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS telegram_chats (
            chat_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            notifiche INTEGER DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )""")

        # Nuove colonne (migrazione non distruttiva)
        _add_column(c, "veicoli", "carburante TEXT")
        _add_column(c, "veicoli", "intervallo_km INTEGER DEFAULT 15000")
        _add_column(c, "veicoli", "intervallo_mesi INTEGER DEFAULT 12")
        _add_column(c, "veicoli", "note TEXT")
        _add_column(c, "manutenzioni", "tipo TEXT DEFAULT 'Tagliando'")
        _add_column(c, "manutenzioni", "costo REAL")
        _add_column(c, "manutenzioni", "officina TEXT")
        _add_column(c, "manutenzioni", "note TEXT")
        _add_column(c, "scadenze", "note TEXT")

        c.execute("CREATE INDEX IF NOT EXISTS idx_veicoli_owner ON veicoli(owner_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_manut_veicolo ON manutenzioni(veicolo_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_scad_veicolo ON scadenze(veicolo_id)")

        _ripara_manutenzioni_bot(c)

        # Superadmin di default
        if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            c.execute(
                """INSERT INTO users (username,password,profile_img,is_admin,
                   must_change_password,is_superadmin) VALUES (?,?,?,?,?,?)""",
                ("admin", generate_password_hash("admin"), None, 1, 1, 1),
            )


def _ripara_manutenzioni_bot(c):
    """
    Il vecchio bot salvava (tipo, data, note) nelle colonne (data, km, details).
    Riconosce quelle righe (km contiene una data) e le rimette a posto.
    """
    for r in c.execute("SELECT id, data, km, details FROM manutenzioni").fetchall():
        km_str = str(r["km"] or "")
        if _DATE_RE.match(km_str) and not _DATE_RE.match(str(r["data"] or "")):
            try:
                nota = json.loads(r["details"]) if r["details"] else ""
            except ValueError:
                nota = r["details"]
            nota = nota if isinstance(nota, str) else ""
            c.execute(
                "UPDATE manutenzioni SET data=?, km=NULL, details=?, tipo=?, note=? WHERE id=?",
                (km_str, json.dumps({}), r["data"] or "Altro", nota, r["id"]),
            )


# ──────────────────────────────────────────────────────────────────────────────
# UTENTI
# ──────────────────────────────────────────────────────────────────────────────
def get_user_by_username(username):
    with _conn() as conn:
        return _row(conn.execute("SELECT * FROM users WHERE username=?", (username,)))


def get_user_by_id(uid):
    with _conn() as conn:
        return _row(conn.execute("SELECT * FROM users WHERE id=?", (uid,)))


def get_users():
    with _conn() as conn:
        return _rows(conn.execute(
            """SELECT u.id, u.username, u.profile_img, u.is_admin,
                      u.must_change_password, u.is_superadmin,
                      (SELECT COUNT(*) FROM veicoli v WHERE v.owner_id=u.id) AS n_veicoli
               FROM users u ORDER BY u.username COLLATE NOCASE"""))


def add_user(username, password_hash, profile_img, is_admin, must_change_password=1):
    """Ritorna False se lo username esiste già."""
    try:
        with _conn() as conn:
            conn.execute(
                """INSERT INTO users (username,password,profile_img,is_admin,must_change_password)
                   VALUES (?,?,?,?,?)""",
                (username, password_hash, profile_img, is_admin, must_change_password),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def count_veicoli_utente(uid):
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM veicoli WHERE owner_id=?", (uid,)).fetchone()[0]


def delete_user(uid):
    """Non elimina il superadmin. Ritorna True se eliminato."""
    with _conn() as conn:
        row = _row(conn.execute("SELECT is_superadmin FROM users WHERE id=?", (uid,)))
        if not row or row["is_superadmin"]:
            return False
        conn.execute("DELETE FROM telegram_chats WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        return True


def aggiorna_utente(uid, username=None, password_hash=None,
                    profile_img=None, is_admin=None, must_change_password=None):
    """Aggiorna solo i campi non None. Ritorna False se lo username è già usato."""
    fields = {
        "username": username, "password": password_hash, "profile_img": profile_img,
        "is_admin": is_admin, "must_change_password": must_change_password,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return True
    sql = "UPDATE users SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?"
    try:
        with _conn() as conn:
            conn.execute(sql, (*updates.values(), uid))
        return True
    except sqlite3.IntegrityError:
        return False


def set_password(username, password_hash):
    """Cambia password e rimuove il flag di cambio obbligatorio."""
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET password=?, must_change_password=0 WHERE username=?",
            (password_hash, username),
        )


# ──────────────────────────────────────────────────────────────────────────────
# VEICOLI
# ──────────────────────────────────────────────────────────────────────────────
def _veicolo_dict(row):
    if not row:
        return None
    v = dict(row)
    try:
        v["immagini"] = json.loads(v.get("immagini") or "[]")
    except ValueError:
        v["immagini"] = []
    v["intervallo_km"] = v.get("intervallo_km") or 15000
    v["intervallo_mesi"] = v.get("intervallo_mesi") or 12
    return v


def get_veicoli(user_id=None, is_admin=False):
    """Tutti i veicoli (admin) o solo quelli dell'utente, ordinati per targa."""
    sql = """SELECT v.*, u.username AS owner_username FROM veicoli v
             LEFT JOIN users u ON u.id = v.owner_id"""
    with _conn() as conn:
        if is_admin:
            cur = conn.execute(sql + " ORDER BY v.targa COLLATE NOCASE")
        else:
            cur = conn.execute(sql + " WHERE v.owner_id=? ORDER BY v.targa COLLATE NOCASE",
                               (user_id,))
        return [_veicolo_dict(r) for r in cur.fetchall()]


def get_veicolo_by_id(vid, user_id=None, is_admin=False):
    """Veicolo come dict, oppure None se non esiste o l'utente non ha accesso."""
    sql = """SELECT v.*, u.username AS owner_username FROM veicoli v
             LEFT JOIN users u ON u.id = v.owner_id WHERE v.id=?"""
    with _conn() as conn:
        if is_admin:
            cur = conn.execute(sql, (vid,))
        else:
            cur = conn.execute(sql + " AND v.owner_id=?", (vid, user_id))
        return _veicolo_dict(cur.fetchone())


def aggiungi_veicolo(owner_id, nome, cognome, targa, marca, modello, anno, km,
                     imgs, lib, carburante=None, intervallo_km=None,
                     intervallo_mesi=None, note=None):
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO veicoli (owner_id,nome,cognome,targa,marca,modello,anno,km,
                   immagini,libretto,carburante,intervallo_km,intervallo_mesi,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (owner_id, nome, cognome, (targa or "").upper().replace(" ", ""), marca,
             modello, anno, _to_int(km), json.dumps(imgs or []), lib, carburante,
             _to_int(intervallo_km) or 15000, _to_int(intervallo_mesi) or 12, note),
        )
        return cur.lastrowid


def aggiorna_veicolo(vid, nome, cognome, targa, marca, modello, anno, km, imgs, lib,
                     carburante=None, intervallo_km=None, intervallo_mesi=None,
                     note=None, owner_id=None):
    with _conn() as conn:
        conn.execute(
            """UPDATE veicoli SET nome=?, cognome=?, targa=?, marca=?, modello=?,
                   anno=?, km=?, immagini=?, libretto=?, carburante=?,
                   intervallo_km=?, intervallo_mesi=?, note=? WHERE id=?""",
            (nome, cognome, (targa or "").upper().replace(" ", ""), marca, modello, anno,
             _to_int(km), json.dumps(imgs or []), lib, carburante,
             _to_int(intervallo_km) or 15000, _to_int(intervallo_mesi) or 12, note, vid),
        )
        if owner_id:
            conn.execute("UPDATE veicoli SET owner_id=? WHERE id=?", (owner_id, vid))


def aggiorna_km_se_maggiore(vid, km):
    """Aggiorna il contachilometri del veicolo se km è più alto di quello salvato."""
    km = _to_int(km)
    if km is None:
        return
    with _conn() as conn:
        conn.execute("UPDATE veicoli SET km=? WHERE id=? AND (km IS NULL OR km < ?)",
                     (km, vid, km))


def elimina_veicolo(vid):
    """Elimina veicolo con manutenzioni e scadenze collegate."""
    with _conn() as conn:
        conn.execute("DELETE FROM manutenzioni WHERE veicolo_id=?", (vid,))
        conn.execute("DELETE FROM scadenze WHERE veicolo_id=?", (vid,))
        conn.execute("DELETE FROM veicoli WHERE id=?", (vid,))


# ──────────────────────────────────────────────────────────────────────────────
# MANUTENZIONI
# ──────────────────────────────────────────────────────────────────────────────
def _manut_dict(row):
    m = dict(row)
    try:
        d = json.loads(m.get("details") or "{}")
    except ValueError:
        d = {}
    m["details"] = d if isinstance(d, dict) else {}
    m["tipo"] = m.get("tipo") or "Tagliando"
    return m


def get_manutenzioni(vid):
    """Manutenzioni del veicolo, dalla più recente."""
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM manutenzioni WHERE veicolo_id=? ORDER BY data DESC, id DESC", (vid,))
        return [_manut_dict(r) for r in cur.fetchall()]


def get_manutenzione(mid, vid):
    with _conn() as conn:
        r = conn.execute("SELECT * FROM manutenzioni WHERE id=? AND veicolo_id=?",
                         (mid, vid)).fetchone()
        return _manut_dict(r) if r else None


def aggiungi_manutenzione(vid, data, km, details, tipo="Tagliando", costo=None,
                          officina=None, note=None):
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO manutenzioni (veicolo_id,data,km,details,tipo,costo,officina,note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (vid, data, _to_int(km), json.dumps(details or {}), tipo or "Tagliando",
             _to_float(costo), officina, note),
        )
        mid = cur.lastrowid
    aggiorna_km_se_maggiore(vid, km)
    return mid


def aggiorna_manutenzione(mid, vid, data, km, details, tipo, costo, officina, note):
    with _conn() as conn:
        conn.execute(
            """UPDATE manutenzioni SET data=?, km=?, details=?, tipo=?, costo=?,
                   officina=?, note=? WHERE id=? AND veicolo_id=?""",
            (data, _to_int(km), json.dumps(details or {}), tipo, _to_float(costo),
             officina, note, mid, vid),
        )
    aggiorna_km_se_maggiore(vid, km)


def elimina_manutenzione(mid, vid):
    with _conn() as conn:
        conn.execute("DELETE FROM manutenzioni WHERE id=? AND veicolo_id=?", (mid, vid))


# ──────────────────────────────────────────────────────────────────────────────
# SCADENZE
# ──────────────────────────────────────────────────────────────────────────────
def get_scadenze(vid):
    """Scadenze del veicolo, dalla più vicina."""
    with _conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM scadenze WHERE veicolo_id=? ORDER BY data ASC", (vid,)))


def get_scadenza(sid, vid):
    with _conn() as conn:
        return _row(conn.execute("SELECT * FROM scadenze WHERE id=? AND veicolo_id=?",
                                 (sid, vid)))


def aggiungi_scadenza(vid, tipo, data, note=None):
    with _conn() as conn:
        return conn.execute(
            "INSERT INTO scadenze (veicolo_id,tipo,data,note) VALUES (?,?,?,?)",
            (vid, tipo, data, note)).lastrowid


def aggiorna_scadenza(sid, vid, tipo, data, note=None):
    with _conn() as conn:
        conn.execute("UPDATE scadenze SET tipo=?, data=?, note=? WHERE id=? AND veicolo_id=?",
                     (tipo, data, note, sid, vid))


def elimina_scadenza(sid, vid):
    with _conn() as conn:
        conn.execute("DELETE FROM scadenze WHERE id=? AND veicolo_id=?", (sid, vid))


# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ──────────────────────────────────────────────────────────────────────────────
def tg_login(chat_id, user_id):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO telegram_chats (chat_id,user_id,notifiche) VALUES (?,?,1)
               ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id""",
            (chat_id, user_id))


def tg_logout(chat_id):
    with _conn() as conn:
        return conn.execute("DELETE FROM telegram_chats WHERE chat_id=?",
                            (chat_id,)).rowcount > 0


def tg_session(chat_id):
    """Sessione Telegram (chat_id → utente) oppure None."""
    with _conn() as conn:
        return _row(conn.execute(
            """SELECT t.chat_id, t.notifiche, u.id AS user_id, u.username, u.is_admin
               FROM telegram_chats t JOIN users u ON u.id = t.user_id
               WHERE t.chat_id=?""", (chat_id,)))


def tg_set_notifiche(chat_id, attive):
    with _conn() as conn:
        conn.execute("UPDATE telegram_chats SET notifiche=? WHERE chat_id=?",
                     (1 if attive else 0, chat_id))


def tg_chats_notifiche():
    with _conn() as conn:
        return _rows(conn.execute(
            """SELECT t.chat_id, u.id AS user_id, u.username, u.is_admin
               FROM telegram_chats t JOIN users u ON u.id = t.user_id
               WHERE t.notifiche=1"""))


# ──────────────────────────────────────────────────────────────────────────────
# CALCOLI: STATO SCADENZE E PROSSIMO TAGLIANDO
# ──────────────────────────────────────────────────────────────────────────────
GIORNI_PREAVVISO = 30
KM_PREAVVISO = 1000


def parse_date(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def add_months(d, months):
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    giorni = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28,
              31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return d.replace(year=y, month=m, day=min(d.day, giorni))


def stato_data(d, oggi=None):
    """'scaduta' | 'imminente' | 'ok' | None per una data."""
    oggi = oggi or date.today()
    if not d:
        return None
    if d < oggi:
        return "scaduta"
    if (d - oggi).days <= GIORNI_PREAVVISO:
        return "imminente"
    return "ok"


def prossimo_tagliando(veicolo, manutenzioni, oggi=None):
    """
    Stima il prossimo tagliando dall'ultimo intervento di tipo 'Tagliando'
    usando gli intervalli del veicolo (km e mesi, vale il primo raggiunto).
    """
    oggi = oggi or date.today()
    ultimi = [m for m in manutenzioni if m.get("tipo", "Tagliando") == "Tagliando"
              and parse_date(m.get("data"))]
    if not ultimi:
        return None
    ultimo = max(ultimi, key=lambda m: m["data"])
    d_ult = parse_date(ultimo["data"])
    int_km = veicolo.get("intervallo_km") or 15000
    int_mesi = veicolo.get("intervallo_mesi") or 12
    data_pross = add_months(d_ult, int_mesi)
    km_pross = (ultimo["km"] + int_km) if isinstance(ultimo.get("km"), int) else None
    km_attuali = veicolo.get("km") if isinstance(veicolo.get("km"), int) else None
    km_mancanti = (km_pross - km_attuali) if (km_pross is not None and km_attuali is not None) else None

    stato = stato_data(data_pross, oggi)
    if km_mancanti is not None:
        if km_mancanti <= 0:
            stato = "scaduta"
        elif km_mancanti <= KM_PREAVVISO and stato == "ok":
            stato = "imminente"
    # Quanto dell'intervallo è già stato consumato (0–100), il maggiore tra tempo e km
    giorni_tot = max((data_pross - d_ult).days, 1)
    pct = (oggi - d_ult).days / giorni_tot * 100
    if km_mancanti is not None:
        pct = max(pct, (int_km - km_mancanti) / int_km * 100)
    return {
        "ultimo": ultimo,
        "percentuale": int(min(max(pct, 0), 100)),
        "data": data_pross.isoformat(),
        "giorni": (data_pross - oggi).days,
        "km": km_pross,
        "km_mancanti": km_mancanti,
        "stato": stato,
    }

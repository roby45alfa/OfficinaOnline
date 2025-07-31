import sqlite3
from werkzeug.security import generate_password_hash
import json
DB='database.db'

def get_db():
    conn=sqlite3.connect(DB)
    conn.row_factory=sqlite3.Row
    return conn

def init_db():
    conn=get_db()
    c=conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        profile_img TEXT,
        is_admin INTEGER,
        must_change_password INTEGER DEFAULT 1
    )""")
    # default admin, password 'admin', force change
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        hashed=generate_password_hash('admin')
        c.execute("INSERT INTO users (username,password,profile_img,is_admin,must_change_password) VALUES (?,?,?,?,?)",
                  ('admin', hashed, None, 1, 1))
    c.execute("""CREATE TABLE IF NOT EXISTS veicoli (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        nome TEXT, cognome TEXT, targa TEXT, marca TEXT,
        modello TEXT, anno TEXT, km INTEGER,
        immagini TEXT, libretto TEXT,
        FOREIGN KEY(owner_id) REFERENCES users(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS manutenzioni (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        veicolo_id INTEGER,
        data TEXT, km INTEGER, details TEXT,
        FOREIGN KEY(veicolo_id) REFERENCES veicoli(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS scadenze (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        veicolo_id INTEGER,
        tipo TEXT, data TEXT,
        FOREIGN KEY(veicolo_id) REFERENCES veicoli(id)
    )""")
    conn.commit()
    conn.close()

# user management
def get_user_by_username(username):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    u=c.fetchone(); conn.close(); return u

def get_users():
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT id, username, profile_img, is_admin, must_change_password FROM users")
    rows=c.fetchall(); conn.close(); return rows

def add_user(username, password_hash, profile_img, is_admin, must_change_password=1):
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT INTO users (username, password, profile_img, is_admin, must_change_password) VALUES (?,?,?,?,?)",
              (username, password_hash, profile_img, is_admin, must_change_password))
    conn.commit(); conn.close()

def delete_user(uid):
    conn=get_db(); c=conn.cursor()
    c.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit(); conn.close()

def set_password(username, password_hash):
    conn=get_db(); c=conn.cursor()
    c.execute("UPDATE users SET password=?, must_change_password=0 WHERE username=?", (password_hash, username))
    conn.commit(); conn.close()

# vehicle / maintenance / deadlines
def get_veicoli(user_id=None, is_admin=False):
    conn=get_db(); c=conn.cursor()
    if is_admin:
        c.execute("SELECT * FROM veicoli")
    else:
        c.execute("SELECT * FROM veicoli WHERE owner_id=?", (user_id,))
    rows=c.fetchall(); conn.close(); return rows

def get_veicolo_by_id(vid, user_id=None, is_admin=False):
    conn=get_db(); c=conn.cursor()
    if is_admin:
        c.execute("SELECT * FROM veicoli WHERE id=?", (vid,))
    else:
        c.execute("SELECT * FROM veicoli WHERE id=? AND owner_id=?", (vid, user_id))
    row=c.fetchone(); conn.close()
    if not row: return None
    immagini=json.loads(row['immagini']) if row['immagini'] else []
    return {'id':row['id'],'nome':row['nome'],'cognome':row['cognome'],'targa':row['targa'],
            'marca':row['marca'],'modello':row['modello'],'anno':row['anno'],'km':row['km'],
            'immagini':immagini,'libretto':row['libretto']}

def aggiungi_veicolo(owner_id, nome, cognome, targa, marca, modello, anno, km, imgs, lib):
    conn=get_db(); c=conn.cursor()
    c.execute("""INSERT INTO veicoli (owner_id,nome,cognome,targa,marca,modello,anno,km,immagini,libretto)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (owner_id, nome, cognome, targa, marca, modello, anno, km, json.dumps(imgs), lib))
    conn.commit(); conn.close()

def aggiorna_veicolo(vid, nome, cognome, targa, marca, modello, anno, km, imgs, lib):
    conn=get_db(); c=conn.cursor()
    c.execute("""UPDATE veicoli SET nome=?,cognome=?,targa=?,marca=?,modello=?,anno=?,km=?,immagini=?,libretto=?
                 WHERE id=?""",
              (nome, cognome, targa, marca, modello, anno, km, json.dumps(imgs), lib, vid))
    conn.commit(); conn.close()

def aggiungi_manutenzione(vid, data, km, details):
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT INTO manutenzioni (veicolo_id,data,km,details) VALUES (?,?,?,?)",
              (vid, data, km, json.dumps(details)))
    conn.commit(); conn.close()

def get_manutenzioni(vid):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT * FROM manutenzioni WHERE veicolo_id=? ORDER BY data DESC", (vid,))
    rows=c.fetchall(); conn.close(); return rows

def aggiungi_scadenza(vid, tipo, data):
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT INTO scadenze (veicolo_id,tipo,data) VALUES (?,?,?)", (vid, tipo, data))
    conn.commit(); conn.close()

def get_scadenze(vid):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT * FROM scadenze WHERE veicolo_id=? ORDER BY data ASC", (vid,))
    rows=c.fetchall(); conn.close(); return rows

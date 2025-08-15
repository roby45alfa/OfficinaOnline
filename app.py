"""
File: app.py
Modulo Flask per la gestione dell'officina:
  - Autenticazione utenti
  - Gestione CRUD di veicoli, manutenzioni, scadenze
  - Upload di immagini e PDF (libretti)
  - Amministrazione utenti
"""

# ──────────────────────────────────────────────────────────────────────────────
# IMPORTAZIONI
# ──────────────────────────────────────────────────────────────────────────────
from flask import (
    Flask, render_template, request, redirect, session,
    url_for, send_from_directory, abort
)
import os
from werkzeug.utils import secure_filename
import json
from werkzeug.security import generate_password_hash, check_password_hash

# Import funzioni database
from db import (
    init_db,
    get_user_by_username, get_user_by_id, get_users,
    add_user, delete_user, aggiorna_utente, set_password,
    get_veicoli, get_veicolo_by_id,
    aggiungi_veicolo, aggiorna_veicolo, elimina_veicolo,
    aggiungi_manutenzione, get_manutenzioni,
    aggiungi_scadenza, get_scadenze
)

# ──────────────────────────────────────────────────────────────────────────────
# MIDDLEWARE PER PREFISSO
# ──────────────────────────────────────────────────────────────────────────────
class PrefixMiddleware(object):
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        if environ['PATH_INFO'].startswith(self.prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][len(self.prefix):] or '/'
            environ['SCRIPT_NAME'] = self.prefix
            return self.app(environ, start_response)
        else:
            start_response('404', [('Content-Type', 'text/plain')])
            return [b'Not Found']

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAZIONE APP
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = 'supersecretfinal'
app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix='/gestioneveicoli')

# Cartelle upload
UPLOAD_FOLDER = 'static/uploads'
PDF_FOLDER    = 'static/libretti'
PROFILE_FOLDER= 'static/profiles'
for d in (UPLOAD_FOLDER, PDF_FOLDER, PROFILE_FOLDER):
    os.makedirs(d, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# HOOK PRIMA DI OGNI RICHIESTA
# ──────────────────────────────────────────────────────────────────────────────
@app.before_request
def load_user():
    username = session.get('username')
    if username:
        user = get_user_by_username(username)
        if not user:
            session.clear()
        else:
            session['is_admin']      = user['is_admin']
            session['profile_img']   = user['profile_img']
            session['user_id']       = user['id']
            session['must_change']   = user['must_change_password']
            session['is_superadmin'] = user['is_superadmin']

# ──────────────────────────────────────────────────────────────────────────────
# INJECTION NEI TEMPLATE
# ──────────────────────────────────────────────────────────────────────────────
@app.context_processor
def inject_user():
    return dict(
        logged_user=session.get('username'),
        profile_img=session.get('profile_img')
    )

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE LOGIN / LOGOUT / CHANGE PASSWORD
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET','POST'])
def login():
    error = False
    if request.method == 'POST':
        user = get_user_by_username(request.form.get('username',''))
        if user:
            pw = request.form.get('password','')
            if check_password_hash(user['password'], pw):
                session['username'] = user['username']
                if user['must_change_password']:
                    return redirect(url_for('change_password'))
                return redirect(url_for('dashboard'))
        error = True
    return render_template('login.html', error=error)

@app.route('/change_password', methods=['GET','POST'])
def change_password():
    if not session.get('username'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        new_pw = request.form.get('password','').strip()
        if not new_pw:
            return render_template('change_password.html', error='Serve una password')
        hashed = generate_password_hash(new_pw)
        set_password(session['username'], hashed)
        return redirect(url_for('dashboard'))
    return render_template('change_password.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE DASHBOARD
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if not session.get('username'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    user_id  = session.get('user_id')
    veicoli = get_veicoli(user_id, is_admin=is_admin)

    last_m = []
    due_s  = []
    for v in veicoli:
        vid = v['id'] if isinstance(v, dict) else v[0]
        for m in get_manutenzioni(vid):
            last_m.append({'veicolo_id': vid, 'data': m[2], 'km': m[3]})
        for s in get_scadenze(vid):
            due_s.append({'veicolo_id': vid, 'tipo': s[2], 'data': s[3]})
    return render_template('dashboard.html', veicoli=veicoli, last_m=last_m, due_s=due_s)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE VEICOLI
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/veicoli')
def veicoli():
    if not session.get('username'):
        return redirect(url_for('login'))
    veic = get_veicoli(session.get('user_id'), is_admin=session.get('is_admin'))
    return render_template('veicoli.html', veicoli=veic)

@app.route('/aggiungi_veicolo', methods=['GET','POST'])
def aggiungi_veicolo_route():
    if not session.get('username'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        data = request.form
        imgs = []
        for f in request.files.getlist('immagini'):
            if f and f.filename:
                fn = secure_filename(f.filename)
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                imgs.append(fn)
        lib = None
        lf = request.files.get('libretto')
        if lf and lf.filename:
            fn = secure_filename(lf.filename)
            lf.save(os.path.join(PDF_FOLDER, fn))
            lib = fn
        aggiungi_veicolo(session.get('user_id'),
                         data.get('nome'), data.get('cognome'),
                         data.get('targa'), data.get('marca'),
                         data.get('modello'), data.get('anno'),
                         data.get('km'), imgs, lib)
        return redirect(url_for('veicoli'))
    return render_template('aggiungi_veicolo.html')

@app.route('/veicolo/<int:vid>')
def dettaglio_veicolo(vid):
    if not session.get('username'):
        return redirect(url_for('login'))
    v = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    if not v:
        abort(404)

    raw_manut = get_manutenzioni(vid)
    manut = []
    for m in raw_manut:
        details = json.loads(m[4]) if m[4] else {}
        manut.append({'id': m[0], 'data': m[2], 'km': m[3], 'details': details})

    scans = get_scadenze(vid)
    return render_template('veicolo.html', veicolo=v, manut=manut, scans=scans)

@app.route('/modifica_veicolo/<int:vid>', methods=['GET','POST'])
def mod_v(vid):
    if not session.get('username'):
        return redirect(url_for('login'))
    ve = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    if not ve:
        abort(404)

    if request.method == 'POST':
        data = request.form
        imgs = ve.get('immagini', [])[:]
        for f in request.files.getlist('immagini'):
            if f and f.filename:
                fn = secure_filename(f.filename)
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                imgs.append(fn)
        lib = ve.get('libretto')
        lf  = request.files.get('libretto')
        if lf and lf.filename:
            fn = secure_filename(lf.filename)
            lf.save(os.path.join(PDF_FOLDER, fn))
            lib = fn
        aggiorna_veicolo(vid,
                         data.get('nome'), data.get('cognome'),
                         data.get('targa'), data.get('marca'),
                         data.get('modello'), data.get('anno'),
                         data.get('km'), imgs, lib)
        return redirect(url_for('dettaglio_veicolo', vid=vid))
    return render_template('modifica_veicolo.html', veicolo=ve)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE MANUTENZIONE / SCADENZE
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/manutenzione/<int:vid>', methods=['GET','POST'])
def manutenzione(vid):
    if not session.get('username'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        data = request.form
        details = {}
        fields = [
            'filtro_olio','filtro_gasolio','filtro_aria','filtro_abitacolo',
            'olio','past_ant','past_post','dischi_ant','dischi_post'
        ]
        for f in fields:
            if data.get(f):
                details[f] = {'cod': data.get(f'cod_{f}'), 'marca': data.get(f'mar_{f}')}
        details['olio_tipo'] = data.get('olio_tipo')
        aggiungi_manutenzione(vid, data.get('data'), data.get('km_man'), details)
        return redirect(url_for('dettaglio_veicolo', vid=vid))

    ve = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    return render_template('manutenzione.html', veicolo=ve)

@app.route('/scadenze/<int:vid>', methods=['GET','POST'])
def scadenze(vid):
    if not session.get('username'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        aggiungi_scadenza(vid, request.form.get('tipo'), request.form.get('data_sc'))
        return redirect(url_for('dettaglio_veicolo', vid=vid))
    ve = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    sc = get_scadenze(vid)
    return render_template('scadenze.html', veicolo=ve, scadenze=sc)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE UTENTI (ADMIN)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/users')
def users():
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))
    users = get_users()
    return render_template('users.html', users=users)

@app.route('/users/add', methods=['GET','POST'])
def users_add():
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        uname = request.form.get('username')
        pwd   = request.form.get('password')
        is_admin_flag = 1 if request.form.get('is_admin') else 0
        img = None
        f = request.files.get('profile_img')
        if f and f.filename:
            fn = secure_filename(f.filename)
            f.save(os.path.join(PROFILE_FOLDER, fn))
            img = fn
        hashed = generate_password_hash(pwd)
        add_user(uname, hashed, img, is_admin_flag, must_change_password=1)
        return redirect(url_for('users'))
    return render_template('add_user.html')

@app.route('/users/edit/<int:uid>', methods=['GET','POST'])
def users_edit(uid):
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))
    user_to_edit = get_user_by_id(uid)
    if not user_to_edit:
        abort(404)
    if user_to_edit['is_superadmin'] and session.get('username') != user_to_edit['username']:
        return "Non puoi modificare l'admin principale", 403

    if request.method == 'POST':
        new_username = request.form.get('username')
        new_password = request.form.get('password')
        is_admin_flag= 1 if request.form.get('is_admin') else 0
        force_change  = 1 if request.form.get('force_change') else 0
        profile_img = user_to_edit['profile_img']
        f = request.files.get('profile_img')
        if f and f.filename:
            fn = secure_filename(f.filename)
            f.save(os.path.join(PROFILE_FOLDER, fn))
            profile_img = fn
        password_hash = None
        if new_password:
            password_hash = generate_password_hash(new_password)
        if user_to_edit['is_superadmin'] and session.get('username') != user_to_edit['username']:
            is_admin_flag = 1
        aggiorna_utente(uid, username=new_username, password_hash=password_hash,
                        profile_img=profile_img, is_admin=is_admin_flag,
                        must_change_password=force_change)
        return redirect(url_for('users'))
    return render_template('edit_user.html', user=user_to_edit)

@app.route('/users/delete/<int:uid>')
def users_delete(uid):
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))
    delete_user(uid)
    return redirect(url_for('users'))

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE FILE
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/uploads/<path:fn>')
def uploads(fn):
    return send_from_directory(UPLOAD_FOLDER, fn)

@app.route('/libretti/<path:fn>')
def download_libretto(fn):
    return send_from_directory(PDF_FOLDER, fn, as_attachment=True)

@app.route('/veicolo/delete/<int:vid>', methods=['POST'])
def elimina_veicolo_route(vid):
    if not session.get('username'):
        return redirect(url_for('login'))
    v = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    if not v:
        abort(404)
    elimina_veicolo(vid)
    return redirect(url_for('veicoli'))

# ──────────────────────────────────────────────────────────────────────────────
# AVVIO
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)

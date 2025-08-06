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
    Flask,               # Oggetto applicazione Flask
    render_template,     # Rendering dei template Jinja2
    request,             # Accesso a dati POST/GET
    redirect,            # Redirect HTTP
    session,             # Sessione utente
    url_for,             # Generazione URL per endpoint
    send_from_directory, # Invio file da cartella locale
    abort                # Genera errori HTTP (es. 404)
)
import os                  # Operazioni sul filesystem
from werkzeug.utils import secure_filename    # Sanitizzazione nomi file upload
import json                # Parsing JSON per i dettagli manutenzione
from werkzeug.security import (
    generate_password_hash, # Hashing password
    check_password_hash     # Verifica hash password
)

# Import delle funzioni di accesso al database
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
# CONFIGURAZIONE APPLICAZIONE
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)               # Istanzia l’applicazione Flask
app.secret_key = 'supersecretfinal' # Chiave segreta per sessioni

# Cartelle per l’upload di immagini, libretti e profili utente
UPLOAD_FOLDER = 'static/uploads'
PDF_FOLDER    = 'static/libretti'
PROFILE_FOLDER= 'static/profiles'
# Assicura che esistano
for d in (UPLOAD_FOLDER, PDF_FOLDER, PROFILE_FOLDER):
    os.makedirs(d, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# HOOK PRIMA DI OGNI RICHIESTA
# ──────────────────────────────────────────────────────────────────────────────
@app.before_request
def load_user():
    """
    Carica nei dati di sessione le informazioni utente.
    Viene eseguito prima di ogni richiesta Flask.
    """
    username = session.get('username')
    if username:
        user = get_user_by_username(username)
        if not user:
            # Se l’utente non esiste più, cancella la sessione
            session.clear()
        else:
            # Memorizza in sessione flag di amministrazione e ID
            session['is_admin']     = user['is_admin']
            session['profile_img']  = user['profile_img']
            session['user_id']      = user['id']
            session['must_change']  = user['must_change_password']
            session['is_superadmin']= user['is_superadmin']

# ──────────────────────────────────────────────────────────────────────────────
# INJECTION NEL CONTEXTO TEMPLATES
# ──────────────────────────────────────────────────────────────────────────────
@app.context_processor
def inject_user():
    """
    Rende disponibili in tutti i template:
      - logged_user: username corrente
      - profile_img: nome file immagine profilo
    """
    return dict(
        logged_user = session.get('username'),
        profile_img  = session.get('profile_img')
    )

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: LOGIN / CHANGE PASSWORD / LOGOUT
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET','POST'])
def login():
    """
    Pagina di login.
    GET: mostra form
    POST: verifica credenziali, imposta sessione, reindirizza
    """
    error = False
    if request.method == 'POST':
        # Estrai utente dal DB
        user = get_user_by_username(request.form.get('username',''))
        if user:
            pw = request.form.get('password','')
            # Verifica hash password
            if check_password_hash(user['password'], pw):
                session['username'] = user['username']
                # Se deve cambiare password, reindirizza
                if user['must_change_password']:
                    return redirect(url_for('change_password'))
                return redirect(url_for('dashboard'))
        error = True
    # Mostra template con eventuale flag error
    return render_template('login.html', error=error)

@app.route('/change_password', methods=['GET','POST'])
def change_password():
    """
    Form per cambio password on first login.
    Richiede utente loggato e non reindirizza al dashboard finché non cambiano password.
    """
    if not session.get('username'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        new_pw = request.form.get('password','').strip()
        if not new_pw:
            # Messaggio di errore se password vuota
            return render_template('change_password.html', error='Serve una password')
        # Hash e update nel DB
        hashed = generate_password_hash(new_pw)
        set_password(session['username'], hashed)
        return redirect(url_for('dashboard'))
    return render_template('change_password.html')

@app.route('/logout')
def logout():
    """Svuota la sessione e torna al login."""
    session.clear()
    return redirect(url_for('login'))

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: DASHBOARD
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    """
    Cruscotto iniziale dopo il login.
    Mostra:
      - Elenco veicoli
      - Ultima manutenzione per veicolo
      - Prossime scadenze
    """
    if not session.get('username'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    user_id  = session.get('user_id')
    # Recupera veicoli visibili
    veicoli = get_veicoli(user_id, is_admin=is_admin)

    # Costruisce liste di report manutenzioni/scadenze
    last_m = []
    due_s  = []
    for v in veicoli:
        # Estrae ID veicolo (dict o tuple)
        vid = v['id'] if isinstance(v, dict) else v[0]
        # Ultime manutenzioni
        for m in get_manutenzioni(vid):
            last_m.append({
                'veicolo_id': vid,
                'data':       m[2],  # campo data
                'km':         m[3]   # campo chilometraggio
            })
        # Prossime scadenze
        for s in get_scadenze(vid):
            due_s.append({
                'veicolo_id': vid,
                'tipo':       s[2],  # descrizione scadenza
                'data':       s[3]   # data scadenza
            })
    return render_template('dashboard.html',
                           veicoli=veicoli,
                           last_m=last_m,
                           due_s=due_s)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: LISTA VEICOLI
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/veicoli')
def veicoli():
    """
    Elenco completo dei veicoli dell’utente o di tutti se admin.
    Mostra link per dettagli e azioni CRUD.
    """
    if not session.get('username'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    user_id  = session.get('user_id')
    veic = get_veicoli(user_id, is_admin=is_admin)
    return render_template('veicoli.html', veicoli=veic)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: AGGIUNGI VEICOLO
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/aggiungi_veicolo', methods=['GET','POST'])
def aggiungi_veicolo_route():
    """
    Form e logica per aggiungere un nuovo veicolo.
    Gestisce upload multipli di immagini e libretto PDF.
    """
    if not session.get('username'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        data = request.form
        imgs = []
        # Cicla tra tutti i file immagine caricati
        for f in request.files.getlist('immagini'):
            if f and f.filename:
                fn = secure_filename(f.filename)
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                imgs.append(fn)
        # Gestione libretto
        lib = None
        lf = request.files.get('libretto')
        if lf and lf.filename:
            fn = secure_filename(lf.filename)
            lf.save(os.path.join(PDF_FOLDER, fn))
            lib = fn
        owner_id = session.get('user_id')
        # Chiamata al DB per inserire veicolo
        aggiungi_veicolo(owner_id,
                         data.get('nome'), data.get('cognome'),
                         data.get('targa'), data.get('marca'),
                         data.get('modello'), data.get('anno'),
                         data.get('km'), imgs, lib)
        return redirect(url_for('veicoli'))

    # GET: mostra form vuoto
    return render_template('aggiungi_veicolo.html')

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: DETTAGLIO VEICOLO
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/veicolo/<int:vid>')
def dettaglio_veicolo(vid):
    """
    Mostra pagina dettaglio per un veicolo:
      - Informazioni base
      - Elenco manutenzioni con dettagli JSON decodificati
      - Elenco scadenze
    """
    if not session.get('username'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    user_id  = session.get('user_id')
    # Recupera il veicolo o 404
    v = get_veicolo_by_id(vid, user_id, is_admin=is_admin)
    if not v:
        abort(404)

    # Decodifica dettagli JSON di ogni manutenzione
    raw_manut = get_manutenzioni(vid)
    manut = []
    for m in raw_manut:
        details = json.loads(m[4]) if m[4] else {}
        manut.append({
            'id':      m[0],
            'data':    m[2],
            'km':      m[3],
            'details': details
        })

    # Recupera scadenze
    scans = get_scadenze(vid)
    return render_template('veicolo.html',
                           veicolo=v,
                           manut=manut,
                           scans=scans)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: MODIFICA VEICOLO
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/modifica_veicolo/<int:vid>', methods=['GET','POST'])
def mod_v(vid):
    """
    Form e logica per modificare un veicolo esistente.
    Mantiene le immagini esistenti e aggiunge eventuali nuove.
    """
    if not session.get('username'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    user_id  = session.get('user_id')
    ve       = get_veicolo_by_id(vid, user_id, is_admin=is_admin)
    if not ve:
        abort(404)

    if request.method == 'POST':
        data = request.form
        imgs = ve.get('immagini', [])[:]  # copia lista esistente
        # Nuove immagini
        for f in request.files.getlist('immagini'):
            if f and f.filename:
                fn = secure_filename(f.filename)
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                imgs.append(fn)
        # Nuovo libretto (se caricato)
        lib = ve.get('libretto')
        lf  = request.files.get('libretto')
        if lf and lf.filename:
            fn = secure_filename(lf.filename)
            lf.save(os.path.join(PDF_FOLDER, fn))
            lib = fn

        # Aggiorna record veicolo
        aggiorna_veicolo(vid,
                         data.get('nome'), data.get('cognome'),
                         data.get('targa'), data.get('marca'),
                         data.get('modello'), data.get('anno'),
                         data.get('km'), imgs, lib)
        return redirect(url_for('dettaglio_veicolo', vid=vid))

    # GET: mostra form precompilato
    return render_template('modifica_veicolo.html', veicolo=ve)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: MANUTENZIONE (INSERIMENTO)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/manutenzione/<int:vid>', methods=['GET','POST'])
def manutenzione(vid):
    """
    Aggiunge una nuova manutenzione per il veicolo vid.
    I dettagli (filtri, pastiglie, olio) vengono raccolti in un dict JSON.
    """
    if not session.get('username'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        data = request.form
        details = {}
        # Lista campi checkbox per filtri e ricambi
        fields = [
            'filtro_olio','filtro_gasolio','filtro_aria','filtro_abitacolo',
            'olio','past_ant','past_post','dischi_ant','dischi_post'
        ]
        # Costruisce dizionario details solo per campi selezionati
        for f in fields:
            if data.get(f):
                details[f] = {
                    'cod':   data.get(f'cod_{f}'),
                    'marca': data.get(f'mar_{f}')
                }
        # Tipo olio (campo testo)
        details['olio_tipo'] = data.get('olio_tipo')
        # Inserisce in DB
        aggiungi_manutenzione(vid,
                              data.get('data'),
                              data.get('km_man'),
                              details)
        return redirect(url_for('dettaglio_veicolo', vid=vid))

    # GET: mostra form manutenzione
    ve = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    return render_template('manutenzione.html', veicolo=ve)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: SCADENZE (INSERIMENTO)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/scadenze/<int:vid>', methods=['GET','POST'])
def scadenze(vid):
    """
    Aggiunge una nuova scadenza (tipo + data) per il veicolo vid.
    """
    if not session.get('username'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        aggiungi_scadenza(vid,
                          request.form.get('tipo'),
                          request.form.get('data_sc'))
        return redirect(url_for('dettaglio_veicolo', vid=vid))

    ve = get_veicolo_by_id(vid, session.get('user_id'), is_admin=session.get('is_admin'))
    sc = get_scadenze(vid)
    return render_template('scadenze.html', veicolo=ve, scadenze=sc)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTE: GESTIONE UTENTI (ADMIN)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/users')
def users():
    """
    Elenco degli utenti (solo admin).
    """
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))
    users = get_users()
    return render_template('users.html', users=users)

@app.route('/users/add', methods=['GET','POST'])
def users_add():
    """
    Aggiunge un nuovo utente (solo admin).
    Gestisce upload immagine profilo e hashing password.
    """
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
    """
    Modifica dati di un utente esistente (solo admin).
    Impedisce modifca admin principale da parte di altri.
    """
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))
    user_to_edit = get_user_by_id(uid)
    if not user_to_edit:
        abort(404)
    # Restrizione superadmin
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

        # Se modifico superadmin, mantieni flag admin
        if user_to_edit['is_superadmin'] and session.get('username') != user_to_edit['username']:
            is_admin_flag = 1

        aggiorna_utente(uid,
                        username=new_username,
                        password_hash=password_hash,
                        profile_img=profile_img,
                        is_admin=is_admin_flag,
                        must_change_password=force_change)
        return redirect(url_for('users'))

    return render_template('edit_user.html', user=user_to_edit)

@app.route('/users/delete/<int:uid>')
def users_delete(uid):
    """
    Elimina un utente (solo admin).
    """
    if not session.get('username') or not session.get('is_admin'):
        return redirect(url_for('login'))
    delete_user(uid)
    return redirect(url_for('users'))

# ──────────────────────────────────────────────────────────────────────────────
# ALTRI ENDPOINT DI SUPPORTO FILE
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/uploads/<path:fn>')
def uploads(fn):
    """Serve immagini dal folder UPLOAD_FOLDER."""
    return send_from_directory(UPLOAD_FOLDER, fn)

@app.route('/libretti/<path:fn>')
def download_libretto(fn):
    """Serve libretti PDF come allegato."""
    return send_from_directory(PDF_FOLDER, fn, as_attachment=True)

@app.route('/veicolo/delete/<int:vid>', methods=['POST'])
def elimina_veicolo_route(vid):
    """
    Elimina un veicolo (solo proprietario o admin).
    """
    if not session.get('username'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    user_id  = session.get('user_id')
    v = get_veicolo_by_id(vid, user_id, is_admin=is_admin)
    if not v:
        abort(404)
    elimina_veicolo(vid)
    return redirect(url_for('veicoli'))

# ──────────────────────────────────────────────────────────────────────────────
# AVVIO APPLICAZIONE
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()  # Inizializza il database (crea tabelle se mancanti)
    app.run(debug=True, host='0.0.0.0', port=5000)

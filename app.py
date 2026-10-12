"""
File: app.py
OfficinaOnline – gestione veicoli, tagliandi e scadenze (Flask).

  - Autenticazione con ruoli (utente / admin / superadmin) e cambio password obbligatorio
  - Veicoli con foto e libretto, storico interventi, scadenze, stima del prossimo tagliando
  - Protezione CSRF, controllo proprietà su ogni veicolo e file, upload con nomi univoci
"""

import csv
import io
import os
import secrets
import time
import uuid
from datetime import date, timedelta
from functools import wraps

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import db
from db import (
    aggiorna_manutenzione, aggiorna_scadenza, aggiorna_utente, aggiorna_veicolo,
    aggiungi_manutenzione, aggiungi_scadenza, aggiungi_veicolo, add_months, add_user,
    count_veicoli_utente, delete_user, elimina_manutenzione, elimina_scadenza,
    elimina_veicolo, get_manutenzione, get_manutenzioni, get_scadenza, get_scadenze,
    get_user_by_id, get_user_by_username, get_users, get_veicoli, get_veicolo_by_id,
    init_db, parse_date, prossimo_tagliando, set_password, stato_data,
)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAZIONE
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URL_PREFIX = os.environ.get("URL_PREFIX", "/gestioneveicoli").rstrip("/")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
PDF_FOLDER = os.path.join(BASE_DIR, "static", "libretti")
PROFILE_FOLDER = os.path.join(BASE_DIR, "static", "profiles")
for _d in (UPLOAD_FOLDER, PDF_FOLDER, PROFILE_FOLDER):
    os.makedirs(_d, exist_ok=True)

IMG_EXT = {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif"}
LIBRETTO_EXT = {"pdf"} | IMG_EXT
MIN_PASSWORD = 6

# Voci selezionabili in un intervento: chiave → (gruppo, etichetta).
# Le chiavi storiche (filtro_olio, olio, past_ant, ...) restano compatibili.
VOCI_INTERVENTO = {
    "olio":                  ("Olio e filtri", "Olio motore"),
    "filtro_olio":           ("Olio e filtri", "Filtro olio"),
    "filtro_aria":           ("Olio e filtri", "Filtro aria"),
    "filtro_abitacolo":      ("Olio e filtri", "Filtro abitacolo"),
    "filtro_gasolio":        ("Olio e filtri", "Filtro carburante"),
    "candele":               ("Motore", "Candele"),
    "cinghia_distribuzione": ("Motore", "Cinghia distribuzione"),
    "pompa_acqua":           ("Motore", "Pompa acqua"),
    "cinghia_servizi":       ("Motore", "Cinghia servizi"),
    "liquido_raffreddamento": ("Motore", "Liquido raffreddamento"),
    "past_ant":              ("Freni", "Pastiglie anteriori"),
    "past_post":             ("Freni", "Pastiglie posteriori"),
    "dischi_ant":            ("Freni", "Dischi anteriori"),
    "dischi_post":           ("Freni", "Dischi posteriori"),
    "liquido_freni":         ("Freni", "Liquido freni"),
    "pneumatici":            ("Altro", "Pneumatici"),
    "batteria":              ("Altro", "Batteria"),
    "spazzole":              ("Altro", "Spazzole tergicristallo"),
}
TIPI_INTERVENTO = ["Tagliando", "Manutenzione straordinaria", "Riparazione", "Altro"]
# Tipo scadenza → mesi per "Rinnova" (None = da inserire a mano)
TIPI_SCADENZA = {"Bollo": 12, "Assicurazione": 12, "Revisione": 24,
                 "Tagliando": 12, "Gomme invernali": 12, "Altro": None}
CARBURANTI = ["Benzina", "Diesel", "GPL", "Metano", "Ibrida", "Elettrica"]


class PrefixMiddleware:
    """Monta l'app sotto URL_PREFIX; la radice "/" reindirizza al prefisso."""

    def __init__(self, app, prefix):
        self.app, self.prefix = app, prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if not self.prefix:
            return self.app(environ, start_response)
        if path == self.prefix or path.startswith(self.prefix + "/"):
            environ["PATH_INFO"] = path[len(self.prefix):] or "/"
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + self.prefix
            return self.app(environ, start_response)
        if path in ("", "/"):
            start_response("302 Found", [("Location", self.prefix + "/")])
            return [b""]
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]


def _load_secret_key():
    """SECRET_KEY dall'ambiente, altrimenti generata una volta e salvata su file."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(db.DB)), ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    MAX_CONTENT_LENGTH=40 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
# Rispetta X-Forwarded-* quando è dietro Nginx Proxy Manager o simili
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.wsgi_app = PrefixMiddleware(app.wsgi_app, URL_PREFIX)

init_db()


# ──────────────────────────────────────────────────────────────────────────────
# SESSIONE, PERMESSI, CSRF
# ──────────────────────────────────────────────────────────────────────────────
@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_user_by_id(uid)
        if not g.user:
            session.clear()

    # Le cartelle con file privati non si servono come static pubblico
    if request.endpoint == "static":
        fn = (request.view_args or {}).get("filename", "")
        if fn.split("/")[0] in ("uploads", "libretti", "profiles"):
            abort(404)

    # Cambio password obbligatorio
    if g.user and g.user["must_change_password"] and request.endpoint not in (
            "change_password", "logout", "static", "profile_img", "healthz"):
        return redirect(url_for("change_password"))

    # CSRF su tutte le richieste POST
    if request.method == "POST":
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
            abort(400, "Sessione scaduta: ricarica la pagina e riprova.")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


# Globale Jinja: disponibile anche dentro le macro importate
app.jinja_env.globals["csrf_token"] = csrf_token


@app.context_processor
def inject_globals():
    return dict(
        current_user=g.get("user"),
        VOCI_INTERVENTO=VOCI_INTERVENTO,
        TIPI_INTERVENTO=TIPI_INTERVENTO,
        TIPI_SCADENZA=TIPI_SCADENZA,
        CARBURANTI=CARBURANTI,
        oggi=date.today().isoformat(),
    )


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.full_path))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


def veicolo_o_404(vid):
    v = get_veicolo_by_id(vid, g.user["id"], is_admin=bool(g.user["is_admin"]))
    if not v:
        abort(404)
    return v


def miei_veicoli():
    return get_veicoli(g.user["id"], is_admin=bool(g.user["is_admin"]))


# ──────────────────────────────────────────────────────────────────────────────
# FILTRI TEMPLATE
# ──────────────────────────────────────────────────────────────────────────────
MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]


@app.template_filter("data_it")
def data_it(s):
    d = parse_date(s)
    return f"{d.day} {MESI[d.month - 1]} {d.year}" if d else (s or "—")


@app.template_filter("km")
def fmt_km(n):
    if n in (None, ""):
        return "—"
    try:
        return f"{int(n):,}".replace(",", ".") + " km"
    except (TypeError, ValueError):
        return str(n)


@app.template_filter("euro")
def fmt_euro(n):
    if n in (None, ""):
        return ""
    s = f"{float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"


@app.template_filter("targa")
def fmt_targa(t):
    """AB123CD → 'AB 123 CD' (formato targhe italiane), altrimenti invariata."""
    t = (t or "").upper().replace(" ", "")
    if len(t) == 7 and t[:2].isalpha() and t[2:5].isdigit() and t[5:].isalpha():
        return f"{t[:2]} {t[2:5]} {t[5:]}"
    return t or "—"


@app.template_filter("tra_giorni")
def tra_giorni(s):
    d = parse_date(s)
    if not d:
        return ""
    n = (d - date.today()).days
    if n == 0:
        return "oggi"
    if n == 1:
        return "domani"
    if n == -1:
        return "ieri"
    if n < 0:
        return f"{-n} giorni fa"
    return f"tra {n} giorni"


# ──────────────────────────────────────────────────────────────────────────────
# UPLOAD
# ──────────────────────────────────────────────────────────────────────────────
def _salva_file(storage, folder, allowed):
    """Salva un file con nome univoco; None se vuoto o estensione non ammessa."""
    if not storage or not storage.filename:
        return None
    name = secure_filename(storage.filename) or "file"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in allowed:
        flash(f"File «{storage.filename}» ignorato: formato non ammesso.", "warning")
        return None
    fn = f"{uuid.uuid4().hex[:10]}_{name}"
    storage.save(os.path.join(folder, fn))
    return fn


def _rimuovi_file(folder, fn):
    if not fn:
        return
    path = os.path.join(folder, os.path.basename(fn))
    if os.path.isfile(path):
        os.remove(path)


# ──────────────────────────────────────────────────────────────────────────────
# LOGIN / LOGOUT / PASSWORD
# ──────────────────────────────────────────────────────────────────────────────
_tentativi = {}  # ip → [timestamp falliti]


def _troppi_tentativi(ip):
    ora = time.time()
    _tentativi[ip] = [t for t in _tentativi.get(ip, []) if ora - t < 600]
    return len(_tentativi[ip]) >= 8


@app.route("/", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _troppi_tentativi(ip):
            error = "Troppi tentativi falliti. Riprova tra qualche minuto."
        else:
            user = get_user_by_username(request.form.get("username", "").strip())
            if user and check_password_hash(user["password"], request.form.get("password", "")):
                _tentativi.pop(ip, None)
                session.clear()
                session.permanent = bool(request.form.get("ricordami"))
                session["user_id"] = user["id"]
                session["username"] = user["username"]  # compatibilità
                nxt = request.args.get("next", "")
                if nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(request.script_root + nxt)
                return redirect(url_for("dashboard"))
            _tentativi.setdefault(ip, []).append(time.time())
            error = "Username o password non corretti."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    forzato = bool(g.user["must_change_password"])
    error = None
    if request.method == "POST":
        attuale = request.form.get("attuale", "")
        nuova = request.form.get("password", "")
        conferma = request.form.get("conferma", "")
        if not forzato and not check_password_hash(g.user["password"], attuale):
            error = "La password attuale non è corretta."
        elif len(nuova) < MIN_PASSWORD:
            error = f"La nuova password deve avere almeno {MIN_PASSWORD} caratteri."
        elif nuova != conferma:
            error = "Le due password non coincidono."
        elif check_password_hash(g.user["password"], nuova):
            error = "Scegli una password diversa da quella attuale."
        else:
            set_password(g.user["username"], generate_password_hash(nuova))
            flash("Password aggiornata.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html", error=error, forzato=forzato)


# ──────────────────────────────────────────────────────────────────────────────
# DASHBOARD E SCADENZARIO
# ──────────────────────────────────────────────────────────────────────────────
ORDINE_STATO = {"scaduta": 0, "imminente": 1, "ok": 2, None: 3}


def _impegni(veicoli, giorni=None):
    """
    Lista unica di scadenze + prossimi tagliandi stimati per i veicoli dati,
    ordinata per urgenza. Se giorni è impostato, include solo quelli entro N giorni
    (più quelli già scaduti).
    """
    oggi = date.today()
    out = []
    for v in veicoli:
        for s in get_scadenze(v["id"]):
            d = parse_date(s["data"])
            out.append({"veicolo": v, "tipo": s["tipo"], "data": s["data"],
                        "note": s.get("note"), "stato": stato_data(d, oggi),
                        "scadenza_id": s["id"], "giorni": (d - oggi).days if d else None})
        pt = prossimo_tagliando(v, get_manutenzioni(v["id"]), oggi)
        if pt:
            out.append({"veicolo": v, "tipo": "Tagliando (stima)", "data": pt["data"],
                        "stato": pt["stato"], "giorni": pt["giorni"], "tagliando": pt})
    if giorni is not None:
        out = [i for i in out if i["stato"] in ("scaduta", "imminente")
               or (i["giorni"] is not None and i["giorni"] <= giorni)]
    out.sort(key=lambda i: (ORDINE_STATO[i["stato"]], i["data"] or "9999"))
    return out


@app.route("/dashboard")
@login_required
def dashboard():
    veicoli = miei_veicoli()
    impegni = _impegni(veicoli, giorni=90)
    n_scaduti = sum(1 for i in impegni if i["stato"] == "scaduta")
    n_imminenti = sum(1 for i in impegni if i["stato"] == "imminente")

    ultimi = []
    for v in veicoli:
        for m in get_manutenzioni(v["id"])[:3]:
            ultimi.append({**m, "veicolo": v})
    ultimi.sort(key=lambda m: m["data"] or "", reverse=True)

    tagliandi = {v["id"]: prossimo_tagliando(v, get_manutenzioni(v["id"])) for v in veicoli}
    return render_template("dashboard.html", veicoli=veicoli, impegni=impegni,
                           n_scaduti=n_scaduti, n_imminenti=n_imminenti,
                           ultimi=ultimi[:6], tagliandi=tagliandi)


@app.route("/scadenzario")
@login_required
def scadenzario():
    filtro = request.args.get("filtro", "tutte")
    impegni = _impegni(miei_veicoli())
    if filtro == "scadute":
        impegni = [i for i in impegni if i["stato"] == "scaduta"]
    elif filtro == "30":
        impegni = [i for i in impegni if i["stato"] in ("scaduta", "imminente")]
    return render_template("scadenzario.html", impegni=impegni, filtro=filtro)


# ──────────────────────────────────────────────────────────────────────────────
# VEICOLI
# ──────────────────────────────────────────────────────────────────────────────
def _form_veicolo():
    f = request.form
    return dict(
        nome=f.get("nome", "").strip(), cognome=f.get("cognome", "").strip(),
        targa=f.get("targa", "").strip(), marca=f.get("marca", "").strip(),
        modello=f.get("modello", "").strip(), anno=f.get("anno", "").strip(),
        km=f.get("km"), carburante=f.get("carburante") or None,
        intervallo_km=f.get("intervallo_km"), intervallo_mesi=f.get("intervallo_mesi"),
        note=f.get("note", "").strip() or None,
    )


@app.route("/veicoli")
@login_required
def veicoli():
    veic = miei_veicoli()
    tagliandi = {v["id"]: prossimo_tagliando(v, get_manutenzioni(v["id"])) for v in veic}
    prossime = {}
    for v in veic:
        sc = [s for s in get_scadenze(v["id"]) if parse_date(s["data"])]
        prossime[v["id"]] = sc[0] if sc else None
    return render_template("veicoli.html", veicoli=veic, tagliandi=tagliandi,
                           prossime=prossime)


@app.route("/aggiungi_veicolo", methods=["GET", "POST"])
@login_required
def aggiungi_veicolo_route():
    utenti = get_users() if g.user["is_admin"] else []
    if request.method == "POST":
        dati = _form_veicolo()
        if not dati["targa"]:
            flash("La targa è obbligatoria.", "danger")
            return render_template("veicolo_form.html", veicolo=dati, utenti=utenti, nuovo=True)
        imgs = [fn for fn in (_salva_file(f, UPLOAD_FOLDER, IMG_EXT)
                              for f in request.files.getlist("immagini")) if fn]
        lib = _salva_file(request.files.get("libretto"), PDF_FOLDER, LIBRETTO_EXT)
        owner = g.user["id"]
        if g.user["is_admin"] and request.form.get("owner_id", "").isdigit():
            owner = int(request.form["owner_id"])
        vid = aggiungi_veicolo(owner, imgs=imgs, lib=lib, **dati)
        flash(f"Veicolo {dati['targa'].upper()} aggiunto.", "success")
        return redirect(url_for("dettaglio_veicolo", vid=vid))
    return render_template("veicolo_form.html", veicolo={}, utenti=utenti, nuovo=True)


@app.route("/veicolo/<int:vid>")
@login_required
def dettaglio_veicolo(vid):
    v = veicolo_o_404(vid)
    manut = get_manutenzioni(vid)
    scans = get_scadenze(vid)
    for s in scans:
        s["stato"] = stato_data(parse_date(s["data"]))
    totale = sum(m["costo"] or 0 for m in manut)
    return render_template("veicolo.html", veicolo=v, manut=manut, scans=scans,
                           tagliando=prossimo_tagliando(v, manut), totale=totale)


@app.route("/modifica_veicolo/<int:vid>", methods=["GET", "POST"])
@login_required
def mod_v(vid):
    ve = veicolo_o_404(vid)
    utenti = get_users() if g.user["is_admin"] else []
    if request.method == "POST":
        dati = _form_veicolo()
        if not dati["targa"]:
            flash("La targa è obbligatoria.", "danger")
            return render_template("veicolo_form.html", veicolo={**ve, **dati},
                                   utenti=utenti, nuovo=False)
        imgs = ve["immagini"][:] + [fn for fn in (_salva_file(f, UPLOAD_FOLDER, IMG_EXT)
                                                  for f in request.files.getlist("immagini")) if fn]
        lib = ve.get("libretto")
        nuovo_lib = _salva_file(request.files.get("libretto"), PDF_FOLDER, LIBRETTO_EXT)
        if nuovo_lib:
            _rimuovi_file(PDF_FOLDER, lib)
            lib = nuovo_lib
        owner = None
        if g.user["is_admin"] and request.form.get("owner_id", "").isdigit():
            owner = int(request.form["owner_id"])
        aggiorna_veicolo(vid, imgs=imgs, lib=lib, owner_id=owner, **dati)
        flash("Modifiche salvate.", "success")
        return redirect(url_for("dettaglio_veicolo", vid=vid))
    return render_template("veicolo_form.html", veicolo=ve, utenti=utenti, nuovo=False)


@app.route("/veicolo/delete/<int:vid>", methods=["POST"])
@login_required
def elimina_veicolo_route(vid):
    v = veicolo_o_404(vid)
    for fn in v["immagini"]:
        _rimuovi_file(UPLOAD_FOLDER, fn)
    _rimuovi_file(PDF_FOLDER, v.get("libretto"))
    elimina_veicolo(vid)
    flash(f"Veicolo {v['targa']} eliminato con tutto lo storico.", "success")
    return redirect(url_for("veicoli"))


@app.route("/veicolo/<int:vid>/foto/elimina", methods=["POST"])
@login_required
def foto_elimina(vid):
    v = veicolo_o_404(vid)
    fn = request.form.get("fn")
    if fn in v["immagini"]:
        imgs = [i for i in v["immagini"] if i != fn]
        aggiorna_veicolo(vid, v["nome"], v["cognome"], v["targa"], v["marca"], v["modello"],
                         v["anno"], v["km"], imgs, v.get("libretto"), v.get("carburante"),
                         v["intervallo_km"], v["intervallo_mesi"], v.get("note"))
        _rimuovi_file(UPLOAD_FOLDER, fn)
        flash("Foto eliminata.", "success")
    return redirect(url_for("mod_v", vid=vid))


@app.route("/veicolo/<int:vid>/libretto/elimina", methods=["POST"])
@login_required
def libretto_elimina(vid):
    v = veicolo_o_404(vid)
    if v.get("libretto"):
        _rimuovi_file(PDF_FOLDER, v["libretto"])
        aggiorna_veicolo(vid, v["nome"], v["cognome"], v["targa"], v["marca"], v["modello"],
                         v["anno"], v["km"], v["immagini"], None, v.get("carburante"),
                         v["intervallo_km"], v["intervallo_mesi"], v.get("note"))
        flash("Libretto eliminato.", "success")
    return redirect(url_for("mod_v", vid=vid))


@app.route("/veicolo/<int:vid>/storico.csv")
@login_required
def export_csv(vid):
    v = veicolo_o_404(vid)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Km", "Tipo", "Voci", "Officina", "Costo", "Note"])
    for m in get_manutenzioni(vid):
        voci = []
        for k, val in m["details"].items():
            if k == "olio_tipo":
                continue
            label = VOCI_INTERVENTO.get(k, ("", k.replace("_", " ")))[1]
            extra = " ".join(x for x in ((val or {}).get("marca"), (val or {}).get("cod")) if x) \
                if isinstance(val, dict) else ""
            voci.append(f"{label} {extra}".strip())
        if m["details"].get("olio_tipo"):
            voci.append(f"Tipo olio {m['details']['olio_tipo']}")
        costo = f"{m['costo']:.2f}".replace(".", ",") if m["costo"] is not None else ""
        w.writerow([m["data"], m["km"] or "", m["tipo"], ", ".join(voci),
                    m.get("officina") or "", costo, m.get("note") or ""])
    nome = f"storico_{secure_filename(v['targa'] or str(vid))}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nome}"})


# ──────────────────────────────────────────────────────────────────────────────
# MANUTENZIONI
# ──────────────────────────────────────────────────────────────────────────────
def _form_manutenzione():
    f = request.form
    details = {}
    for key in VOCI_INTERVENTO:
        if f.get(key):
            details[key] = {"cod": f.get(f"cod_{key}", "").strip(),
                            "marca": f.get(f"mar_{key}", "").strip()}
    if f.get("olio_tipo", "").strip():
        details["olio_tipo"] = f.get("olio_tipo").strip()
    return dict(
        data=f.get("data"), km=f.get("km_man"), details=details,
        tipo=f.get("tipo") if f.get("tipo") in TIPI_INTERVENTO else "Tagliando",
        costo=f.get("costo"), officina=f.get("officina", "").strip() or None,
        note=f.get("note", "").strip() or None,
    )


@app.route("/manutenzione/<int:vid>", methods=["GET", "POST"])
@login_required
def manutenzione(vid):
    ve = veicolo_o_404(vid)
    if request.method == "POST":
        dati = _form_manutenzione()
        if not parse_date(dati["data"]):
            flash("Inserisci una data valida.", "danger")
            return render_template("manutenzione.html", veicolo=ve, m=dati)
        aggiungi_manutenzione(vid, **dati)
        flash("Intervento registrato.", "success")
        return redirect(url_for("dettaglio_veicolo", vid=vid) + "#interventi")
    ultima = (get_manutenzioni(vid) or [None])[0]
    return render_template("manutenzione.html", veicolo=ve,
                           m={"data": date.today().isoformat(), "km": ve.get("km"),
                              "tipo": "Tagliando", "details": {}},
                           ultima=ultima)


@app.route("/manutenzione/<int:vid>/<int:mid>/modifica", methods=["GET", "POST"])
@login_required
def manutenzione_modifica(vid, mid):
    ve = veicolo_o_404(vid)
    m = get_manutenzione(mid, vid) or abort(404)
    if request.method == "POST":
        dati = _form_manutenzione()
        if not parse_date(dati["data"]):
            flash("Inserisci una data valida.", "danger")
            return render_template("manutenzione.html", veicolo=ve, m={**m, **dati})
        aggiorna_manutenzione(mid, vid, **dati)
        flash("Intervento aggiornato.", "success")
        return redirect(url_for("dettaglio_veicolo", vid=vid) + "#interventi")
    return render_template("manutenzione.html", veicolo=ve, m=m)


@app.route("/manutenzione/<int:vid>/<int:mid>/elimina", methods=["POST"])
@login_required
def manutenzione_elimina(vid, mid):
    veicolo_o_404(vid)
    elimina_manutenzione(mid, vid)
    flash("Intervento eliminato.", "success")
    return redirect(url_for("dettaglio_veicolo", vid=vid) + "#interventi")


# ──────────────────────────────────────────────────────────────────────────────
# SCADENZE
# ──────────────────────────────────────────────────────────────────────────────
def _form_scadenza():
    tipo = request.form.get("tipo", "").strip()
    if tipo == "Altro" and request.form.get("tipo_altro", "").strip():
        tipo = request.form["tipo_altro"].strip()
    return tipo, request.form.get("data_sc"), request.form.get("note", "").strip() or None


@app.route("/scadenze/<int:vid>", methods=["GET", "POST"])
@login_required
def scadenze(vid):
    ve = veicolo_o_404(vid)
    if request.method == "POST":
        tipo, data_sc, note = _form_scadenza()
        if not tipo or not parse_date(data_sc):
            flash("Indica tipo e data della scadenza.", "danger")
        else:
            aggiungi_scadenza(vid, tipo, data_sc, note)
            flash(f"Scadenza «{tipo}» aggiunta.", "success")
            return redirect(url_for("dettaglio_veicolo", vid=vid) + "#scadenze")
    return render_template("scadenze.html", veicolo=ve, s={})


@app.route("/scadenze/<int:vid>/<int:sid>/modifica", methods=["GET", "POST"])
@login_required
def scadenza_modifica(vid, sid):
    ve = veicolo_o_404(vid)
    s = get_scadenza(sid, vid) or abort(404)
    if request.method == "POST":
        tipo, data_sc, note = _form_scadenza()
        if not tipo or not parse_date(data_sc):
            flash("Indica tipo e data della scadenza.", "danger")
        else:
            aggiorna_scadenza(sid, vid, tipo, data_sc, note)
            flash("Scadenza aggiornata.", "success")
            return redirect(url_for("dettaglio_veicolo", vid=vid) + "#scadenze")
    return render_template("scadenze.html", veicolo=ve, s=s)


@app.route("/scadenze/<int:vid>/<int:sid>/rinnova", methods=["POST"])
@login_required
def scadenza_rinnova(vid, sid):
    """Sposta in avanti la scadenza del periodo tipico (es. bollo +12 mesi)."""
    veicolo_o_404(vid)
    s = get_scadenza(sid, vid) or abort(404)
    mesi = TIPI_SCADENZA.get(s["tipo"]) or 12
    d = parse_date(s["data"]) or date.today()
    nuova = add_months(d, mesi).isoformat()
    aggiorna_scadenza(sid, vid, s["tipo"], nuova, s.get("note"))
    flash(f"{s['tipo']} rinnovato: nuova scadenza {data_it(nuova)}.", "success")
    return redirect(request.form.get("next") or url_for("dettaglio_veicolo", vid=vid))


@app.route("/scadenze/<int:vid>/<int:sid>/elimina", methods=["POST"])
@login_required
def scadenza_elimina(vid, sid):
    veicolo_o_404(vid)
    elimina_scadenza(sid, vid)
    flash("Scadenza eliminata.", "success")
    return redirect(url_for("dettaglio_veicolo", vid=vid) + "#scadenze")


# ──────────────────────────────────────────────────────────────────────────────
# UTENTI (ADMIN)
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/users")
@admin_required
def users():
    return render_template("users.html", users=get_users())


@app.route("/users/add", methods=["GET", "POST"])
@admin_required
def users_add():
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        pwd = request.form.get("password", "")
        if not uname or len(pwd) < MIN_PASSWORD:
            flash(f"Username obbligatorio e password di almeno {MIN_PASSWORD} caratteri.", "danger")
            return render_template("user_form.html", user={"username": uname}, nuovo=True)
        img = _salva_file(request.files.get("profile_img"), PROFILE_FOLDER, IMG_EXT)
        if not add_user(uname, generate_password_hash(pwd), img,
                        1 if request.form.get("is_admin") else 0, must_change_password=1):
            _rimuovi_file(PROFILE_FOLDER, img)
            flash(f"Lo username «{uname}» esiste già.", "danger")
            return render_template("user_form.html", user={"username": uname}, nuovo=True)
        flash(f"Utente {uname} creato. Dovrà cambiare password al primo accesso.", "success")
        return redirect(url_for("users"))
    return render_template("user_form.html", user={}, nuovo=True)


@app.route("/users/edit/<int:uid>", methods=["GET", "POST"])
@admin_required
def users_edit(uid):
    u = get_user_by_id(uid) or abort(404)
    if u["is_superadmin"] and g.user["id"] != u["id"]:
        abort(403)
    if request.method == "POST":
        new_username = request.form.get("username", "").strip() or None
        new_password = request.form.get("password", "")
        if new_password and len(new_password) < MIN_PASSWORD:
            flash(f"La password deve avere almeno {MIN_PASSWORD} caratteri.", "danger")
            return render_template("user_form.html", user=u, nuovo=False)
        img = _salva_file(request.files.get("profile_img"), PROFILE_FOLDER, IMG_EXT)
        if img:
            _rimuovi_file(PROFILE_FOLDER, u["profile_img"])
        is_admin = 1 if (request.form.get("is_admin") or u["is_superadmin"]) else 0
        # Un admin non può togliersi da solo i permessi
        if g.user["id"] == u["id"]:
            is_admin = 1
        ok = aggiorna_utente(
            uid, username=new_username,
            password_hash=generate_password_hash(new_password) if new_password else None,
            profile_img=img, is_admin=is_admin,
            must_change_password=1 if request.form.get("force_change") else 0)
        if not ok:
            flash(f"Lo username «{new_username}» è già usato.", "danger")
            return render_template("user_form.html", user=u, nuovo=False)
        if g.user["id"] == u["id"] and new_username:
            session["username"] = new_username
        flash("Utente aggiornato.", "success")
        return redirect(url_for("users"))
    return render_template("user_form.html", user=u, nuovo=False)


@app.route("/users/delete/<int:uid>", methods=["POST"])
@admin_required
def users_delete(uid):
    u = get_user_by_id(uid) or abort(404)
    if u["is_superadmin"] or u["id"] == g.user["id"]:
        flash("Questo utente non può essere eliminato.", "danger")
    elif count_veicoli_utente(uid):
        flash(f"{u['username']} ha ancora veicoli: assegnali a un altro utente "
              "dalla pagina del veicolo, poi riprova.", "warning")
    elif delete_user(uid):
        _rimuovi_file(PROFILE_FOLDER, u["profile_img"])
        flash(f"Utente {u['username']} eliminato.", "success")
    return redirect(url_for("users"))


# ──────────────────────────────────────────────────────────────────────────────
# FILE PROTETTI
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/uploads/<path:fn>")
@login_required
def uploads(fn):
    if not any(fn in v["immagini"] for v in miei_veicoli()):
        abort(404)
    return send_from_directory(UPLOAD_FOLDER, fn, max_age=86400)


@app.route("/libretti/<path:fn>")
@login_required
def download_libretto(fn):
    if not any(fn == v.get("libretto") for v in miei_veicoli()):
        abort(404)
    return send_from_directory(PDF_FOLDER, fn, as_attachment=request.args.get("dl") == "1")


@app.route("/profilo/<path:fn>")
@login_required
def profile_img(fn):
    return send_from_directory(PROFILE_FOLDER, fn, max_age=86400)


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# ERRORI
# ──────────────────────────────────────────────────────────────────────────────
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def errore(e):
    messaggi = {
        400: e.description if isinstance(e.description, str) else "Richiesta non valida.",
        403: "Non hai i permessi per questa pagina.",
        404: "Pagina o veicolo non trovato.",
        413: "File troppo grande (massimo 40 MB per invio).",
    }
    return render_template("errore.html", codice=e.code,
                           messaggio=messaggi.get(e.code, "Errore")), e.code


# ──────────────────────────────────────────────────────────────────────────────
# AVVIO (sviluppo). In produzione: gunicorn, vedi entrypoint.sh
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="0.0.0.0", port=5000)

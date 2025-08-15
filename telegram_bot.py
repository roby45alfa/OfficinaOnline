"""
File: telegram_bot.py
Modulo Python per il Bot Telegram dell’app “Officina”
Responsabilità:
  - Autenticazione utenti via /login e /logout
  - Menu principale con pulsanti per Veicoli, Manutenzioni, Scadenze
  - Visualizzazione inline dei veicoli
  - Elenco, filtro e aggiunta interattiva di manutenzioni e scadenze
  - Notifiche giornaliere delle scadenze
"""

# ──────────────────────────────────────────────────────────────────────────────
# IMPORTAZIONI
# ──────────────────────────────────────────────────────────────────────────────
import warnings
warnings.filterwarnings("ignore")  # Disabilita avvisi non critici

import os                           # Legge variabili d’ambiente
import logging                      # Log delle operazioni
import threading                    # Thread per loop notifiche
from datetime import datetime, timedelta  # Gestione date e orari

import requests                     # Invio HTTP per notifiche bot (via API Telegram)
from telegram import (              # Componenti core di telegram-bot
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (          # Estensioni e handler per telegram-bot
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from werkzeug.security import check_password_hash  # Verifica password hashtata

# Import delle funzioni di accesso al database (db.py)
from db import (
    get_user_by_username,
    get_users,
    get_veicoli,
    get_manutenzioni,
    get_scadenze,
    aggiungi_manutenzione,
    get_db,  # Nota: get_db non più usato con flask.g
)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAZIONE GLOBALE
# ──────────────────────────────────────────────────────────────────────────────
# Sessioni in memoria: chat_id → {user_id, username, is_admin}
SESSIONS: dict[int, dict] = {}

# Parametri notifiche giornaliere (UTC)
NOTIF_HOUR    = 8     # Ora di invio
NOTIF_MINUTE  = 0     # Minuto di invio
NOTIF_ENABLED = False # Flag attivazione notifiche
_notif_thread: threading.Thread | None = None
_notif_stop_event = threading.Event()  # Per fermare il loop notifiche

# Logger di sistema
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tastiera principale (Reply Keyboard) per il menu
MENU = [
    ["🚗 Veicoli", "🔧 Manutenzioni"],
    ["⏰ Scadenze", "❌ Logout"],
]

# Testo di aiuto (help)
HELP_TEXT = (
    "Comandi disponibili:\n"
    "/login <username> <password>\n"
    "/logout\n"
    "Oppure usa i bottoni del menu.\n"
)

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS PRIVATI
# ──────────────────────────────────────────────────────────────────────────────
def _user_has_access_to_vehicle(session: dict, vid: int) -> bool:
    """
    Controlla se l’utente (session) ha accesso al veicolo vid.
    - Se is_admin=True, sempre True
    - Altrimenti controlla che vid compaia fra i veicoli di user_id
    """
    if session.get("is_admin"):
        return True
    # Ottiene tutti i veicoli dell’utente non-admin e verifica l’ID
    return any(
        int(dict(v)["id"]) == vid
        for v in get_veicoli(user_id=session["user_id"], is_admin=False)
    )

def _genera_report_scadenze_per_utente(user_row) -> str | None:
    """
    Genera un report di scadenze scadute o prossime (entro 7 giorni)
    per l’utente specificato in user_row.
    Ritorna None se non ci sono scadenze da notificare.
    """
    u = dict(user_row)
    uid      = u["id"]
    is_admin = bool(u.get("is_admin"))
    today    = datetime.utcnow().date()
    cutoff   = today + timedelta(days=7)
    items: list[str] = []

    # Per ogni veicolo dell’utente (o tutti se admin)
    for v in map(dict, get_veicoli(is_admin=is_admin, user_id=None if is_admin else uid)):
        # Controlla ogni scadenza del veicolo
        for s in map(dict, get_scadenze(v["id"])):
            desc = s.get("tipo") or s.get("descrizione", "")
            d_str = s.get("data", "")
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            # Determina stato: scaduta o prossima
            if d < today:
                status = "⛔ Scaduta"
            elif d <= cutoff:
                status = "⚠️ Prossima"
            else:
                continue
            items.append(f"{v['marca']} {v['targa']} – {desc} il {d_str} ({status})")

    if not items:
        return None

    header = f"📣 Scadenze per {'Admin' if is_admin else u['username']}:\n"
    return header + "\n".join(items)

def _notifications_loop():
    """
    Loop in background che invia notifiche ogni giorno alle HH:MM UTC.
    Usa direttamente le API HTTP di Telegram per inviare i messaggi.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        logger.error("Manca TELEGRAM_TOKEN")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Finché abilitate e non fermate
    while NOTIF_ENABLED and not _notif_stop_event.is_set():
        now    = datetime.utcnow()
        # Prossimo bersaglio con ora e minuto configurati
        target = now.replace(
            hour=NOTIF_HOUR, minute=NOTIF_MINUTE,
            second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)

        wait = (target - now).total_seconds()
        logger.info(f"[notifiche] in attesa {int(wait)}s fino a {target.isoformat()}Z")
        # Attende o esce se fermato
        if _notif_stop_event.wait(wait):
            break

        # Per ogni utente, genera report e invia se non vuoto
        for u in get_users():
            report = _genera_report_scadenze_per_utente(u)
            if not report:
                continue
            uname = dict(u)["username"]
            # Cerca chat_id corrispondenti in sessioni
            for chat_id, sess in SESSIONS.items():
                if sess["username"] == uname:
                    try:
                        requests.post(
                            url,
                            json={"chat_id":chat_id, "text":report},
                            timeout=10
                        ).raise_for_status()
                    except Exception as e:
                        logger.warning(f"Notif fallita per {chat_id}: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLERS: COMANDI DI BASE
# ──────────────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start
    Mostra il menu principale con i pulsanti definiti in MENU.
    """
    await update.effective_message.reply_text(
        "Benvenuto! Scegli un’opzione:",
        reply_markup=ReplyKeyboardMarkup(MENU, resize_keyboard=True)
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /help
    Mostra l’elenco dei comandi disponibili.
    """
    await update.effective_message.reply_text(HELP_TEXT)

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /login <username> <password>
    Esegue autenticazione:
      - Verifica che ci siano esattamente 2 argomenti
      - Confronta password hashtata
      - Memorizza sessione in SESSIONS
    """
    msg = update.effective_message
    if len(context.args) != 2:
        return await msg.reply_text("Uso: /login <username> <password>")
    row = get_user_by_username(context.args[0])
    # Verifica che utente esista e password sia corretta
    if not row or not check_password_hash(dict(row).get("password",""), context.args[1]):
        return await msg.reply_text("Credenziali errate.")
    user = dict(row)
    SESSIONS[update.effective_chat.id] = {
        "user_id":   user["id"],
        "username":  user["username"],
        "is_admin":  bool(user.get("is_admin"))
    }
    await msg.reply_text(f"Login come {user['username']}.")
    # Torna al menu principale
    await start(update, context)

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /logout
    Cancella la sessione corrente (rimuovendo chat_id da SESSIONS).
    """
    msg = update.effective_message
    cid = update.effective_chat.id
    if cid in SESSIONS:
        del SESSIONS[cid]
        return await msg.reply_text("Logout eseguito.", reply_markup=ReplyKeyboardRemove())
    await msg.reply_text("Non eri loggato.")

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: MENU TESTUALE
# ──────────────────────────────────────────────────────────────────────────────
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestisce i testi dei pulsanti di MENU:
      🚗 Veicoli      → elenca veicoli via /veicoli oppure inline
      🔧 Manutenzioni → inline select veicolo per manutenzioni
      ⏰ Scadenze     → inline select veicolo per scadenze
      ❌ Logout       → logout
    Se siamo in attesa di input form, ignora il menu.
    """
    if context.user_data.get("waiting_for"):
        return
    text = update.effective_message.text
    if text == "🚗 Veicoli":
        return await veicoli_cmd(update, context)
    if text == "🔧 Manutenzioni":
        return await list_veicoli_inline(update, context, for_scadenze=False)
    if text == "⏰ Scadenze":
        return await list_veicoli_inline(update, context, for_scadenze=True)
    if text == "❌ Logout":
        return await logout(update, context)
    return await unknown(update, context)

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: SELEZIONE INLINE DEI VEICOLI
# ──────────────────────────────────────────────────────────────────────────────
async def list_veicoli_inline(update: Update, context: ContextTypes.DEFAULT_TYPE, for_scadenze: bool):
    """
    Mostra una InlineKeyboard con i veicoli di SESSIONS[chat_id].
    Callback dati: "MANU;<vid>" o "SCAD;<vid>"
    """
    msg  = update.effective_message
    sess = SESSIONS.get(update.effective_chat.id)
    if not sess:
        return await msg.reply_text("Devi prima /login.")
    # Crea bottoni da riga per ciascun veicolo
    buttons = [
        [InlineKeyboardButton(
            f"{v['marca']} {v['targa']}",
            callback_data=f"{'SCAD' if for_scadenze else 'MANU'};{v['id']}"
        )]
        for v in map(dict, get_veicoli(is_admin=sess["is_admin"], user_id=sess["user_id"]))
    ]
    await msg.reply_text("Seleziona veicolo:", reply_markup=InlineKeyboardMarkup(buttons))

async def inline_veicolo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    CallbackQueryHandler per i bottoni inline dei veicoli.
    Smista a manutenzioni_cmd o scadenze_cmd.
    """
    q   = update.callback_query
    await q.answer()
    typ, vid = q.data.split(";")
    if typ == "MANU":
        return await manutenzioni_cmd(update, context, vid_override=int(vid))
    else:
        return await scadenze_cmd(update, context, vid_override=int(vid))

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: ELENCO VEICOLI TESTUALE
# ──────────────────────────────────────────────────────────────────────────────
async def veicoli_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /veicoli
    Elenca i veicoli di sessione come testo.
    Se nessuno, indica “Nessun veicolo.”
    """
    msg  = update.effective_message
    sess = SESSIONS.get(update.effective_chat.id)
    if not sess:
        return await msg.reply_text("Devi prima /login.")
    rows = get_veicoli(is_admin=sess["is_admin"], user_id=sess["user_id"])
    if not rows:
        await msg.reply_text("Nessun veicolo.")
    else:
        lines = [f"ID:{r['id']} {r['marca']} {r['targa']}" for r in map(dict, rows)]
        await msg.reply_text("\n".join(lines))
    # Ritorna al menu principale
    await start(update, context)

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: MANUTENZIONI
# ──────────────────────────────────────────────────────────────────────────────
async def manutenzioni_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, vid_override: int | None = None):
    """
    Mostra manutenzioni di un veicolo (ID estratto da argomenti o vid_override).
    Aggiunge pulsante ➕ Aggiungi e 🔙 Menu.
    """
    msg  = update.effective_message
    sess = SESSIONS.get(update.effective_chat.id)
    if not sess:
        return await msg.reply_text("Devi prima /login.")
    # Ricava vid da override o argomenti
    vid = int(vid_override) if vid_override is not None else int(context.args[0])
    if not _user_has_access_to_vehicle(sess, vid):
        return await msg.reply_text("Accesso negato.")
    mans = get_manutenzioni(vid)
    if not mans:
        await msg.reply_text("Nessuna manutenzione.")
    else:
        lines = []
        for m in map(dict, mans):
            desc = m.get("tipo") or m.get("descrizione", "")
            date = m.get("data", "")
            note = m.get("note", "")
            txt = f"ID:{m['id']} {desc} il {date}"
            if note:
                txt += f" – {note}"
            lines.append(txt)
        # Inline keyboard per aggiungere nuova manutenzione o tornare al menu
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Aggiungi", callback_data=f"START_ADD_MANU;{vid}")],
            [InlineKeyboardButton("🔙 Menu",    callback_data="MENU")]
        ])
        await msg.reply_text("\n".join(lines), reply_markup=kb)

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: SCADENZE
# ──────────────────────────────────────────────────────────────────────────────
async def scadenze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, vid_override: int | None = None):
    """
    Mostra scadenze di un veicolo con bottoni per filtri e aggiunta.
    """
    msg  = update.effective_message
    sess = SESSIONS.get(update.effective_chat.id)
    if not sess:
        return await msg.reply_text("Devi prima /login.")
    vid = int(vid_override) if vid_override is not None else int(context.args[0])
    if not _user_has_access_to_vehicle(sess, vid):
        return await msg.reply_text("Accesso negato.")
    rows = get_scadenze(vid)
    lines = [
        f"ID:{s['id']} {(s.get('tipo') or s.get('descrizione',''))} il {s['data']}"
        for s in map(dict, rows)
    ]
    if not lines:
        await msg.reply_text("Nessuna scadenza.")
    else:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏳ Prossime 7 giorni", callback_data=f"FILTER_SCAD;UPCOMING;{vid}"),
                InlineKeyboardButton("❌ Scadute",           callback_data=f"FILTER_SCAD;EXPIRED;{vid}")
            ],
            [InlineKeyboardButton("🔄 Tutte", callback_data=f"FILTER_SCAD;ALL;{vid}")],
            [
                InlineKeyboardButton("➕ Aggiungi", callback_data=f"START_ADD_SCAD;{vid}"),
                InlineKeyboardButton("🔙 Menu",     callback_data="MENU")
            ]
        ])
        await msg.reply_text("\n".join(lines), reply_markup=kb)

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: FILTRO SCADENZE
# ──────────────────────────────────────────────────────────────────────────────
async def filter_scadenze_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback per i bottoni di filtro scadenze:
    • UPCOMING: prossime 7 giorni
    • EXPIRED : già scadute
    • ALL     : tutte
    """
    q = update.callback_query
    await q.answer()
    _, mode, vid_str = q.data.split(";")
    vid = int(vid_str)
    today  = datetime.utcnow().date()
    cutoff = today + timedelta(days=7)
    filtered = []
    for s in map(dict, get_scadenze(vid)):
        try:
            d = datetime.strptime(s["data"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if mode == "UPCOMING" and not (today <= d <= cutoff):
            continue
        if mode == "EXPIRED" and not (d < today):
            continue
        desc = s.get("tipo") or s.get("descrizione","")
        filtered.append(f"ID:{s['id']} {desc} il {s['data']}")
    text = "\n".join(filtered) if filtered else "Nessuna scadenza per questo filtro."
    # Ricrea la stessa keyboard per ulteriori filtri o aggiunta
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏳ Prossime 7 giorni", callback_data=f"FILTER_SCAD;UPCOMING;{vid}"),
            InlineKeyboardButton("❌ Scadute",           callback_data=f"FILTER_SCAD;EXPIRED;{vid}")
        ],
        [InlineKeyboardButton("🔄 Tutte", callback_data=f"FILTER_SCAD;ALL;{vid}")],
        [
            InlineKeyboardButton("➕ Aggiungi", callback_data=f"START_ADD_SCAD;{vid}"),
            InlineKeyboardButton("🔙 Menu",     callback_data="MENU")
        ]
    ])
    await q.message.edit_text(text, reply_markup=kb)

# ──────────────────────────────────────────────────────────────────────────────
# BOT HANDLER: FORM INTERATTIVO PER AGGIUNTA MANUTENZIONE
# ──────────────────────────────────────────────────────────────────────────────
async def _render_add_manutenzione_form(msg, context):
    """
    Mostra il form interattivo:
      - Pulsanti per impostare tipo, data, note
      - Pulsanti Salva e Annulla
    I valori correnti sono in context.user_data['fields'].
    """
    vid    = context.user_data["new_vid"]
    fields = context.user_data.setdefault("fields", {"tipo":None, "data":None, "note":None})
    # Header e corpo form sintetico
    header = f"🆕 Aggiungi manutenzione per veicolo {vid}\n\n"
    body   = (
        f"✏️ Tipo: {fields['tipo'] or '...'}\n"
        f"📅 Data: {fields['data'] or '...'}\n"
        f"📝 Note: {fields['note'] or '...'}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Tipo", callback_data="ADD_MANU_FIELD;TYPE")],
        [InlineKeyboardButton("📅 Data", callback_data="ADD_MANU_FIELD;DATE")],
        [InlineKeyboardButton("📝 Note", callback_data="ADD_MANU_FIELD;NOTE")],
        [InlineKeyboardButton("✅ Salva", callback_data="SAVE_MANU")],
        [InlineKeyboardButton("🔙 Annulla", callback_data="MENU")],
    ])
    await msg.reply_text(header + body, reply_markup=kb)

async def start_add_manutenzione(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback per START_ADD_MANU;<vid>
    Inizializza context.user_data e mostra il form interattivo.
    """
    q = update.callback_query
    await q.answer()
    _, vid = q.data.split(";")
    context.user_data.clear()
    context.user_data["new_vid"] = int(vid)
    await _render_add_manutenzione_form(q.message, context)

async def add_manutenzione_field_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback per ADD_MANU_FIELD;<FIELD>
    Imposta context.user_data['waiting_for'] e chiede l’input testuale corrispondente.
    """
    q = update.callback_query
    await q.answer()
    _, field = q.data.split(";")
    context.user_data["waiting_for"] = field
    prompts = {
        "TYPE": "Inserisci il tipo di manutenzione:",
        "DATE": "Inserisci la data (YYYY-MM-DD):",
        "NOTE": "Inserisci eventuali note (o 'nessuna'):"
    }
    await q.message.reply_text(prompts[field], reply_markup=ReplyKeyboardRemove())

async def add_manutenzione_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestisce l’input testuale dopo add_manutenzione_field_cb:
    - Valida la data se necessario
    - Salva in context.user_data['fields']
    - Rirenderizza il form sintetico
    """
    if "waiting_for" not in context.user_data:
        return
    field = context.user_data.pop("waiting_for")
    text  = update.effective_message.text.strip()
    if field == "DATE":
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return await update.effective_message.reply_text("Formato errato, riprova YYYY-MM-DD:")
    keymap = {"TYPE":"tipo", "DATE":"data", "NOTE":"note"}
    context.user_data["fields"][keymap[field]] = text
    await _render_add_manutenzione_form(update.effective_message, context)

async def save_manutenzione_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback per SAVE_MANU:
    - Controlla che tipo e data siano presenti
    - Chiama aggiungi_manutenzione(...)
    - Conferma e resetta context.user_data
    """
    q = update.callback_query
    await q.answer()
    fields = context.user_data.get("fields", {})
    vid    = context.user_data.get("new_vid")

    # Controllo campi obbligatori
    if not fields.get("tipo") or not fields.get("data"):
        await q.message.reply_text(
            "Devi specificare almeno *tipo* e *data* prima di salvare.",
            parse_mode="MarkdownV2"
        )
        return await _render_add_manutenzione_form(q.message, context)

    # Salva la manutenzione
    aggiungi_manutenzione(vid, fields["tipo"], fields["data"], fields.get("note",""))

    # Conferma e ripristina menu
    await q.message.reply_text("✅ Manutenzione salvata!", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    await start(update, context)

# ──────────────────────────────────────────────────────────────────────────────
# HANDLER VARI: MENU CALLBACK E NOTIFICHE
# ──────────────────────────────────────────────────────────────────────────────
async def callback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback per MENU → torna al menu principale."""
    await start(update, context)

async def attiva_notifiche_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /attiva_notifiche
    Attiva il thread di notifiche giornaliere.
    """
    msg = update.effective_message
    global NOTIF_ENABLED, _notif_thread
    if NOTIF_ENABLED:
        return await msg.reply_text("Notifiche già attive.")
    NOTIF_ENABLED = True
    _notif_stop_event.clear()
    _notif_thread = threading.Thread(target=_notifications_loop, daemon=True)
    _notif_thread.start()
    await msg.reply_text("Notifiche attivate.")
    await start(update, context)

async def disattiva_notifiche_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /disattiva_notifiche
    Disattiva il thread di notifiche.
    """
    msg = update.effective_message
    global NOTIF_ENABLED
    if not NOTIF_ENABLED:
        return await msg.reply_text("Notifiche già disattivate.")
    NOTIF_ENABLED = False
    _notif_stop_event.set()
    await msg.reply_text("Notifiche disattivate.")
    await start(update, context)

async def imposta_notifiche_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /imposta_notifiche HH:MM
    Imposta l’orario UTC per le notifiche e riavvia il thread se attivo.
    """
    msg = update.effective_message
    global NOTIF_HOUR, NOTIF_MINUTE, NOTIF_ENABLED, _notif_thread
    if len(context.args) != 1:
        return await msg.reply_text("Uso: /imposta_notifiche <HH:MM> (UTC)")
    try:
        h, m = map(int, context.args[0].split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError()
    except ValueError:
        return await msg.reply_text("Formato errato, es 07:30")
    NOTIF_HOUR, NOTIF_MINUTE = h, m
    # Se già attive, riavvia il loop con nuovo orario
    if NOTIF_ENABLED:
        _notif_stop_event.set()
        _notif_stop_event.clear()
        _notif_thread = threading.Thread(target=_notifications_loop, daemon=True)
        _notif_thread.start()
    await msg.reply_text(f"Notifiche impostate alle {h:02d}:{m:02d} UTC.")
    await start(update, context)

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback per comandi sconosciuti."""
    await update.effective_message.reply_text("Comando non riconosciuto. Scrivi /help")

# ──────────────────────────────────────────────────────────────────────────────
# MAIN: CONFIGURAZIONE APPLICATION E AVVIO POLLING
# ──────────────────────────────────────────────────────────────────────────────
def main():
    """Configura gli handler e avvia il polling del bot Telegram."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        logger.error("Serve TELEGRAM_TOKEN nel .env")
        return

    app = ApplicationBuilder().token(token).build()

    # Comandi slash di base
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("logout",logout))

    # Selezione inline dei veicoli (MANU o SCAD)
    app.add_handler(CallbackQueryHandler(inline_veicolo_selected, pattern="^(MANU|SCAD);"))

    # Filtri scadenze e callback menu
    app.add_handler(CallbackQueryHandler(filter_scadenze_cb,      pattern="^FILTER_SCAD;"))
    app.add_handler(CallbackQueryHandler(start_add_manutenzione, pattern="^START_ADD_MANU;"))
    app.add_handler(CallbackQueryHandler(add_manutenzione_field_cb, pattern="^ADD_MANU_FIELD;"))
    app.add_handler(CallbackQueryHandler(save_manutenzione_cb,    pattern="^SAVE_MANU$"))
    app.add_handler(CallbackQueryHandler(callback_menu,          pattern="^MENU$"))

    # Gestione input testuale del form (prima del menu)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, add_manutenzione_field_input),
        group=0
    )
    # Gestione menu testuale (dopo input form)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler),
        group=1
    )

    # Comandi notifiche
    app.add_handler(CommandHandler("attiva_notifiche",   attiva_notifiche_cmd))
    app.add_handler(CommandHandler("disattiva_notifiche",disattiva_notifiche_cmd))
    app.add_handler(CommandHandler("imposta_notifiche",  imposta_notifiche_cmd))

    # Fallback per comandi non riconosciuti
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Auto-start notifiche se non già attive
    global NOTIF_ENABLED, _notif_thread
    if not NOTIF_ENABLED:
        NOTIF_ENABLED = True
        _notif_stop_event.clear()
        _notif_thread = threading.Thread(target=_notifications_loop, daemon=True)
        _notif_thread.start()
        logger.info(f"Notifiche programmate alle {NOTIF_HOUR:02d}:{NOTIF_MINUTE:02d} UTC")

    logger.info("Bot avviato, polling…")
    app.run_polling()

if __name__ == "__main__":
    main()

"""
File: telegram_bot.py
Bot Telegram di OfficinaOnline.

  - /login <utente> <password> (il messaggio con la password viene cancellato)
  - Sessioni salvate nel database: restano valide anche dopo un riavvio
  - Scheda veicolo con prossimo tagliando, scadenze e ultimi interventi
  - Registrazione guidata di interventi e scadenze, aggiornamento km
  - Promemoria giornaliero (ora italiana) di scadenze superate o in arrivo
"""

import logging
import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)
from werkzeug.security import check_password_hash

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("officina-bot")

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Rome"))
ORA_NOTIFICHE = os.environ.get("ORA_NOTIFICHE", "08:00")

MENU = ReplyKeyboardMarkup([["🚗 Veicoli", "📋 Da fare"], ["⚙️ Impostazioni"]],
                           resize_keyboard=True)
ICONA = {"scaduta": "⛔", "imminente": "⚠️", "ok": "✅", None: "•"}
TIPI_INTERVENTO = ["Tagliando", "Manutenzione straordinaria", "Riparazione", "Altro"]
TIPI_SCADENZA = ["Bollo", "Assicurazione", "Revisione", "Tagliando", "Altro"]

# Stati delle conversazioni guidate
(I_TIPO, I_DATA, I_KM, I_NOTE, S_TIPO, S_ALTRO, S_DATA, K_KM) = range(8)


# ──────────────────────────────────────────────────────────────────────────────
# UTILITÀ
# ──────────────────────────────────────────────────────────────────────────────
def oggi():
    return datetime.now(TZ).date()


def data_it(s):
    d = db.parse_date(s)
    return d.strftime("%d/%m/%Y") if d else (s or "—")


def km(n):
    return f"{int(n):,} km".replace(",", ".") if isinstance(n, int) else "—"


def leggi_data(testo):
    """Accetta 31/12/2025, 31-12-2025, 31.12.25, 2025-12-31 oppure 'oggi'."""
    t = testo.strip().lower()
    if t == "oggi":
        return oggi()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def sessione(update: Update):
    return db.tg_session(update.effective_chat.id)


def veicolo_accessibile(sess, vid):
    return db.get_veicolo_by_id(vid, sess["user_id"], is_admin=bool(sess["is_admin"]))


async def rispondi(update: Update, testo, **kw):
    """Risponde sia a messaggi sia a pulsanti inline."""
    kw.setdefault("parse_mode", ParseMode.HTML)
    if update.callback_query:
        try:
            return await update.callback_query.edit_message_text(testo, **kw)
        except BadRequest:
            pass
    return await update.effective_chat.send_message(testo, **kw)


def esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def richiede_login(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        sess = sessione(update)
        if not sess:
            if update.callback_query:
                await update.callback_query.answer()
            await update.effective_chat.send_message(
                "Prima accedi con:\n/login <code>utente</code> <code>password</code>",
                parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        context.user_data["sess"] = sess
        return await handler(update, context)
    return wrapper


# ──────────────────────────────────────────────────────────────────────────────
# IMPEGNI (scadenze + prossimi tagliandi)
# ──────────────────────────────────────────────────────────────────────────────
def impegni_utente(user_id, is_admin, solo_urgenti=True):
    righe = []
    for v in db.get_veicoli(user_id, is_admin=bool(is_admin)):
        for s in db.get_scadenze(v["id"]):
            d = db.parse_date(s["data"])
            righe.append((db.stato_data(d, oggi()), s["data"], v, s["tipo"], None))
        pt = db.prossimo_tagliando(v, db.get_manutenzioni(v["id"]), oggi())
        if pt:
            righe.append((pt["stato"], pt["data"], v, "Tagliando (stima)", pt))
    if solo_urgenti:
        righe = [r for r in righe if r[0] in ("scaduta", "imminente")]
    ordine = {"scaduta": 0, "imminente": 1, "ok": 2, None: 3}
    righe.sort(key=lambda r: (ordine[r[0]], r[1] or ""))
    return righe


def formatta_impegni(righe):
    out = []
    for stato, data, v, tipo, pt in righe:
        extra = f" o {km(pt['km'])}" if pt and pt.get("km") else ""
        out.append(f"{ICONA[stato]} <b>{esc(v['targa'])}</b> · {esc(tipo)} · {data_it(data)}{extra}")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────────────────
# COMANDI BASE
# ──────────────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sess = sessione(update)
    if sess:
        await update.effective_chat.send_message(
            f"Ciao {esc(sess['username'])}! Scegli dal menu qui sotto.",
            reply_markup=MENU, parse_mode=ParseMode.HTML)
    else:
        await update.effective_chat.send_message(
            "Benvenuto nel bot dell'officina.\nAccedi con:\n"
            "/login <code>utente</code> <code>password</code>", parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "<b>Comandi</b>\n"
        "/login utente password – accedi (il messaggio viene cancellato)\n"
        "/veicoli – i tuoi veicoli\n"
        "/dafare – scadenze superate o in arrivo\n"
        "/notifiche on|off – promemoria giornaliero\n"
        "/annulla – interrompe un inserimento\n"
        "/logout – esci",
        parse_mode=ParseMode.HTML)


async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    # Il messaggio contiene la password in chiaro: lo cancello subito
    try:
        await msg.delete()
    except BadRequest:
        pass
    if len(context.args) != 2:
        return await update.effective_chat.send_message("Uso: /login utente password")
    user = db.get_user_by_username(context.args[0])
    if not user or not check_password_hash(user["password"], context.args[1]):
        return await update.effective_chat.send_message("Utente o password non corretti.")
    db.tg_login(update.effective_chat.id, user["id"])
    await update.effective_chat.send_message(
        f"✅ Accesso come <b>{esc(user['username'])}</b>. Ti scriverò ogni giorno alle "
        f"{ORA_NOTIFICHE} se c'è qualcosa in scadenza.",
        reply_markup=MENU, parse_mode=ParseMode.HTML)


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = db.tg_logout(update.effective_chat.id)
    await update.effective_chat.send_message("Disconnesso." if ok else "Non eri collegato.",
                                             reply_markup=ReplyKeyboardRemove())


@richiede_login
async def notifiche_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg not in ("on", "off"):
        stato = "attive" if context.user_data["sess"]["notifiche"] else "disattivate"
        return await update.effective_chat.send_message(
            f"Notifiche {stato} (ogni giorno alle {ORA_NOTIFICHE}).\nUsa /notifiche on oppure /notifiche off")
    db.tg_set_notifiche(update.effective_chat.id, arg == "on")
    await update.effective_chat.send_message(
        "🔔 Notifiche attive." if arg == "on" else "🔕 Notifiche disattivate.")


@richiede_login
async def impostazioni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sess = context.user_data["sess"]
    att = bool(sess["notifiche"])
    kb = Kb([[Btn("🔕 Disattiva notifiche" if att else "🔔 Attiva notifiche",
                  callback_data="NOTIF;" + ("off" if att else "on"))],
             [Btn("🚪 Esci", callback_data="LOGOUT")]])
    await rispondi(update, f"Collegato come <b>{esc(sess['username'])}</b>.\n"
                           f"Notifiche: {'attive' if att else 'disattivate'} ({ORA_NOTIFICHE}).",
                   reply_markup=kb)


async def impostazioni_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "LOGOUT":
        db.tg_logout(update.effective_chat.id)
        return await q.edit_message_text("Disconnesso. Per rientrare: /login utente password")
    db.tg_set_notifiche(update.effective_chat.id, q.data.endswith("on"))
    return await impostazioni(update, context)


# ──────────────────────────────────────────────────────────────────────────────
# VEICOLI E DA FARE
# ──────────────────────────────────────────────────────────────────────────────
@richiede_login
async def veicoli_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sess = context.user_data["sess"]
    vv = db.get_veicoli(sess["user_id"], is_admin=bool(sess["is_admin"]))
    if not vv:
        return await rispondi(update, "Nessun veicolo. Aggiungilo dal sito.")
    kb = [[Btn(f"{v['targa']} · {v['marca'] or ''} {v['modello'] or ''}".strip(),
               callback_data=f"V;{v['id']}")] for v in vv]
    await rispondi(update, "Scegli un veicolo:", reply_markup=Kb(kb))


@richiede_login
async def dafare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sess = context.user_data["sess"]
    righe = impegni_utente(sess["user_id"], sess["is_admin"])
    testo = formatta_impegni(righe) if righe else "✅ Nulla in scadenza nei prossimi 30 giorni."
    await rispondi(update, "<b>Da fare</b>\n" + testo)


def scheda_veicolo(v):
    manut = db.get_manutenzioni(v["id"])
    pt = db.prossimo_tagliando(v, manut, oggi())
    righe = [f"<b>{esc(v['targa'])}</b> · {esc(v['marca'])} {esc(v['modello'])}",
             f"Contachilometri: {km(v['km'])}"]
    if pt:
        extra = f" o a {km(pt['km'])}" if pt.get("km") else ""
        righe.append(f"{ICONA[pt['stato']]} Prossimo tagliando: {data_it(pt['data'])}{extra}")
    sc = db.get_scadenze(v["id"])
    if sc:
        righe.append("\n<b>Scadenze</b>")
        for s in sc:
            st = db.stato_data(db.parse_date(s["data"]), oggi())
            righe.append(f"{ICONA[st]} {esc(s['tipo'])} · {data_it(s['data'])}")
    if manut:
        righe.append("\n<b>Ultimi interventi</b>")
        for m in manut[:3]:
            righe.append(f"• {data_it(m['data'])} · {esc(m['tipo'])}"
                         + (f" · {km(m['km'])}" if m.get("km") else "")
                         + (f"\n   <i>{esc(m['note'])}</i>" if m.get("note") else ""))
    return "\n".join(righe)


@richiede_login
async def veicolo_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid = int(q.data.split(";")[1])
    v = veicolo_accessibile(context.user_data["sess"], vid)
    if not v:
        return await rispondi(update, "Veicolo non trovato.")
    kb = Kb([
        [Btn("🔧 Registra intervento", callback_data=f"ADDI;{vid}"),
         Btn("📅 Aggiungi scadenza", callback_data=f"ADDS;{vid}")],
        [Btn("🔢 Aggiorna km", callback_data=f"KM;{vid}"),
         Btn("⬅️ Veicoli", callback_data="VEICOLI")],
    ])
    await rispondi(update, scheda_veicolo(v), reply_markup=kb)


async def veicoli_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await veicoli_cmd(update, context)


# ──────────────────────────────────────────────────────────────────────────────
# CONVERSAZIONE: REGISTRA INTERVENTO
# ──────────────────────────────────────────────────────────────────────────────
SALTA = Kb([[Btn("Salta", callback_data="SKIP")]])


@richiede_login
async def int_inizio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid = int(q.data.split(";")[1])
    v = veicolo_accessibile(context.user_data["sess"], vid)
    if not v:
        return ConversationHandler.END
    context.user_data["nuovo"] = {"vid": vid, "targa": v["targa"]}
    kb = Kb([[Btn(t, callback_data=f"T;{t}")] for t in TIPI_INTERVENTO])
    await rispondi(update, f"🔧 Nuovo intervento su <b>{esc(v['targa'])}</b>\nChe tipo?",
                   reply_markup=kb)
    return I_TIPO


async def int_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["nuovo"]["tipo"] = q.data.split(";", 1)[1]
    await rispondi(update, "📅 Data? Scrivi ad es. 15/03/2025",
                   reply_markup=Kb([[Btn("Oggi", callback_data="OGGI")]]))
    return I_DATA


async def int_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        d = oggi()
    else:
        d = leggi_data(update.effective_message.text)
        if not d:
            await update.effective_message.reply_text("Data non valida, riprova (es. 15/03/2025).")
            return I_DATA
    context.user_data["nuovo"]["data"] = d.isoformat()
    await rispondi(update, "🔢 Km al momento dell'intervento?", reply_markup=SALTA)
    return I_KM


async def int_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    else:
        val = update.effective_message.text.replace(".", "").replace(" ", "").strip()
        if not val.isdigit():
            await update.effective_message.reply_text("Scrivi solo il numero, es. 152000.")
            return I_KM
        context.user_data["nuovo"]["km"] = int(val)
    await rispondi(update, "📝 Note? (cosa è stato fatto, ricambi, costo…)", reply_markup=SALTA)
    return I_NOTE


async def int_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = context.user_data.pop("nuovo")
    if update.callback_query:
        await update.callback_query.answer()
    else:
        n["note"] = update.effective_message.text.strip()
    db.aggiungi_manutenzione(n["vid"], n["data"], n.get("km"), {}, tipo=n["tipo"],
                             note=n.get("note"))
    await update.effective_chat.send_message(
        f"✅ {esc(n['tipo'])} registrato su <b>{esc(n['targa'])}</b> il {data_it(n['data'])}"
        + (f" a {km(n['km'])}" if n.get("km") else "") + ".\n"
        "Puoi aggiungere i dettagli dei ricambi dal sito.",
        parse_mode=ParseMode.HTML, reply_markup=MENU)
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
# CONVERSAZIONE: AGGIUNGI SCADENZA
# ──────────────────────────────────────────────────────────────────────────────
@richiede_login
async def sc_inizio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid = int(q.data.split(";")[1])
    v = veicolo_accessibile(context.user_data["sess"], vid)
    if not v:
        return ConversationHandler.END
    context.user_data["nuovo"] = {"vid": vid, "targa": v["targa"]}
    kb = Kb([[Btn(t, callback_data=f"T;{t}")] for t in TIPI_SCADENZA])
    await rispondi(update, f"📅 Nuova scadenza per <b>{esc(v['targa'])}</b>\nDi che tipo?",
                   reply_markup=kb)
    return S_TIPO


async def sc_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tipo = q.data.split(";", 1)[1]
    if tipo == "Altro":
        await rispondi(update, "Scrivi la descrizione (es. Collaudo GPL):")
        return S_ALTRO
    context.user_data["nuovo"]["tipo"] = tipo
    await rispondi(update, f"Quando scade il {tipo.lower()}? (es. 31/12/2025)")
    return S_DATA


async def sc_altro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nuovo"]["tipo"] = update.effective_message.text.strip()[:60]
    await update.effective_message.reply_text("Quando scade? (es. 31/12/2025)")
    return S_DATA


async def sc_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = leggi_data(update.effective_message.text)
    if not d:
        await update.effective_message.reply_text("Data non valida, riprova (es. 31/12/2025).")
        return S_DATA
    n = context.user_data.pop("nuovo")
    db.aggiungi_scadenza(n["vid"], n["tipo"], d.isoformat())
    await update.effective_message.reply_text(
        f"✅ {n['tipo']} di {n['targa']} in scadenza il {d.strftime('%d/%m/%Y')}.",
        reply_markup=MENU)
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
# CONVERSAZIONE: AGGIORNA KM
# ──────────────────────────────────────────────────────────────────────────────
@richiede_login
async def km_inizio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid = int(q.data.split(";")[1])
    v = veicolo_accessibile(context.user_data["sess"], vid)
    if not v:
        return ConversationHandler.END
    context.user_data["nuovo"] = {"vid": vid, "targa": v["targa"]}
    await rispondi(update, f"Km attuali di <b>{esc(v['targa'])}</b>: {km(v['km'])}\n"
                           "Scrivi il nuovo valore:")
    return K_KM


async def km_valore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.effective_message.text.replace(".", "").replace(" ", "").strip()
    if not val.isdigit():
        await update.effective_message.reply_text("Scrivi solo il numero, es. 152000.")
        return K_KM
    n = context.user_data.pop("nuovo")
    db.aggiorna_km_se_maggiore(n["vid"], int(val))
    v = db.get_veicolo_by_id(n["vid"], is_admin=True)
    await update.effective_message.reply_text(
        f"✅ {n['targa']}: {km(v['km'])}" +
        ("" if v["km"] == int(val) else " (il valore inserito era più basso, non l'ho cambiato)"),
        reply_markup=MENU)
    return ConversationHandler.END


async def annulla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("nuovo", None)
    await update.effective_chat.send_message("Operazione annullata.", reply_markup=MENU)
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
# PROMEMORIA GIORNALIERO
# ──────────────────────────────────────────────────────────────────────────────
async def promemoria(context: ContextTypes.DEFAULT_TYPE):
    for chat in db.tg_chats_notifiche():
        righe = impegni_utente(chat["user_id"], chat["is_admin"])
        if not righe:
            continue
        try:
            await context.bot.send_message(
                chat["chat_id"], "📣 <b>Promemoria officina</b>\n" + formatta_impegni(righe),
                parse_mode=ParseMode.HTML)
        except Exception as e:  # chat bloccata, ecc.
            log.warning("Promemoria non inviato a %s: %s", chat["chat_id"], e)


async def sconosciuto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message("Non ho capito. Usa il menu oppure /help.")


# ──────────────────────────────────────────────────────────────────────────────
# AVVIO
# ──────────────────────────────────────────────────────────────────────────────
def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        log.error("TELEGRAM_TOKEN mancante: il bot non parte (il sito funziona lo stesso).")
        return
    db.init_db()
    app = ApplicationBuilder().token(token).build()

    testo = filters.TEXT & ~filters.COMMAND
    fallback = [CommandHandler("annulla", annulla)]
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(int_inizio, pattern=r"^ADDI;")],
        states={
            I_TIPO: [CallbackQueryHandler(int_tipo, pattern=r"^T;")],
            I_DATA: [CallbackQueryHandler(int_data, pattern=r"^OGGI$"), MessageHandler(testo, int_data)],
            I_KM: [CallbackQueryHandler(int_km, pattern=r"^SKIP$"), MessageHandler(testo, int_km)],
            I_NOTE: [CallbackQueryHandler(int_note, pattern=r"^SKIP$"), MessageHandler(testo, int_note)],
        }, fallbacks=fallback, conversation_timeout=900))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(sc_inizio, pattern=r"^ADDS;")],
        states={
            S_TIPO: [CallbackQueryHandler(sc_tipo, pattern=r"^T;")],
            S_ALTRO: [MessageHandler(testo, sc_altro)],
            S_DATA: [MessageHandler(testo, sc_data)],
        }, fallbacks=fallback, conversation_timeout=900))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(km_inizio, pattern=r"^KM;")],
        states={K_KM: [MessageHandler(testo, km_valore)]},
        fallbacks=fallback, conversation_timeout=900))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("logout", logout))
    app.add_handler(CommandHandler("veicoli", veicoli_cmd))
    app.add_handler(CommandHandler("dafare", dafare_cmd))
    app.add_handler(CommandHandler("notifiche", notifiche_cmd))
    app.add_handler(CommandHandler("annulla", annulla))
    app.add_handler(CallbackQueryHandler(veicolo_cb, pattern=r"^V;"))
    app.add_handler(CallbackQueryHandler(veicoli_cb, pattern=r"^VEICOLI$"))
    app.add_handler(CallbackQueryHandler(impostazioni_cb, pattern=r"^(NOTIF;|LOGOUT$)"))
    app.add_handler(MessageHandler(filters.Regex(r"^🚗 Veicoli$"), veicoli_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^📋 Da fare$"), dafare_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^⚙️ Impostazioni$"), impostazioni))
    app.add_handler(MessageHandler(filters.ALL, sconosciuto))

    h, m = map(int, ORA_NOTIFICHE.split(":"))
    app.job_queue.run_daily(promemoria, time(hour=h, minute=m, tzinfo=TZ), name="promemoria")
    log.info("Bot avviato. Promemoria ogni giorno alle %s (%s).", ORA_NOTIFICHE, TZ)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

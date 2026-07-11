"""Handler dei comandi Telegram."""

import logging
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .providers import PROVIDERS, get_provider
from .tracker import RECAP_LOOKBACK, format_passage, format_recap

logger = logging.getLogger(__name__)

MAX_EVENT_BUTTONS = 12
MAX_RUNNER_BUTTONS = 10

# I callback_data di Telegram sono limitati a 64 byte: due UUID non ci
# stanno, quindi i bottoni portano solo un indice dentro liste temporanee
# salvate in chat_data ("search_results", "followed").

TELEGRAM_MESSAGE_LIMIT = 4096


async def _send_long(bot, chat_id: int, text: str) -> None:
    """Invia un testo spezzandolo sui newline se supera il limite Telegram."""
    while text:
        if len(text) <= TELEGRAM_MESSAGE_LIMIT:
            await bot.send_message(chat_id, text)
            return
        cut = text.rfind("\n", 0, TELEGRAM_MESSAGE_LIMIT)
        if cut <= 0:
            cut = TELEGRAM_MESSAGE_LIMIT
        await bot.send_message(chat_id, text[:cut])
        text = text[cut:].lstrip("\n")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Ciao! Ti mando aggiornamenti live su un corridore di una gara trail.\n\n"
        "1. /gara — scegli la gara\n"
        "2. /cerca <nome o pettorale> — trova il corridore\n"
        "3. Lo selezioni e ti avviso ad ogni checkpoint.\n\n"
        "Comandi utili: /seguiti per gestire chi segui, "
        "/stato per l'ultima posizione nota.\n"
        "Inizia con /gara"
    )


async def gara(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = []
    for provider in PROVIDERS.values():
        try:
            events.extend(await provider.list_events())
        except Exception:
            logger.exception("Errore nel recupero gare (%s)", provider.name)
    events.sort(key=lambda e: (e.started_at is None, e.started_at))
    if not events:
        await update.message.reply_text("Nessuna gara live al momento.")
        return

    keyboard = []
    for event in events[:MAX_EVENT_BUTTONS]:
        dates = ""
        if event.started_at:
            dates = f" — dal {event.started_at:%d/%m}"
            if event.ended_at:
                dates += f" al {event.ended_at:%d/%m}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{event.name}{dates}",
                    callback_data=f"ev|{event.provider}|{event.id}",
                )
            ]
        )
    await update.message.reply_text(
        "Scegli la gara:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def event_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, provider_name, event_id = query.data.split("|", 2)

    provider = get_provider(provider_name)
    events = await provider.list_events()
    event = next((e for e in events if e.id == event_id), None)
    if event is None:
        await query.edit_message_text("Gara non trovata, riprova con /gara.")
        return

    context.chat_data["event"] = {
        "provider": provider_name,
        "id": event.id,
        "name": event.name,
        "timezone": event.timezone,
        "ended_at": event.ended_at.isoformat() if event.ended_at else None,
    }
    await query.edit_message_text(
        f"Gara selezionata: {event.name}\n\n"
        "Ora cerca il corridore con /cerca <nome o pettorale>."
    )


async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    event = context.chat_data.get("event")
    if not event:
        await update.message.reply_text("Prima scegli la gara con /gara.")
        return
    query_text = " ".join(context.args).strip()
    if not query_text:
        await update.message.reply_text(
            "Scrivi nome o pettorale dopo il comando, es:\n/cerca Rossi"
        )
        return

    provider = get_provider(event["provider"])
    try:
        runners = await provider.search_runners(event["id"], query_text)
    except Exception:
        logger.exception("Errore nella ricerca corridori")
        await update.message.reply_text("Errore nella ricerca, riprova tra poco.")
        return
    if not runners:
        await update.message.reply_text(
            f"Nessun corridore trovato per \"{query_text}\" in {event['name']}."
        )
        return

    results = [
        {
            "provider": r.provider,
            "event_id": r.event_id,
            "event_name": event["name"],
            "timezone": event["timezone"],
            "event_end": event.get("ended_at"),
            "id": r.id,
            "number": r.number,
            "name": r.name,
            "country": r.country,
        }
        for r in runners[:MAX_RUNNER_BUTTONS]
    ]
    context.chat_data["search_results"] = results

    keyboard = [
        [
            InlineKeyboardButton(
                f"#{r['number']} {r['name']}"
                + (f" ({r['country']})" if r["country"] else ""),
                callback_data=f"run|{i}",
            )
        ]
        for i, r in enumerate(results)
    ]
    extra = ""
    if len(runners) > MAX_RUNNER_BUTTONS:
        extra = (
            f"\n(mostro i primi {MAX_RUNNER_BUTTONS} di {len(runners)}: "
            "affina la ricerca se non lo vedi)"
        )
    await update.message.reply_text(
        f"Risultati per \"{query_text}\":{extra}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def runner_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    results = context.chat_data.get("search_results") or []
    index = int(query.data.split("|", 1)[1])
    if index >= len(results):
        await query.edit_message_text("Ricerca scaduta, rifai /cerca.")
        return
    runner = results[index]

    followed = context.chat_data.setdefault("followed", [])
    if any(
        f["id"] == runner["id"] and f["event_id"] == runner["event_id"]
        for f in followed
    ):
        await query.edit_message_text(
            f"Segui già {runner['name']} (#{runner['number']})."
        )
        return
    follow = dict(runner, last_seen=None)
    followed.append(follow)

    await query.edit_message_text(
        f"✅ Ora segui {runner['name']} (#{runner['number']}) "
        f"in {runner['event_name']}.\n"
        "Recupero i passaggi già registrati..."
    )

    # Riepilogo di tutti i passaggi dall'inizio della gara; da qui in poi
    # il tracker notifica solo quelli nuovi (last_seen = ultimo passaggio).
    since = datetime.now(UTC) - RECAP_LOOKBACK
    chat_id = query.message.chat_id
    try:
        provider = get_provider(runner["provider"])
        passages = await provider.get_updates(runner["event_id"], since)
        mine = [p for p in passages if p.runner_id == runner["id"]]
    except Exception:
        logger.exception("Recupero riepilogo fallito per %s", runner["name"])
        follow["last_seen"] = since.isoformat()
        await context.bot.send_message(
            chat_id,
            "Non riesco a recuperare i passaggi già registrati; "
            "ti avviso comunque per quelli nuovi.",
        )
        return

    if mine:
        follow["last_seen"] = mine[-1].passed_at.isoformat()
        await _send_long(context.bot, chat_id, format_recap(mine, follow))
    else:
        follow["last_seen"] = since.isoformat()
        await context.bot.send_message(
            chat_id,
            "Nessun passaggio registrato finora: "
            "ti avviso al primo checkpoint.\n"
            "/seguiti per gestire i corridori seguiti.",
        )


async def seguiti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    followed = context.chat_data.get("followed") or []
    if not followed:
        await update.message.reply_text(
            "Non segui nessun corridore. Usa /gara e poi /cerca."
        )
        return
    keyboard = [
        [
            InlineKeyboardButton(
                f"❌ #{f['number']} {f['name']} — {f['event_name']}",
                callback_data=f"unf|{i}",
            )
        ]
        for i, f in enumerate(followed)
    ]
    await update.message.reply_text(
        "Corridori seguiti (tocca per smettere di seguire):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def stato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    followed = context.chat_data.get("followed") or []
    if not followed:
        await update.message.reply_text(
            "Non segui nessun corridore. Usa /gara e poi /cerca."
        )
        return
    await update.message.reply_text("Controllo l'ultima posizione nota...")
    lines = []
    for follow in followed:
        provider = get_provider(follow["provider"])
        try:
            passage = await provider.get_last_passage(
                follow["event_id"], follow["id"]
            )
        except Exception:
            logger.exception("Errore /stato per %s", follow["name"])
            passage = None
        if passage:
            lines.append(format_passage(passage, follow))
        else:
            lines.append(
                f"🏃 {follow['name']} (#{follow['number']})\n"
                f"📍 Nessun passaggio registrato — {follow['event_name']}"
            )
    await update.message.reply_text("\n\n".join(lines))


async def unfollow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    followed = context.chat_data.get("followed") or []
    index = int(query.data.split("|", 1)[1])
    if index >= len(followed):
        await query.edit_message_text("Elenco cambiato, rifai /seguiti.")
        return
    removed = followed.pop(index)
    await query.edit_message_text(
        f"Non segui più {removed['name']} (#{removed['number']})."
    )


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gara", gara))
    app.add_handler(CommandHandler("cerca", cerca))
    app.add_handler(CommandHandler("seguiti", seguiti))
    app.add_handler(CommandHandler("stato", stato))
    app.add_handler(CallbackQueryHandler(event_selected, pattern=r"^ev\|"))
    app.add_handler(CallbackQueryHandler(runner_selected, pattern=r"^run\|"))
    app.add_handler(CallbackQueryHandler(unfollow, pattern=r"^unf\|"))

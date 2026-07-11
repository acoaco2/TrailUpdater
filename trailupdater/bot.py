"""Handler dei comandi Telegram."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .providers import get_provider

logger = logging.getLogger(__name__)

MAX_EVENT_BUTTONS = 12
MAX_RUNNER_BUTTONS = 10

# I callback_data di Telegram sono limitati a 64 byte: due UUID non ci
# stanno, quindi i bottoni portano solo un indice dentro liste temporanee
# salvate in chat_data ("search_results", "followed").


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Ciao! Ti mando aggiornamenti live su un corridore di una gara trail.\n\n"
        "1. /gara — scegli la gara\n"
        "2. /cerca <nome o pettorale> — trova il corridore\n"
        "3. Lo selezioni e ti avviso ad ogni checkpoint.\n\n"
        "Comandi utili: /seguiti per vedere chi stai seguendo.\n"
        "Inizia con /gara"
    )


async def gara(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    provider = get_provider("owaka")
    try:
        events = await provider.list_events()
    except Exception:
        logger.exception("Errore nel recupero delle gare")
        await update.message.reply_text(
            "Non riesco a recuperare l'elenco delle gare, riprova tra poco."
        )
        return
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
    followed.append(dict(runner, last_seen=None))

    await query.edit_message_text(
        f"✅ Ora segui {runner['name']} (#{runner['number']}) "
        f"in {runner['event_name']}.\n\n"
        "Riceverai un messaggio ad ogni passaggio ai checkpoint "
        "(le notifiche si attivano nel prossimo step di sviluppo).\n"
        "/seguiti per gestire i corridori seguiti."
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
    app.add_handler(CallbackQueryHandler(event_selected, pattern=r"^ev\|"))
    app.add_handler(CallbackQueryHandler(runner_selected, pattern=r"^run\|"))
    app.add_handler(CallbackQueryHandler(unfollow, pattern=r"^unf\|"))

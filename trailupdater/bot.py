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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Ciao! Ti mando aggiornamenti live su un corridore di una gara trail.\n\n"
        "1. /gara — scegli la gara\n"
        "2. /cerca <nome o pettorale> — trova il corridore\n"
        "3. Lo selezioni e ti avviso ad ogni checkpoint.\n\n"
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

    # Recupera il nome per conferma (dalla lista, già in cache lato provider).
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
    await update.message.reply_text(
        "La ricerca del corridore arriva nel prossimo step dello sviluppo. 🚧"
    )


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gara", gara))
    app.add_handler(CommandHandler("cerca", cerca))
    app.add_handler(CallbackQueryHandler(event_selected, pattern=r"^ev\|"))

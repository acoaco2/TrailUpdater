"""Avvio del bot TrailUpdater (long polling, nessun webhook)."""

import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import Application, ApplicationBuilder, PicklePersistence

from trailupdater.bot import register_handlers
from trailupdater.tracker import schedule

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _notify_followers(app: Application, text: str) -> None:
    """Manda un avviso a tutte le chat che stanno seguendo qualcuno."""
    for chat_id, data in app.chat_data.items():
        if not data.get("followed"):
            continue
        try:
            await app.bot.send_message(chat_id, text)
        except Exception:
            logging.exception("Avviso di servizio fallito a chat %s", chat_id)


async def _on_startup(app: Application) -> None:
    await _notify_followers(
        app, "✅ Bot avviato: riprendo il monitoraggio dei corridori seguiti."
    )


async def _on_stop(app: Application) -> None:
    # post_stop: il bot è ancora connesso, a differenza di post_shutdown.
    await _notify_followers(
        app,
        "⚠️ Bot spento: le notifiche sono sospese finché non viene riavviato.",
    )


def main() -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante: crealo nel file .env")

    # Stato (gara selezionata, corridori seguiti) su disco: sopravvive ai riavvii.
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    persistence = PicklePersistence(filepath=data_dir / "bot_state.pkl")

    app = (
        ApplicationBuilder()
        .token(token)
        .persistence(persistence)
        .post_init(_on_startup)
        .post_stop(_on_stop)
        .build()
    )
    register_handlers(app)
    schedule(app)
    logging.info("Bot avviato, in ascolto...")
    # Da Python 3.14 asyncio.get_event_loop() non crea più il loop da solo,
    # ma python-telegram-bot 21.x ci fa affidamento dentro run_polling.
    asyncio.set_event_loop(asyncio.new_event_loop())
    # Oltre ai segnali standard (Ctrl+C, kill), intercetta anche la
    # chiusura del terminale (SIGHUP, solo su macOS/Linux) per riuscire
    # a mandare l'avviso di spegnimento.
    stop_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGABRT"):
        stop_signals.append(signal.SIGABRT)
    if hasattr(signal, "SIGHUP"):
        stop_signals.append(signal.SIGHUP)
    app.run_polling(stop_signals=stop_signals)


if __name__ == "__main__":
    main()

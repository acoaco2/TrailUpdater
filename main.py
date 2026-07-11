"""Avvio del bot TrailUpdater (long polling, nessun webhook)."""

import logging
import os

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

from trailupdater.bot import register_handlers

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante: crealo nel file .env")

    app = ApplicationBuilder().token(token).build()
    register_handlers(app)
    logging.info("Bot avviato, in ascolto...")
    app.run_polling()


if __name__ == "__main__":
    main()

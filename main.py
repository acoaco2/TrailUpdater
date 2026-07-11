"""Avvio del bot TrailUpdater (long polling, nessun webhook)."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, PicklePersistence

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

    # Stato (gara selezionata, corridori seguiti) su disco: sopravvive ai riavvii.
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    persistence = PicklePersistence(filepath=data_dir / "bot_state.pkl")

    app = ApplicationBuilder().token(token).persistence(persistence).build()
    register_handlers(app)
    logging.info("Bot avviato, in ascolto...")
    app.run_polling()


if __name__ == "__main__":
    main()

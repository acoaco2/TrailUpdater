"""Polling dei checkpoint e invio delle notifiche.

Gira come job ricorrente del bot: raggruppa i corridori seguiti da tutte
le chat per (provider, evento), chiede al provider i passaggi nuovi e
notifica ogni chat interessata. Il progresso per corridore è salvato in
`last_seen` dentro la voce "followed" della chat.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from .providers import get_provider

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 180
# Quando si inizia a seguire un corridore, quanto indietro guardare:
# così arriva subito l'ultimo passaggio recente invece del silenzio.
FIRST_LOOKBACK = timedelta(hours=2)


def _format_message(passage, follow: dict) -> str:
    tz = ZoneInfo(follow.get("timezone") or "UTC")
    local_time = passage.passed_at.astimezone(tz)
    progress = ""
    if passage.checkpoint_position and passage.checkpoint_total:
        progress = f" ({passage.checkpoint_position}/{passage.checkpoint_total})"
    return (
        f"🏃 {follow['name']} (#{follow['number']})\n"
        f"📍 {passage.checkpoint_name}{progress}\n"
        f"🕐 {local_time:%H:%M} — {follow['event_name']}"
    )


async def poll_updates(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_poll(context.application)


async def run_poll(app: Application) -> None:
    # (provider, event_id) -> [(chat_id, voce followed)]
    groups: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for chat_id, data in app.chat_data.items():
        for follow in data.get("followed", []):
            key = (follow["provider"], follow["event_id"])
            groups.setdefault(key, []).append((chat_id, follow))
    if not groups:
        return

    now = datetime.now(UTC)
    for (provider_name, event_id), entries in groups.items():
        # Inizializza i nuovi seguiti e calcola da quando interrogare.
        for _, follow in entries:
            if not follow.get("last_seen"):
                follow["last_seen"] = (now - FIRST_LOOKBACK).isoformat()
        since = min(datetime.fromisoformat(f["last_seen"]) for _, f in entries)

        provider = get_provider(provider_name)
        try:
            passages = await provider.get_updates(event_id, since)
        except Exception:
            logger.exception(
                "Polling fallito per %s/%s", provider_name, event_id
            )
            continue

        touched_chats: set[int] = set()
        for chat_id, follow in entries:
            last_seen = datetime.fromisoformat(follow["last_seen"])
            new = [
                p
                for p in passages
                if p.runner_id == follow["id"] and p.passed_at > last_seen
            ]
            if not new:
                continue
            for passage in new:
                try:
                    await app.bot.send_message(
                        chat_id, _format_message(passage, follow)
                    )
                except Exception:
                    logger.exception("Invio notifica fallito a chat %s", chat_id)
                    break
                follow["last_seen"] = passage.passed_at.isoformat()
                touched_chats.add(chat_id)

        # Le modifiche fatte da un job non passano dal normale flusso degli
        # update, quindi la persistenza va aggiornata esplicitamente.
        if app.persistence:
            for chat_id in touched_chats:
                await app.persistence.update_chat_data(
                    chat_id, app.chat_data[chat_id]
                )


def schedule(app: Application) -> None:
    app.job_queue.run_repeating(
        poll_updates, interval=POLL_INTERVAL_SECONDS, first=10
    )

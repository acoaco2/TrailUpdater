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
# Alla selezione il bot manda il riepilogo dei passaggi già avvenuti e
# imposta last_seen; questo lookback resta solo come rete di sicurezza
# per voci "followed" salvate senza last_seen (es. dati di versioni vecchie).
FIRST_LOOKBACK = timedelta(hours=2)
# Quanto indietro guardare per il riepilogo iniziale: copre l'intera
# durata anche delle gare più lunghe (TOR450: ~10 giorni).
RECAP_LOOKBACK = timedelta(days=14)
# Quanti giorni dopo la fine della gara smettere di seguire i corridori.
CLEANUP_GRACE = timedelta(days=3)


def format_passage(passage, follow: dict) -> str:
    tz = ZoneInfo(follow.get("timezone") or "UTC")
    local_time = passage.passed_at.astimezone(tz)
    when = f"🕐 {local_time:%H:%M} — {follow['event_name']}"
    who = f"{follow['name']} (#{follow['number']})"
    if passage.kind == "finish":
        return (
            f"🏁 {who} ha tagliato il traguardo!\n"
            f"📍 {passage.checkpoint_name}\n{when}"
        )
    if passage.kind == "dnf":
        return (
            f"🔴 {who} risulta ritirato/a.\n{when}\n"
            "(a volte è un errore di cronometraggio: incrocia le dita)"
        )
    progress = ""
    if passage.checkpoint_position and passage.checkpoint_total:
        progress = f" ({passage.checkpoint_position}/{passage.checkpoint_total})"
    return f"🏃 {who}\n📍 {passage.checkpoint_name}{progress}\n{when}"


def format_recap(passages, follow: dict) -> str:
    """Riepilogo compatto di tutti i passaggi già registrati."""
    tz = ZoneInfo(follow.get("timezone") or "UTC")
    lines = [
        f"📋 {follow['name']} (#{follow['number']}) — {follow['event_name']}",
        "Passaggi finora:",
    ]
    for p in passages:
        local_time = p.passed_at.astimezone(tz)
        progress = ""
        if p.checkpoint_position and p.checkpoint_total:
            progress = f" ({p.checkpoint_position}/{p.checkpoint_total})"
        if p.kind == "finish":
            emoji, name = "🏁", f"{p.checkpoint_name} — ARRIVATO/A!"
        elif p.kind == "dnf":
            emoji, name = "🔴", "Ritiro"
        else:
            emoji, name = "📍", p.checkpoint_name
        lines.append(f"{emoji} {local_time:%d/%m %H:%M} — {name}{progress}")
    lines.append("\nDa adesso ti avviso ad ogni nuovo passaggio.")
    return "\n".join(lines)


async def poll_updates(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_poll(context.application)


async def _cleanup_ended(app: Application) -> None:
    """Smette di seguire i corridori di gare finite da giorni."""
    today = datetime.now(UTC).date()
    for chat_id, data in app.chat_data.items():
        followed = data.get("followed") or []
        expired = [
            f
            for f in followed
            if f.get("event_end")
            and datetime.fromisoformat(f["event_end"]).date() + CLEANUP_GRACE < today
        ]
        if not expired:
            continue
        for follow in expired:
            followed.remove(follow)
            try:
                await app.bot.send_message(
                    chat_id,
                    f"🗑 {follow['event_name']} è finita da qualche giorno: "
                    f"ho smesso di seguire {follow['name']} "
                    f"(#{follow['number']}).",
                )
            except Exception:
                logger.exception("Notifica pulizia fallita a chat %s", chat_id)
        if app.persistence:
            await app.persistence.update_chat_data(chat_id, data)


async def run_poll(app: Application) -> None:
    await _cleanup_ended(app)

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
                        chat_id, format_passage(passage, follow)
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

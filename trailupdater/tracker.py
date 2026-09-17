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


def _fmt_duration(delta: timedelta) -> str:
    hours, seconds = divmod(int(delta.total_seconds()), 3600)
    minutes = seconds // 60
    return f"{hours}h{minutes:02d}" if hours else f"{minutes} min"


def _checkpoint_label(passage) -> str:
    """Nome checkpoint con progresso e km, es. "Youlaz (5/15, km 40.4)"."""
    details = []
    if passage.checkpoint_position and passage.checkpoint_total:
        details.append(f"{passage.checkpoint_position}/{passage.checkpoint_total}")
    if passage.distance_m:
        details.append(f"km {passage.distance_m / 1000:.1f}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{passage.checkpoint_name}{suffix}"


def _pace_line(passage) -> str | None:
    """Ritmo sul tratto dal checkpoint precedente, se calcolabile."""
    if not passage.prev_passed_at:
        return None
    elapsed = passage.passed_at - passage.prev_passed_at
    if elapsed <= timedelta(0):
        return None
    stretch = (
        f"{passage.prev_checkpoint_name} → {passage.checkpoint_name}"
        if passage.prev_checkpoint_name
        else "dal checkpoint precedente"
    )
    if passage.distance_m and passage.prev_distance_m is not None:
        km = (passage.distance_m - passage.prev_distance_m) / 1000
        if km > 0:
            speed = km / (elapsed.total_seconds() / 3600)
            return (
                f"⏱ {stretch}: {km:.1f} km in {_fmt_duration(elapsed)}"
                f" ({speed:.1f} km/h)"
            )
    return f"⏱ {stretch}: {_fmt_duration(elapsed)}"


def _eta_line(passage, tz: ZoneInfo) -> str | None:
    """Stima d'arrivo al prossimo checkpoint, se disponibile."""
    if not passage.next_checkpoint_name:
        return None
    where = passage.next_checkpoint_name
    if passage.next_distance_m:
        # Quanto manca al prossimo checkpoint, oltre al km di percorso.
        if passage.distance_m is not None:
            gap = (passage.next_distance_m - passage.distance_m) / 1000
            if gap > 0:
                where += f" tra {gap:.1f} km"
        where += f" (km {passage.next_distance_m / 1000:.1f})"
    if not passage.eta_next:
        return f"⏳ Prossimo: {where}"
    eta_local = passage.eta_next.astimezone(tz)
    return f"⏳ Prossimo: {where} — stimato ~{eta_local:%H:%M}"


def format_passage(passage, follow: dict) -> str:
    tz = ZoneInfo(follow.get("timezone") or "UTC")
    local_time = passage.passed_at.astimezone(tz)
    when = f"🕐 {local_time:%H:%M} — {follow['event_name']}"
    who = f"{follow['name']} (#{follow['number']})"
    rank = f" — {passage.rank}°" if passage.rank else ""
    if passage.kind == "finish":
        return (
            f"🏁 {who} ha tagliato il traguardo{rank}!\n"
            f"📍 {_checkpoint_label(passage)}\n{when}"
        )
    if passage.kind == "dnf":
        place = ""
        if passage.checkpoint_name and passage.checkpoint_name != "ritiro":
            place = f" a {passage.checkpoint_name}"
            if passage.distance_m:
                place += f" (km {passage.distance_m / 1000:.1f})"
        return (
            f"🔴 {who} risulta ritirato/a{place}.\n{when}\n"
            "(a volte è un errore di cronometraggio: incrocia le dita)"
        )
    lines = [f"🏃 {who}{rank}", f"📍 {_checkpoint_label(passage)}", when]
    pace = _pace_line(passage)
    if pace:
        lines.append(pace)
    eta = _eta_line(passage, tz)
    if eta:
        lines.append(eta)
    return "\n".join(lines)


def format_recap(passages, follow: dict) -> str:
    """Riepilogo compatto di tutti i passaggi già registrati."""
    tz = ZoneInfo(follow.get("timezone") or "UTC")
    lines = [
        f"📋 {follow['name']} (#{follow['number']}) — {follow['event_name']}",
        "Passaggi finora:",
    ]
    for p in passages:
        local_time = p.passed_at.astimezone(tz)
        if p.kind == "finish":
            emoji, label = "🏁", f"{_checkpoint_label(p)} — ARRIVATO/A!"
        elif p.kind == "dnf":
            emoji, label = "🔴", "Ritiro"
            if p.checkpoint_name and p.checkpoint_name != "ritiro":
                label = f"Ritiro a {p.checkpoint_name}"
        else:
            emoji, label = "📍", _checkpoint_label(p)
        rank = f" — {p.rank}°" if p.rank else ""
        lines.append(f"{emoji} {local_time:%d/%m %H:%M} — {label}{rank}")
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

"""Modelli normalizzati, indipendenti dalla piattaforma di tracking."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Event:
    provider: str          # es. "owaka"
    id: str                # id nativo del provider
    name: str
    started_at: date | None
    ended_at: date | None
    timezone: str          # es. "Europe/Rome"


@dataclass(frozen=True)
class Runner:
    provider: str
    event_id: str
    id: str                # id nativo del provider
    number: str            # pettorale
    name: str
    country: str | None


@dataclass(frozen=True)
class CheckpointPassage:
    provider: str
    event_id: str
    runner_id: str
    checkpoint_id: str
    checkpoint_name: str
    passed_at: datetime    # UTC
    # Ordine del checkpoint sul percorso (es. 37 di 79), se noto.
    checkpoint_position: int | None = None
    checkpoint_total: int | None = None
    # "checkpoint" (passaggio normale), "finish" (traguardo), "dnf" (ritiro).
    kind: str = "checkpoint"
    # Arricchimenti opzionali (dipendono da cosa espone la piattaforma):
    rank: int | None = None              # posizione a questo checkpoint
    distance_m: int | None = None        # distanza dal via di questo checkpoint
    prev_checkpoint_name: str | None = None
    prev_passed_at: datetime | None = None
    prev_distance_m: int | None = None
    next_checkpoint_name: str | None = None
    next_distance_m: int | None = None
    eta_next: datetime | None = None     # stima arrivo al prossimo checkpoint

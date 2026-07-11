"""Arricchimento dei passaggi, comune a tutti i provider.

Partendo dai passaggi grezzi di TUTTI i corridori di una gara calcola,
per ogni passaggio da notificare:

- la posizione a quel checkpoint (quanti ci sono passati prima);
- il checkpoint precedente del corridore (per il ritmo del tratto);
- il prossimo checkpoint e una stima d'arrivo, usando il tempo mediano
  impiegato su quel tratto dai corridori che l'hanno già percorso.

I provider devono solo normalizzare i loro dati in questa forma:

- ``raw``: runner_id -> lista di (station_key, passed_at, kind, checkpoint_id)
  ordinata per orario; ``kind`` è "checkpoint", "finish" o "dnf".
- ``stations``: station_key -> (nome, distanza_in_metri | None), con
  station_key intero crescente lungo il percorso (1 = partenza).
"""

from bisect import bisect_right
from datetime import datetime, timedelta
from statistics import median

from ..models import CheckpointPassage

# Sotto questo numero di corridori osservati su un tratto la stima
# d'arrivo non è affidabile e viene omessa.
MIN_ETA_SAMPLES = 3

RawEntry = tuple[int | None, datetime, str, str]


def build_passages(
    provider: str,
    event_id: str,
    raw: dict[str, list[RawEntry]],
    stations: dict[int, tuple[str, int | None]],
    since: datetime,
) -> list[CheckpointPassage]:
    finish_key = max(stations) if stations else None
    station_keys = sorted(stations)

    # Orari di passaggio per postazione (solo passaggi cronometrati).
    station_times: dict[int, list[datetime]] = {}
    # Tempi osservati su ogni tratto percorso senza checkpoint intermedi.
    segment_times: dict[tuple[int, int], list[timedelta]] = {}
    for entries in raw.values():
        timed = [(k, t) for k, t, kind, _ in entries if k is not None and kind != "dnf"]
        for key, passed_at in timed:
            station_times.setdefault(key, []).append(passed_at)
        for (k1, t1), (k2, t2) in zip(timed, timed[1:]):
            if k2 > k1 and t2 > t1:
                segment_times.setdefault((k1, k2), []).append(t2 - t1)
    for times in station_times.values():
        times.sort()

    def eta_for(key: int, passed_at: datetime) -> tuple[int | None, datetime | None]:
        """(prossima postazione, stima d'arrivo) dopo la postazione key."""
        following = [k for k in station_keys if k > key]
        if not following:
            return None, None
        next_key = following[0]
        samples = segment_times.get((key, next_key), [])
        if len(samples) < MIN_ETA_SAMPLES:
            return next_key, None
        return next_key, passed_at + median(samples)

    passages: list[CheckpointPassage] = []
    for runner_id, entries in raw.items():
        prev: tuple[int, datetime] | None = None
        for key, passed_at, kind, checkpoint_id in entries:
            if kind == "dnf":
                if passed_at > since:
                    passages.append(
                        CheckpointPassage(
                            provider=provider,
                            event_id=event_id,
                            runner_id=runner_id,
                            checkpoint_id=checkpoint_id,
                            checkpoint_name="ritiro",
                            passed_at=passed_at,
                            kind="dnf",
                        )
                    )
                continue
            if key is None:
                continue
            this_prev, prev = prev, (key, passed_at)
            if passed_at <= since:
                continue

            name, distance = stations.get(key, ("checkpoint", None))
            rank = bisect_right(station_times.get(key, []), passed_at)
            if key == finish_key:
                kind = "finish"
            next_key = eta = None
            if kind == "checkpoint":
                next_key, eta = eta_for(key, passed_at)
            prev_name = prev_time = prev_dist = None
            if this_prev:
                prev_name, prev_dist = stations.get(this_prev[0], (None, None))
                prev_time = this_prev[1]
            next_name = next_dist = None
            if next_key is not None:
                next_name, next_dist = stations[next_key]

            passages.append(
                CheckpointPassage(
                    provider=provider,
                    event_id=event_id,
                    runner_id=runner_id,
                    checkpoint_id=checkpoint_id,
                    checkpoint_name=name,
                    passed_at=passed_at,
                    checkpoint_position=key,
                    checkpoint_total=finish_key,
                    kind=kind,
                    rank=rank or None,
                    distance_m=distance,
                    prev_checkpoint_name=prev_name,
                    prev_passed_at=prev_time,
                    prev_distance_m=prev_dist,
                    next_checkpoint_name=next_name,
                    next_distance_m=next_dist,
                    eta_next=eta,
                )
            )

    passages.sort(key=lambda p: p.passed_at)
    return passages

"""Provider per la piattaforma Owaka (https://owaka.live).

API JSON pubblica su https://api.owaka.live — endpoint documentati in
ARCHITECTURE.md. Serve uno User-Agent da browser, nessuna autenticazione.
"""

from datetime import UTC, date, datetime, timedelta

import httpx

from ..models import CheckpointPassage, Event, Runner
from .base import TrackingProvider

API_BASE = "https://api.owaka.live"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
RUNNERS_CACHE_TTL = timedelta(minutes=30)
STAGES_CACHE_TTL = timedelta(hours=6)
# La geografia pesa ~1 MB e non cambia durante la gara: cache lunga.
WAYPOINTS_CACHE_TTL = timedelta(hours=6)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class OwakaProvider(TrackingProvider):
    name = "owaka"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        # event_id -> (scaricati_alle, [Runner]); l'elenco iscritti cambia
        # raramente durante una gara, inutile riscaricarlo ad ogni ricerca.
        self._runners_cache: dict[str, tuple[datetime, list[Runner]]] = {}
        # event_id -> (scaricati_alle, [stage dict])
        self._stages_cache: dict[str, tuple[datetime, list[dict]]] = {}
        # stage_id -> (scaricati_alle, {waypoint_id: (nome, posizione)}, totale)
        self._waypoints_cache: dict[
            str, tuple[datetime, dict[str, tuple[str, int | None]], int]
        ] = {}

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()["data"]

    async def list_events(self) -> list[Event]:
        data = await self._get("/lives")
        events = [
            Event(
                provider=self.name,
                id=item["id"],
                name=item["name"],
                started_at=_parse_date(item.get("startedAt")),
                ended_at=_parse_date(item.get("endedAt")),
                timezone=item.get("timezone") or "UTC",
            )
            for item in data
        ]
        # Solo gare in corso o future (con un giorno di tolleranza sulla fine).
        cutoff = date.today() - timedelta(days=1)
        current = [e for e in events if e.ended_at is None or e.ended_at >= cutoff]
        current.sort(key=lambda e: e.started_at or date.max)
        return current

    async def _runners(self, event_id: str) -> list[Runner]:
        cached = self._runners_cache.get(event_id)
        if cached and datetime.now() - cached[0] < RUNNERS_CACHE_TTL:
            return cached[1]
        data = await self._get(f"/lives/{event_id}/vehicles")
        runners = [
            Runner(
                provider=self.name,
                event_id=event_id,
                id=item["id"],
                number=item.get("number") or "?",
                name=item.get("name") or "?",
                country=item.get("country"),
            )
            for item in data
        ]
        self._runners_cache[event_id] = (datetime.now(), runners)
        return runners

    async def search_runners(self, event_id: str, query: str) -> list[Runner]:
        query = query.strip().casefold()
        runners = await self._runners(event_id)
        return [
            r
            for r in runners
            if query in r.name.casefold() or query == r.number.casefold().lstrip("0")
            or query == r.number.casefold()
        ]

    async def _stages(self, event_id: str) -> list[dict]:
        cached = self._stages_cache.get(event_id)
        if cached and datetime.now() - cached[0] < STAGES_CACHE_TTL:
            return cached[1]
        data = await self._get(f"/lives/{event_id}/stages")
        self._stages_cache[event_id] = (datetime.now(), data)
        return data

    async def _waypoints(
        self, stage_id: str
    ) -> tuple[dict[str, tuple[str, int | None]], int]:
        cached = self._waypoints_cache.get(stage_id)
        if cached and datetime.now() - cached[0] < WAYPOINTS_CACHE_TTL:
            return cached[1], cached[2]
        data = await self._get(f"/stages/{stage_id}/geography")
        waypoints = data.get("liveStageWaypoints") or []
        mapping = {
            w["id"]: (w.get("name") or "checkpoint", w.get("position"))
            for w in waypoints
        }
        total = len(waypoints)
        self._waypoints_cache[stage_id] = (datetime.now(), mapping, total)
        return mapping, total

    async def get_updates(
        self, event_id: str, since: datetime
    ) -> list[CheckpointPassage]:
        # startedAt filtra i passaggi con validatedAt >= since (verificato
        # su dati reali); il filtro fine (strettamente maggiore, per
        # corridore) lo fa il chiamante.
        since_param = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        passages: list[CheckpointPassage] = []
        for stage in await self._stages(event_id):
            mapping, total = await self._waypoints(stage["id"])
            data = await self._get(
                f"/stages/{stage['id']}/latest_checkpoints",
                params={"startedAt": since_param},
            )
            for vehicle in data:
                for cp in vehicle["checkpoints"]:
                    name, position = mapping.get(
                        cp["liveStageWaypointId"], ("checkpoint", None)
                    )
                    pos = position + 1 if position is not None else None
                    passages.append(
                        CheckpointPassage(
                            provider=self.name,
                            event_id=event_id,
                            runner_id=vehicle["liveVehicleId"],
                            checkpoint_id=cp["liveStageWaypointId"],
                            checkpoint_name=name,
                            passed_at=datetime.fromisoformat(cp["validatedAt"]),
                            checkpoint_position=pos,
                            checkpoint_total=total or None,
                            kind=(
                                "finish" if pos and pos == total else "checkpoint"
                            ),
                        )
                    )

        # Ritiri e squalifiche (DNF, DSQ, ...) segnalati dall'organizzazione.
        statuses = await self._get(
            f"/lives/{event_id}/vehicle_statuses",
            params={"startedAt": since_param},
        )
        for status in statuses:
            if status.get("type") in ("DNF", "DSQ", "OUT"):
                passages.append(
                    CheckpointPassage(
                        provider=self.name,
                        event_id=event_id,
                        runner_id=status["liveVehicleId"],
                        checkpoint_id=status["id"],
                        checkpoint_name="ritiro",
                        passed_at=datetime.fromisoformat(status["startedAt"]),
                        kind="dnf",
                    )
                )

        passages.sort(key=lambda p: p.passed_at)
        return passages

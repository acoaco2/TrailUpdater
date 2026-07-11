"""Provider per la piattaforma rankings TORX (rankings.torxtrail.com).

Usata per Gran Trail Courmayeur e per le gare TORX (TOR330, TOR450, ...).
I dati sono file JSON statici pubblicati sotto
https://live.torxtrail.com/rankings/json/:

- elenco_gare.json           elenco gare con id numerico e "codice"
- iscritti_{id}.json         iscritti (pettorale, nome, cognome, paese)
- avanzati_{id}.json         "postazioni": checkpoint con nome, rank, distanza
- {id}.json                  classifica live: per corridore (bib) l'array
                             "crono" dei passaggi con timestamp e status

Codici status (dal front-end): 0=FINISHED, 194=IN_RACE, 195=DNF,
196=COM, 197=WRONG_TIMEKEEPING, 198=DSQ, 200=DNS.
"""

import math
from datetime import UTC, date, datetime, timedelta

from curl_cffi.requests import AsyncSession

from ..models import CheckpointPassage, Event, Runner
from .base import TrackingProvider

JSON_BASE = "https://live.torxtrail.com/rankings/json"
RUNNERS_CACHE_TTL = timedelta(minutes=30)
STATIONS_CACHE_TTL = timedelta(hours=6)
# Le gare TORX sono in Valle d'Aosta; l'elenco non espone un fuso orario.
DEFAULT_TIMEZONE = "Europe/Rome"

STATUS_DNF = 195
STATUS_DSQ = 198


class TorxProvider(TrackingProvider):
    name = "torx"

    def __init__(self) -> None:
        # Il WAF del sito fa fingerprinting TLS e rifiuta i client Python
        # standard (httpx/requests): curl_cffi impersona il TLS di Chrome.
        self._client = AsyncSession(impersonate="chrome", timeout=30)
        self._runners_cache: dict[str, tuple[datetime, list[Runner]]] = {}
        # event_id -> (scaricati_alle, {rank: nome}, rank del traguardo)
        self._stations_cache: dict[
            str, tuple[datetime, dict[int, str], int | None]
        ] = {}

    async def _get_json(self, path: str) -> dict | list:
        resp = await self._client.get(JSON_BASE + path)
        resp.raise_for_status()
        return resp.json()

    async def list_events(self) -> list[Event]:
        data = await self._get_json("/elenco_gare.json")
        today = date.today()
        events = []
        for race in data["gare"]:
            if race.get("hide_frontend") or not race.get("event_date"):
                continue
            if not race.get("id"):
                continue
            start = datetime.fromisoformat(race["event_date"]).date()
            # Fine stimata: partenza + tempo limite (ore), default 48h.
            limit_hours = race.get("time_limit") or 48
            end = start + timedelta(days=math.ceil(limit_hours / 24))
            # Solo gare in corso o imminenti (entro 30 giorni).
            if end < today - timedelta(days=1) or start > today + timedelta(days=30):
                continue
            events.append(
                Event(
                    provider=self.name,
                    id=str(race["id"]),
                    name=race["nome_gara"],
                    started_at=start,
                    ended_at=end,
                    timezone=DEFAULT_TIMEZONE,
                )
            )
        events.sort(key=lambda e: e.started_at or date.max)
        return events

    async def _runners(self, event_id: str) -> list[Runner]:
        cached = self._runners_cache.get(event_id)
        if cached and datetime.now() - cached[0] < RUNNERS_CACHE_TTL:
            return cached[1]
        data = await self._get_json(f"/iscritti_{event_id}.json")
        runners = []
        for item in data.get("iscritti", []):
            bib = str(item.get("pettorale") or "").strip()
            if not bib:
                continue
            full_name = f"{item.get('nome', '')} {item.get('cognome', '')}"
            runners.append(
                Runner(
                    provider=self.name,
                    event_id=event_id,
                    # La classifica identifica i corridori solo per
                    # pettorale, quindi il pettorale è anche l'id.
                    id=bib,
                    number=bib,
                    name=full_name.strip().title(),
                    country=(item.get("nazionalita") or "").upper() or None,
                )
            )
        self._runners_cache[event_id] = (datetime.now(), runners)
        return runners

    async def search_runners(self, event_id: str, query: str) -> list[Runner]:
        query = query.strip().casefold()
        runners = await self._runners(event_id)
        return [
            r
            for r in runners
            if query in r.name.casefold() or query == r.number.casefold()
        ]

    async def _stations(
        self, event_id: str
    ) -> tuple[dict[int, str], int | None]:
        cached = self._stations_cache.get(event_id)
        if cached and datetime.now() - cached[0] < STATIONS_CACHE_TTL:
            return cached[1], cached[2]
        data = await self._get_json(f"/avanzati_{event_id}.json")
        visible = [
            p
            for p in data.get("postazioni", [])
            if not p.get("postazione_nascosta")
        ]
        mapping = {p["rank"]: p["text"] for p in visible}
        finish_rank = max(mapping) if mapping else None
        self._stations_cache[event_id] = (datetime.now(), mapping, finish_rank)
        return mapping, finish_rank

    async def get_updates(
        self, event_id: str, since: datetime
    ) -> list[CheckpointPassage]:
        stations, finish_rank = await self._stations(event_id)
        data = await self._get_json(f"/{event_id}.json")
        results = data[0].get("result", []) if data else []

        passages: list[CheckpointPassage] = []
        for runner in results:
            bib = str(runner.get("bib") or "").strip()
            if not bib:
                continue
            for cp in runner.get("crono") or []:
                if not cp.get("tempo"):
                    continue
                passed_at = datetime.fromisoformat(cp["tempo"]).astimezone(UTC)
                if passed_at <= since:
                    continue
                rank = cp.get("postazione")
                if cp.get("type") in (STATUS_DNF, STATUS_DSQ):
                    kind = "dnf"
                elif rank is not None and rank == finish_rank:
                    kind = "finish"
                else:
                    kind = "checkpoint"
                passages.append(
                    CheckpointPassage(
                        provider=self.name,
                        event_id=event_id,
                        runner_id=bib,
                        checkpoint_id=str(cp.get("id") or rank),
                        checkpoint_name=(
                            "ritiro"
                            if kind == "dnf"
                            else stations.get(rank, "checkpoint")
                        ),
                        passed_at=passed_at,
                        checkpoint_position=rank,
                        checkpoint_total=finish_rank,
                        kind=kind,
                    )
                )
        passages.sort(key=lambda p: p.passed_at)
        return passages

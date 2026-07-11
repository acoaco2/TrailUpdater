"""Interfaccia astratta di un provider di tracking.

Il bot parla solo con questa interfaccia e con i modelli in models.py:
nessun dettaglio della piattaforma (Owaka, LiveTrail, ...) deve uscire
dal rispettivo modulo provider.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from ..models import CheckpointPassage, Event, Runner


class TrackingProvider(ABC):
    name: str  # identificativo breve, es. "owaka"

    @abstractmethod
    async def list_events(self) -> list[Event]:
        """Eventi live disponibili (in corso o imminenti)."""

    @abstractmethod
    async def search_runners(self, event_id: str, query: str) -> list[Runner]:
        """Cerca corridori per nome o pettorale in un evento."""

    @abstractmethod
    async def get_updates(
        self, event_id: str, since: datetime
    ) -> list[CheckpointPassage]:
        """Passaggi ai checkpoint registrati dopo `since` (UTC)."""

    async def get_last_passage(
        self, event_id: str, runner_id: str
    ) -> CheckpointPassage | None:
        """Ultimo passaggio noto di un corridore (per /stato)."""
        passages = await self.get_updates(
            event_id, datetime.now(UTC) - timedelta(days=10)
        )
        mine = [p for p in passages if p.runner_id == runner_id]
        return mine[-1] if mine else None

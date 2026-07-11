from .base import TrackingProvider
from .owaka import OwakaProvider

# Registro dei provider disponibili. Per supportare una nuova piattaforma
# basta implementare TrackingProvider e aggiungerla qui.
PROVIDERS: dict[str, TrackingProvider] = {
    "owaka": OwakaProvider(),
}


def get_provider(name: str) -> TrackingProvider:
    return PROVIDERS[name]

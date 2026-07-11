# Architettura

## Scoperta chiave: la fonte dati

`live.torxtrail.com` è solo un sito vetrina WordPress. Il tracking vero dei
corridori TORX è sulla piattaforma **Owaka** (`owaka.live`), che espone
un'**API JSON pubblica senza autenticazione** su `https://api.owaka.live`.

Endpoint verificati (luglio 2026, con l'evento TOR330 2025 come test):

| Endpoint | Cosa restituisce |
|---|---|
| `GET /lives` | Elenco degli eventi live (id, slug, nome, date, timezone) |
| `GET /lives/{id o slug}` | Dettaglio evento |
| `GET /lives/{id}/vehicles` | I partecipanti ("vehicle" = corridore): id, pettorale (`number`), nome, paese, categoria |
| `GET /lives/{id}/categories` | Categorie dell'evento |
| `GET /lives/{id}/stages` | Tappe (per un trail tipicamente una sola, con date) |
| `GET /stages/{stageId}/latest_checkpoints?startedAt=ISO8601` | Passaggi ai checkpoint per corridore; `startedAt` permette il **polling incrementale** (solo i passaggi nuovi) |
| `GET /stages/{stageId}/geography` | Percorso + `liveStageWaypoints`: id, **nome del checkpoint**, posizione, ruolo |
| `GET /lives/{id}/latest_locations` | Ultime posizioni GPS dei tracker |

Flusso dati per una notifica:

1. `latest_checkpoints` (filtrato con `startedAt` = ultimo poll) → nuovi passaggi
   `{liveVehicleId, liveStageWaypointId, validatedAt}`.
2. `liveVehicleId` → nome/pettorale del corridore (da `vehicles`, cache).
3. `liveStageWaypointId` → nome del checkpoint (da `geography`, cache).
4. Messaggio Telegram: "🏃 Mario Rossi (#123) è passato a *Rifugio Bertone* alle 14:32".

Nota: l'API usa un User-Agent check blando (serve un UA da browser) ma nessun
token. Polling educato: ogni 3–5 minuti è più che sufficiente per un trail.

## Secondo provider: rankings TORX (`torx`)

Il Gran Trail Courmayeur e le gare TORX "minori" non usano Owaka ma la
piattaforma di rankings propria (`rankings.torxtrail.com`, iframe dentro
live.torxtrail.com). I dati sono JSON statici pubblici sotto
`https://live.torxtrail.com/rankings/json/`:

| File | Cosa contiene |
|---|---|
| `elenco_gare.json` | Tutte le gare, con `id` numerico, `codice`, data, tempo limite |
| `iscritti_{id}.json` | Iscritti: `pettorale`, nome, cognome, nazionalità |
| `avanzati_{id}.json` | `postazioni`: checkpoint con nome, `rank`, distanza, GPS |
| `{id}.json` | Classifica live: per pettorale l'array `crono` dei passaggi |

Codici status del cronometraggio (dal front-end): 0=FINISHED, 194=IN_RACE,
195=DNF, 196=COM, 197=WRONG_TIMEKEEPING, 198=DSQ, 200=DNS.

Attenzione: il WAF del sito fa **fingerprinting TLS** e rifiuta i client
HTTP Python standard (httpx/requests → 403 anche con header da browser).
Il provider usa `curl_cffi` con `impersonate="chrome"`.

Nota sull'arrivo: su Owaka il waypoint del traguardo spesso non viene
validato via GPS (cronometraggio manuale), quindi la notifica "finish"
può non scattare; sulla piattaforma rankings TORX la postazione FINISH
è cronometrata e l'arrivo è affidabile.

## Struttura del progetto (prevista)

```
trailupdater/
├── providers/
│   ├── base.py        # Interfaccia astratta del provider
│   └── owaka.py       # Implementazione Owaka
├── bot/
│   └── handlers.py    # Comandi Telegram (/gara, /cerca, /segui, ...)
├── tracker.py         # Loop di polling: nuovi passaggi → notifiche
├── storage.py         # Persistenza: iscrizioni utente, ultimo stato visto
├── models.py          # Modelli normalizzati: Event, Runner, CheckpointPassage
└── main.py            # Avvio bot + scheduler
```

## Astrazione del provider (portabilità tra gare/siti)

Il bot non conosce Owaka. Parla solo con questa interfaccia e con i modelli
normalizzati; per un sito diverso (LiveTrail, Wedosport, ...) si aggiunge un
file in `providers/` e nient'altro:

```python
class TrackingProvider(ABC):
    def list_events(self) -> list[Event]: ...
    def search_runners(self, event_id: str, query: str) -> list[Runner]: ...
    def get_updates(self, event_id: str, since: datetime) -> list[CheckpointPassage]: ...
```

Modelli normalizzati (indipendenti dalla piattaforma):

- `Event`: id, nome, date, timezone, provider
- `Runner`: id, pettorale, nome, paese, categoria
- `CheckpointPassage`: runner_id, nome checkpoint, orario, (distanza km opz.)

## Bot Telegram — UX prevista

- `/start` — benvenuto e istruzioni
- `/gara` — elenco gare live (bottoni inline), selezione
- `/cerca <nome o pettorale>` — ricerca corridore nella gara selezionata
- selezione con bottone → il corridore viene "seguito"
- `/seguiti` — elenco corridori seguiti, con bottone per smettere di seguire
- notifiche push automatiche ad ogni nuovo passaggio

## Persistenza

SQLite (o JSON all'inizio): per ogni chat Telegram, l'elenco di
(provider, event_id, runner_id) seguiti + timestamp dell'ultimo passaggio
notificato, per non duplicare messaggi dopo un riavvio.

## Stack

Python 3.12 (già installato), `python-telegram-bot` per il bot,
`httpx`/`requests` per l'API. Nessun webhook: long polling Telegram,
così gira anche su un PC di casa senza IP pubblico.

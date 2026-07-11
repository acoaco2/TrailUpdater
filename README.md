# TrailUpdater

Bot Telegram che invia aggiornamenti live su un corridore di una gara trail:
cerchi il corridore per nome o pettorale, lo selezioni, e ricevi un messaggio
ogni volta che passa un checkpoint.

Prima gara supportata: **TORX** (Tor des Géants ecc.) tramite la piattaforma
di tracking [Owaka](https://owaka.live), che è la fonte dati dietro
[live.torxtrail.com](https://live.torxtrail.com/).

## Obiettivi

- Cercare e selezionare un corridore direttamente dal bot Telegram.
- Ricevere una notifica quando il corridore passa un checkpoint
  (nome del punto, orario, eventualmente distanza percorsa).
- **Indipendenza dalla piattaforma**: la logica del bot è separata dal
  "provider" dei dati. Per una gara futura su un altro sito basta scrivere
  un nuovo provider, senza toccare bot e notifiche.

## Stato del progetto

- [x] Step 1 — Analisi della fonte dati e architettura (vedi [ARCHITECTURE.md](ARCHITECTURE.md))
- [ ] Step 2 — Creazione del bot Telegram (BotFather) e scheletro del progetto
- [ ] Step 3 — Provider Owaka: elenco gare, ricerca corridori
- [ ] Step 4 — Polling dei checkpoint e invio notifiche
- [ ] Step 5 — Persistenza (iscrizioni, stato) e rifiniture

## Setup (in arrivo)

Il progetto sarà in Python. Servirà un token del bot Telegram (da BotFather),
configurato via variabile d'ambiente `TELEGRAM_BOT_TOKEN` (mai committato).

# TrailUpdater

Bot Telegram che invia aggiornamenti live su un corridore di una gara trail:
cerchi il corridore per nome o pettorale, lo selezioni, e ricevi un messaggio
ogni volta che passa un checkpoint.

Prima gara supportata: **TORX** (Tor des Géants ecc.) tramite la piattaforma
di tracking [Owaka](https://owaka.live), che è la fonte dati dietro
[live.torxtrail.com](https://live.torxtrail.com/).

## ▶️ Avviare il bot (Windows)

Da PowerShell, nella cartella del progetto:

```powershell
cd C:\Users\acoac\acodev\TrailUpdater
.\.venv\Scripts\python main.py
```

- Il bot è attivo quando compare `Application started`.
- Resta attivo solo finché la finestra è aperta e il PC è acceso (non in sospensione).
- **Per fermarlo usa Ctrl+C**: così gli utenti ricevono l'avviso "Bot spento".
  Chiudendo la finestra con la X l'avviso non parte.
- Tieni avviata **una sola istanza** alla volta: con due istanze sullo stesso
  token Telegram rifiuta la seconda (errore `Conflict`).
- L'avviso `PTBUserWarning: Could not add signal handlers...` è normale su
  Windows e si può ignorare.

## Obiettivi

- Cercare e selezionare un corridore direttamente dal bot Telegram.
- Ricevere una notifica quando il corridore passa un checkpoint
  (nome del punto, orario, eventualmente distanza percorsa).
- **Indipendenza dalla piattaforma**: la logica del bot è separata dal
  "provider" dei dati. Per una gara futura su un altro sito basta scrivere
  un nuovo provider, senza toccare bot e notifiche.

## Stato del progetto

- [x] Step 1 — Analisi della fonte dati e architettura (vedi [ARCHITECTURE.md](ARCHITECTURE.md))
- [x] Step 2 — Bot Telegram (@aco_trailupdater_bot) e scheletro: /start, /gara con selezione
- [x] Step 3 — /cerca con selezione a bottoni, /seguiti con unfollow, stato persistente su disco
- [x] Step 4 — Polling dei checkpoint (ogni 3 min) e notifiche Telegram
- [x] Step 5 — Notifiche arrivo/ritiro, /stato, pulizia gare finite, secondo provider (rankings TORX / Gran Trail Courmayeur)
- [ ] Step 6 — Avvio automatico all'accensione del PC (opzionale)

## Setup (solo la prima volta)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
# crea un file .env con: TELEGRAM_BOT_TOKEN=<token da BotFather>
.\.venv\Scripts\python main.py
```

Il token non va mai committato: `.env` è escluso da git.

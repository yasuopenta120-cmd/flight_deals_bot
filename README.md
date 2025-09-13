# Flight Deals Bot ✈️

Ένα bot που ψάχνει αυτόματα φθηνά αεροπορικά εισιτήρια (μέσω Amadeus API) 
και στέλνει ειδοποιήσεις στο Telegram.

## Features
- Έλεγχος τιμών κάθε ώρα (ρυθμιζόμενο στο .env).
- Ειδοποίηση στο Telegram με **τιμή + ώρες πτήσης + deep links** (Google Flights & Skyscanner).
- Alert αν η τιμή ανά άτομο ≤ ALERT_PER_PERSON.
- Καθημερινή αναφορά στις 22:00 με τη χαμηλότερη τιμή της ημέρας.
- Ιστορικό τιμών (SQLite).
- Telegram commands:
  - `/start` → άμεση αναζήτηση
  - `/history` → Top 10 καλύτερες τιμές
  - `/help` → λίστα εντολών
- Φίλτρα ωρών:
  - Outbound από Αθήνα: DEP_WINDOW_FROM..DEP_WINDOW_TO
  - Inbound από Βαρκελώνη: RET_WINDOW_FROM..RET_WINDOW_TO

## Setup (Railway)

1. Κάνε fork ή clone το repo στο GitHub.
2. Συνδέσου στο [Railway](https://railway.app), επίλεξε **New Project → Deploy from GitHub Repo**.
3. Στο Railway, στο tab **Variables**, βάλε όλα τα πεδία που έχει το `.env.example`.
4. Στο Railway, Start Command ή Procfile:
5. worker: python flight_deals_bot.py
6. Κάνε Deploy. Δες τα Logs για επιβεβαίωση.
7. Στείλε στο Telegram `/start` για δοκιμή.

## Notes
- Χρησιμοποιεί Amadeus **Test API** (`https://test.api.amadeus.com`).
- Για Production χρειάζεται να αλλάξεις endpoint και να χρησιμοποιήσεις production API keys.
- Μην ανεβάσεις το `.env` στο GitHub.

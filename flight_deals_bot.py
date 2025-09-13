#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import sqlite3
from datetime import datetime
import threading

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# --------------------------------------------------------------------
#  Load .env and config
# --------------------------------------------------------------------
load_dotenv()

TELEGRAM_TOKEN        = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "").strip()
AMADEUS_CLIENT_ID     = os.getenv("AMADEUS_CLIENT_ID", "").strip()
AMADEUS_CLIENT_SECRET = os.getenv("AMADEUS_CLIENT_SECRET", "").strip()

ORIGIN         = os.getenv("ORIGIN", "ATH").upper()
DESTINATION    = os.getenv("DESTINATION", "BCN").upper()
DEPARTURE_DATE = os.getenv("DEPARTURE_DATE", "2026-04-28")
RETURN_DATE    = os.getenv("RETURN_DATE", "2026-05-05")
ADULTS         = int(os.getenv("ADULTS", "2"))
CURRENCY       = os.getenv("CURRENCY", "EUR")

ALERT_PER_PERSON     = float(os.getenv("ALERT_PER_PERSON", "200.0"))
POLL_EVERY_MINUTES   = int(os.getenv("POLL_EVERY_MINUTES", "60"))
TIMEZONE             = os.getenv("TIMEZONE", "Europe/Athens")
DAILY_SUMMARY_HOUR   = int(os.getenv("DAILY_SUMMARY_HOUR", "22"))
DAILY_SUMMARY_MINUTE = int(os.getenv("DAILY_SUMMARY_MINUTE", "0"))

DB_PATH = os.getenv("DB_PATH", "flights_history.db")

# --- Time windows (ENV -> int or None) ---
def _to_int_or_none(x):
    try:
        if x is None: return None
        s = str(x).strip()
        return int(s) if s != "" and s.lower() != "none" else None
    except:
        return None

DEP_WINDOW_FROM = _to_int_or_none(os.getenv("DEP_WINDOW_FROM"))
DEP_WINDOW_TO   = _to_int_or_none(os.getenv("DEP_WINDOW_TO"))
RET_WINDOW_FROM = _to_int_or_none(os.getenv("RET_WINDOW_FROM"))
RET_WINDOW_TO   = _to_int_or_none(os.getenv("RET_WINDOW_TO"))

# Amadeus test endpoints
AMADEUS_TOKEN_URL  = "https://test.api.amadeus.com/v1/security/oauth2/token"
AMADEUS_SEARCH_URL = "https://test.api.amadeus.com/v2/shopping/flight-offers"

# Telegram endpoints
TELEGRAM_SEND_URL       = lambda token: f"https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_GETUPDATES_URL = lambda token, offset=None: f"https://api.telegram.org/bot{token}/getUpdates" + (f"?offset={offset}" if offset else "")

TZ = pytz.timezone(TIMEZONE)

# --------------------------------------------------------------------
#  SQLite helpers
# --------------------------------------------------------------------
def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            dep_date TEXT,
            ret_date TEXT,
            google_link TEXT,
            skyscanner_link TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_history(price, currency, dep_date, ret_date, g_link, s_link):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO history (timestamp, price, currency, dep_date, ret_date, google_link, skyscanner_link) VALUES (?,?,?,?,?,?,?)",
        (datetime.now(TZ).isoformat(), price, currency, dep_date, ret_date, g_link, s_link)
    )
    conn.commit()
    conn.close()

def top_n_history(n=10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT price, currency, dep_date, ret_date, google_link, skyscanner_link, timestamp
        FROM history
        ORDER BY price ASC
        LIMIT ?
    """, (n,))
    rows = c.fetchall()
    conn.close()
    return rows

def best_price_today():
    today = datetime.now(TZ).date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT price, currency, dep_date, ret_date, google_link, skyscanner_link
        FROM history
        WHERE date(timestamp)=?
        ORDER BY price ASC
        LIMIT 1
    """, (today,))
    row = c.fetchone()
    conn.close()
    return row

# --------------------------------------------------------------------
#  Telegram helpers
# --------------------------------------------------------------------
def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID.")
        return False
    try:
        url = TELEGRAM_SEND_URL(TELEGRAM_TOKEN)
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": False}
        r = requests.post(url, json=payload, timeout=25)
        if r.status_code != 200:
            print("Telegram send error:", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print("Telegram exception:", e)
        return False

# --------------------------------------------------------------------
#  Amadeus helpers
# --------------------------------------------------------------------
def get_amadeus_token():
    if not AMADEUS_CLIENT_ID or not AMADEUS_CLIENT_SECRET:
        print("Missing Amadeus credentials.")
        return None
    try:
        data = {
            "grant_type": "client_credentials",
            "client_id": AMADEUS_CLIENT_ID,
            "client_secret": AMADEUS_CLIENT_SECRET
        }
        r = requests.post(AMADEUS_TOKEN_URL, data=data, timeout=30)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        print("Amadeus token error:", e)
        return None

def search_amadeus(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "originLocationCode": ORIGIN,
        "destinationLocationCode": DESTINATION,
        "departureDate": DEPARTURE_DATE,
        "returnDate": RETURN_DATE,
        "adults": ADULTS,
        "currencyCode": CURRENCY,
        "max": 50
    }
    try:
        r = requests.get(AMADEUS_SEARCH_URL, headers=headers, params=params, timeout=40)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Amadeus search error:", e)
        return None

# --------------------------------------------------------------------
#  Extract times and filter with time windows
# --------------------------------------------------------------------
def _parse_hour(iso_ts):
    """Return hour (0-23) from an ISO datetime like '2026-04-28T08:30:00'."""
    if not iso_ts: return None
    try:
        return datetime.fromisoformat(iso_ts.replace("Z","")).hour
    except:
        return None

def extract_dates_from_offer(offer):
    dep = None; ret = None
    try:
        its = offer.get("itineraries", [])
        if len(its) >= 1 and its[0].get("segments"):
            dep = its[0]["segments"][0]["departure"]["at"][:10]
        if len(its) >= 2 and its[1].get("segments"):
            ret = its[1]["segments"][0]["departure"]["at"][:10]
    except Exception:
        pass
    return dep, ret

def extract_times_from_offer(offer):
    out_dep = out_arr = in_dep = in_arr = None
    try:
        its = offer.get("itineraries", [])
        if len(its) >= 1 and its[0].get("segments"):
            s0 = its[0]["segments"][0]
            out_dep = s0["departure"]["at"]
            out_arr = s0["arrival"]["at"]
        if len(its) >= 2 and its[1].get("segments"):
            s1 = its[1]["segments"][0]
            in_dep = s1["departure"]["at"]
            in_arr = s1["arrival"]["at"]
    except:
        pass
    return out_dep, out_arr, in_dep, in_arr

def offer_matches_time_windows(offer):
    """
    Επιστρέφει True αν το offer περνάει τα παράθυρα ωρών.
    - Outbound (ATH->BCN): DEP_WINDOW_FROM..DEP_WINDOW_TO
    - Inbound (BCN->ATH):  RET_WINDOW_FROM..RET_WINDOW_TO
    """
    out_dep_iso, _, in_dep_iso, _ = extract_times_from_offer(offer)
    out_hr = _parse_hour(out_dep_iso)
    in_hr  = _parse_hour(in_dep_iso)

    if DEP_WINDOW_FROM is not None and DEP_WINDOW_TO is not None and out_hr is not None:
        if not (DEP_WINDOW_FROM <= out_hr <= DEP_WINDOW_TO):
            return False

    if RET_WINDOW_FROM is not None and RET_WINDOW_TO is not None and in_hr is not None:
        if not (RET_WINDOW_FROM <= in_hr <= RET_WINDOW_TO):
            return False

    return True

# --------------------------------------------------------------------
#  Choose best offer (with time window filter)
# --------------------------------------------------------------------
def best_offer_from_search(data):
    offers = data.get("data", []) if data else []
    best = None
    for o in offers:
        # NEW: filter by time windows
        if not offer_matches_time_windows(o):
            continue
        # pick by lowest price
        try:
            price = float(o.get("price", {}).get("grandTotal") or o.get("price", {}).get("total"))
        except Exception:
            continue
        if best is None or price < best[0]:
            best = (price, o)
    return best

# --------------------------------------------------------------------
#  Deep links
# --------------------------------------------------------------------
def google_flights_link(origin, dest, dep_date, ret_date=None, currency="EUR", adults=1, locale="el"):
    if not dep_date:
        return None
    base = f"https://www.google.com/flights?hl={locale}#flt={origin}.{dest}.{dep_date}"
    if ret_date:
        base += f"*{dest}.{origin}.{ret_date}"
    base += f";c:{currency};sd:1;adults={adults}"
    return base

def skyscanner_link(origin, dest, dep_date, ret_date=None, currency="EUR", adults=1):
    if not dep_date:
        return None
    origin_l = origin.lower(); dest_l = dest.lower()
    dep_fmt = dep_date.replace("-", "")[2:]
    if ret_date:
        ret_fmt = ret_date.replace("-", "")[2:]
        return f"https://www.skyscanner.net/transport/flights/{origin_l}/{dest_l}/{dep_fmt}/{ret_fmt}/?adults={adults}&currency={currency}"
    else:
        return f"https://www.skyscanner.net/transport/flights/{origin_l}/{dest_l}/{dep_fmt}/?adults={adults}&currency={currency}"

# --------------------------------------------------------------------
#  Poll and notify
# --------------------------------------------------------------------
def poll_and_notify():
    token = get_amadeus_token()
    if not token:
        return
    data = search_amadeus(token)
    best = best_offer_from_search(data)
    if not best:
        return

    price, offer = best
    dep_date, ret_date = extract_dates_from_offer(offer)
    out_dep_iso, out_arr_iso, in_dep_iso, in_arr_iso = extract_times_from_offer(offer)

    g_link = google_flights_link(ORIGIN, DESTINATION, dep_date, ret_date, CURRENCY, ADULTS)
    s_link = skyscanner_link(ORIGIN, DESTINATION, dep_date, ret_date, CURRENCY, ADULTS)

    add_history(price, CURRENCY, dep_date, ret_date, g_link, s_link)

    def fmt(dt):
        try:
            return datetime.fromisoformat(dt.replace("Z","")).strftime("%Y-%m-%d %H:%M")
        except:
            return dt

    lines = [f"✈️ Found price: {price:.2f} {CURRENCY} for {ADULTS} pax"]
    if out_dep_iso and out_arr_iso:
        lines.append(f"📅 Outbound: {fmt(out_dep_iso)} → {fmt(out_arr_iso)}")
    elif dep_date:
        lines.append(f"📅 {dep_date}")

    if in_dep_iso and in_arr_iso:
        lines.append(f"📅 Return:   {fmt(in_dep_iso)} → {fmt(in_arr_iso)}")

    if g_link:
        lines.append(f"🔗 Google Flights: {g_link}")
    if s_link:
        lines.append(f"🔗 Skyscanner: {s_link}")

    send_telegram_message("\n".join(lines))

    per_person = price / ADULTS if ADULTS else price
    if per_person <= ALERT_PER_PERSON:
        send_telegram_message(f"🔥 [ALERT] Price ≤ {ALERT_PER_PERSON:.0f}€/person! ({per_person:.2f}€/person)")

# --------------------------------------------------------------------
#  Daily summary
# --------------------------------------------------------------------
def daily_summary_job():
    row = best_price_today()
    if row:
        price, currency, dep_date, ret_date, g_link, s_link = row
        lines = [f"📉 Daily lowest price: {price:.2f} {currency}"]
        if dep_date and ret_date:
            lines.append(f"📅 {dep_date} → {ret_date}")
        if g_link:
            lines.append(f"🔗 Google Flights: {g_link}")
        if s_link:
            lines.append(f"🔗 Skyscanner: {s_link}")
        send_telegram_message("\n".join(lines))
    else:
        send_telegram_message("ℹ️ No prices recorded today.")

# --------------------------------------------------------------------
#  Telegram commands (/start, /history, /help)
# --------------------------------------------------------------------
UPDATE_OFFSET_FILE = "tg_update_offset.txt"

def load_offset():
    if os.path.exists(UPDATE_OFFSET_FILE):
        try:
            return int(open(UPDATE_OFFSET_FILE).read().strip())
        except Exception:
            return None
    return None

def save_offset(offset):
    with open(UPDATE_OFFSET_FILE, "w") as f:
        f.write(str(offset))

def handle_update(update):
    try:
        msg = update.get("message") or update.get("edited_message") or {}
        text = msg.get("text", "").strip()
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id"))
    except Exception:
        return

    # Safety: only your chat id
    if str(TELEGRAM_CHAT_ID) and chat_id != str(TELEGRAM_CHAT_ID):
        return

    if text.startswith("/start"):
        send_telegram_message("🔎 Running immediate search...")
        poll_and_notify()
    elif text.startswith("/history"):
        rows = top_n_history(10)
        if not rows:
            send_telegram_message("No history yet.")
            return
        parts = ["📊 Top 10 Lowest Prices:"]
        for i, r in enumerate(rows, start=1):
            price, currency, dep, ret, g, s, ts = r
            parts.append(f"{i}) €{price:.2f} — {dep or '?'} → {ret or '?'}")
            if g: parts.append(f"🔗 G: {g}")
            if s: parts.append(f"🔗 S: {s}")
        send_telegram_message("\n".join(parts))
    elif text.startswith("/help"):
        send_telegram_message(
            "Commands:\n"
            "/start  - run immediate search\n"
            "/history- top 10 lowest prices\n"
            "/help   - this message"
        )
    else:
        send_telegram_message("Send /start, /history or /help")

def tg_updates_loop():
    offset = load_offset()
    while True:
        try:
            url = TELEGRAM_GETUPDATES_URL(TELEGRAM_TOKEN, offset)
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                j = r.json()
                for u in j.get("result", []):
                    handle_update(u)
                    offset = u["update_id"] + 1
                    save_offset(offset)
            time.sleep(2)
        except Exception as e:
            print("tg loop error:", e)
            time.sleep(5)

# --------------------------------------------------------------------
#  Main
# --------------------------------------------------------------------
def main():
    ensure_db()

    # Schedulers
    sched = BackgroundScheduler(timezone=TZ)
    sched.add_job(poll_and_notify, 'interval', minutes=POLL_EVERY_MINUTES)
    sched.add_job(daily_summary_job, 'cron', hour=DAILY_SUMMARY_HOUR, minute=DAILY_SUMMARY_MINUTE)
    sched.start()

    # Telegram commands listener
    t = threading.Thread(target=tg_updates_loop, daemon=True)
    t.start()

    # Initial run
    poll_and_notify()

    # Keep alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()

if __name__ == "__main__":
    main()

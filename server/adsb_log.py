#!/usr/bin/env python3
"""Zapis samolotów ADS-B/MLAT wokół czujników, do kalibracji zasięgu słyszenia.

Co POLL_S sekund pyta darmowe API (adsb.lol, zapasowo adsb.fi) o samoloty w promieniu
RADIUS_NM od środka punktów i dopisuje te w zasięgu MAX_KM do data/adsb/YYYY-MM-DD.jsonl.
Dni starsze niż wczoraj są pakowane gzipem, starsze niż KEEP_DAYS kasowane.
Uruchomienie: setsid nohup python3 adsb_log.py >> adsb.log 2>&1 < /dev/null &
"""
import gzip, json, math, os, shutil, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

CENTER = tuple(float(x) for x in os.environ.get("SKYWATCH_ADSB_CENTER", "52.2297,21.0122").split(","))   # środek obszaru punktów
RADIUS_NM = 10
MAX_KM = 12                   # dalej i tak nie usłyszymy
POLL_S = 10
KEEP_DAYS = 45
SOURCES = [
    f"https://api.adsb.lol/v2/point/{CENTER[0]}/{CENTER[1]}/{RADIUS_NM}",
    f"https://opendata.adsb.fi/api/v2/lat/{CENTER[0]}/lon/{CENTER[1]}/dist/{RADIUS_NM}",
]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "adsb")
FIELDS = ("hex", "flight", "t", "r", "category", "type", "alt_baro", "alt_geom", "gs", "track", "baro_rate", "lat", "lon", "seen_pos")


def km(lat, lon):
    dy = (lat - CENTER[0]) * 111.32
    dx = (lon - CENTER[1]) * 111.32 * math.cos(math.radians(CENTER[0]))
    return math.hypot(dx, dy)


backoff = {}   # źródło → do kiedy go nie pytamy (po 429 / błędzie)


def fetch():
    for i, u in enumerate(SOURCES):
        if time.time() < backoff.get(i, 0) and i < len(SOURCES) - 1: continue
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "skywatchpl-adsb-log/1"})
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.load(r)
            return i, d.get("ac") or d.get("aircraft") or []
        except Exception as e:
            backoff[i] = time.time() + 600
            print(f"{datetime.now(timezone.utc):%H:%M:%S} źródło {i}: {e} (przerwa 10 min)", flush=True)
    return None, None


def housekeep():
    today = datetime.now(timezone.utc).date()
    for f in os.listdir(OUT):
        try: day = datetime.strptime(f[:10], "%Y-%m-%d").date()
        except ValueError: continue
        p = os.path.join(OUT, f)
        if (today - day).days > KEEP_DAYS: os.remove(p)
        elif f.endswith(".jsonl") and (today - day).days >= 2:
            with open(p, "rb") as a, gzip.open(p + ".gz", "wb") as b: shutil.copyfileobj(a, b)
            os.remove(p)


def main():
    os.makedirs(OUT, exist_ok=True)
    last_hk = 0
    while True:
        t0 = time.time()
        src, ac = fetch()
        if ac is not None:
            rows = []
            for a in ac:
                if not isinstance(a.get("lat"), (int, float)) or a.get("alt_baro") == "ground": continue
                d = km(a["lat"], a["lon"])
                if d > MAX_KM: continue
                r = {k: a[k] for k in FIELDS if a.get(k) is not None}
                if isinstance(r.get("flight"), str): r["flight"] = r["flight"].strip()
                r.update(ts=round(t0 - (a.get("seen_pos") or 0), 1), km=round(d, 2), src=src)
                rows.append(r)
            if rows:
                with open(os.path.join(OUT, f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"), "a") as f:
                    for r in rows: f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        if t0 - last_hk > 3600:
            housekeep(); last_hk = t0
        time.sleep(max(1, POLL_S - (time.time() - t0)))


if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(0)

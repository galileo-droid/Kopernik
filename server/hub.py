#!/usr/bin/env python3
"""
SkywatchPL — serwer grupy (MVP 0.1).

Odbiera od aplikacji na telefonach: wykrycia, puls „żyję”, zgłoszenia ręczne i nagrania.
Rozsyła alarmy (telefony odpytują /api/alerts co kilka sekund).
Tylko biblioteka standardowa Pythona.

Uruchomienie:
  SKYWATCH_TOKEN=tajny python3 hub.py --port 8765 [--notify-cmd 'skrypt.sh {msg}']
Alarm testowy (z tej samej maszyny):
  python3 hub.py alert --level 2 --text "Test alarmu"
  python3 hub.py alert --cancel

Od 0.14 telefony podpisują żądania kluczem urządzenia (devkeys.py), wspólny token działa przejściowo.
Fuzja: ≥2 zaufane punkty albo zaufany + nowe w pobliżu → poziom 1; same nowe punkty (≥5 kratek i sieci)
→ ciche „Podejrzenie”. Łańcuch (≥3 punkty słyszą po kolei wzdłuż linii) liczy się tak samo. Poziomy 2–3 tylko ręcznie.
Trwający alarm (HOLD_S) blokuje automatyczny alarm tego samego lub niższego poziomu.
Alarmy nie zawierają współrzędnych (ZASADY pkt 2).
"""
import argparse, collections, gzip, hmac, io, json, math, mimetypes, os, re, shlex, struct, subprocess, sys, threading, time, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import devkeys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("SKYWATCH_DATA") or os.path.join(HERE, "data")   # inny katalog = osobny hub testowy
ADMIN = os.path.join(HERE, "admin")  # panel koordynatora: index.html + leaflet/
DIST = os.path.join(HERE, "dist")   # <flavor>/skywatch.apk + <flavor>/version.json: {"versionCode": 2, "versionName": "0.2"}
APK_NAMES = {"user": "Czujnik", "dev": "Czujnik-dev", "alarm": "Alarm"}
FLAVORS = ("user", "dev", "alarm")  # user = Czujnik (pakiet jak do 0.17), alarm = osobna aplikacja Alarm (od 0.18); sprzed 0.9 bez flavor → „user”
MAX_CLIP = 1024 * 1024                   # nagranie z telefonu: do 15 s, 16 kHz, mono, 16 bit ≈ 480 kB
CLIPS_QUOTA = 500 * 1024 * 1024          # łącznie na nagrania; pełny dysk = brak alarmów, więc nadmiar odrzucamy
CLIP_DAYS = 14                           # nagrania starsze niż tyle dni są kasowane, chyba że są przy alarmie
ALARM_CLIP_WIN = (180, 120)              # s przed / po alarmie: wykrycia z tego okna mają nagrania trzymane na stałe
clip_keep = set()                        # (węzeł, nazwa): nagrania z wykryć, które wywołały alarm — nie kasujemy
clip_ok = {}                             # (węzeł, nazwa) → czas: nagrania zapowiedziane wykryciem po tonie
FUSE_WINDOW = {"detection": 60, "manual": 120}  # s
NEW_MIN = 5          # same nowe punkty: co najmniej tyle różnych kratek i różnych sieci
CONFIRM_KM = 5       # zaufany punkt w tym promieniu potwierdza zgłoszenia nowych
SPAN_KM = 8          # zgłoszenia jednego drona mieszczą się w takim obszarze (słychać go na 1–3 km)
HOLD_S = 600         # s: nieodwołany alarm tyle trwa; w tym czasie automat nie wysyła alarmu tego samego ani niższego poziomu
CHAIN = {"window": 300, "min": 3, "speed": (25, 80), "rms_s": 20}   # łańcuch: kolejne pojedyncze wykrycia wzdłuż jednej linii
POS_MAX_AGE = 300    # s: starsza pozycja z pulsu nie jest doklejana do zgłoszenia
GRID = (0.0027, 0.0044)   # siatka ~300 m (szer., dł.): dokładniejszej pozycji telefonu nie przechowujemy (ochrona lokalizacji ludzi)
EVENTS_DAYS = 30     # dziennik zdarzeń starszy niż tyle dni jest kasowany
# Wersja dev (tylko zaufane telefony z kanałem „dev”, do kalibracji zasięgu): dziennik co sekundę + nagrania po głośności
DEVLOG_DAYS = 30
DEVLOG_QUOTA = 2 * 1024 ** 3             # dziennik dev łącznie; ponad to kasujemy najstarsze dni
DEVCLIPS_QUOTA = 300 * 1024 * 1024       # nagrania dev po głośności (osobno od nagrań po tonie): ponad to kasujemy najstarsze
DEVLOG_RATE = 120                        # paczek na godzinę z jednego telefonu (aplikacja wysyła co minutę)
RATE = {"heartbeat": 120, "detection": 240, "track": 60, "manual": 6, "feedback": 20, "station": 12}  # zdarzeń na godzinę z jednego telefonu
# Walidacja: dowolny tekst z telefonu jest odrzucany. Napisy tylko z tych wzorców, reszta pól tekstowych wypada.
STR_OK = {
    "node": r"[0-9a-f-]{8,40}", "clip": r"[A-Za-z0-9._-]{1,64}", "app": r"\d{1,3}\.\d{1,3}(-dev)?",
    "model": r"[A-Za-z0-9 ._-]{1,32}", "source": r"-|[A-Z_]{1,24}( \(niedeklarowane\))?",
    "kind": r"hear|see|confirm|false_alarm", "method": r"audio_level|harmonic",
    "mode": r"personal|receiver|sensor", "flavor": r"user|dev|alarm", "type": r"heartbeat|detection|track|manual|feedback|station",
    "battery_bin": r"low|mid|high", "key_hw": r"strongbox|tee|software",
}
NESTED_STR = re.compile(r"[A-Za-z0-9 ._:%-]{0,24}")   # krótkie znaczniki diagnostyczne, np. loc_diag.providers.gps = "ok 17m"

APK_PAGE = """<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SkywatchPL — pobierz aplikację</title>
<style>
 body{{margin:0;background:#0B1220;color:#E6ECF5;font-family:system-ui,Segoe UI,Roboto,sans-serif;
      display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}}
 .card{{max-width:440px;width:100%;text-align:center}}
 h1{{font-size:1.5rem;margin:0 0 .3em}}
 p{{color:#9FB0C8;line-height:1.5}}
 a.btn{{display:block;margin:14px 0;padding:16px;border-radius:14px;background:#4C8DFF;color:#fff;
      text-decoration:none;font-weight:600;font-size:1.05rem}}
 a.dev{{background:#1b2740;color:#9FB0C8}}
 small{{color:#6b7a94}}
 .note{{margin-top:22px;font-size:.9rem;color:#9FB0C8;text-align:left;background:#121c30;
      border-radius:12px;padding:14px}}
</style></head><body><div class="card">
 <h1>SkywatchPL</h1>
 <p>Obywatelski nasłuch dronów. Dwie osobne aplikacje na Androida — dotknij pobranego pliku, aby zainstalować.</p>
 <a class="btn" href="/apk/alarm">SkywatchPL Alarm &middot; {aver}<br><small style="color:#dfe8ff">ostrzeżenia o dronach i przycisk „słyszę / widzę”</small></a>
 <a class="btn" href="/apk/user">SkywatchPL Czujnik &middot; {uver}<br><small style="color:#dfe8ff">nasłuch nieba, bez alarmów — telefon na stałe przy oknie</small></a>
 <a class="btn dev" href="/apk/dev">Czujnik z diagnostyką (dev) &middot; {dver}</a>
 <div class="note">Czujnik i Alarm trzymaj na <b>osobnych telefonach</b>: czujnik nigdy nie gra alarmu, żeby się nie zdradzić
 i nie nagrać własnej syreny. Po pobraniu Android zapyta o zgodę na instalację z tego źródła — trzeba ją raz włączyć.
 Aplikacje same powiadomią o kolejnych aktualizacjach. Tylko wykrywanie i alarmy; nic nie naprowadza.</div>
 <p><small>iPhone na razie nieobsługiwany.</small></p>
</div></body></html>"""

lock = threading.Lock()
log_lock = threading.Lock()   # dopisywanie do dzienników vs. przycinanie events.jsonl
alerts = []          # [{id, level, text, t, cancel}]
recent = []          # [{t (czas zdarzenia, zegar serwera), typ, node, trust, lat, lon, net}]
nodes = {}           # node -> ostatni puls
stations = {}        # node -> {lat, lon}: kratka stacji (od 0.14 puls nie ma pozycji)
events = collections.deque(maxlen=5000)   # ostatnie zgłoszenia (bez pulsów) dla panelu
a = None


def log(kind, rec):
    os.makedirs(DATA, exist_ok=True)
    with log_lock, open(os.path.join(DATA, f"{kind}.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def clean(v, depth=0):
    """Zostawia tylko liczby, wartości logiczne, krótkie znaczniki i płytkie słowniki/listy z nich. None = odrzuć."""
    if v is None or isinstance(v, bool): return v
    if isinstance(v, (int, float)): return v if math.isfinite(v) and abs(v) < 1e13 else None
    if isinstance(v, str): return v if depth and NESTED_STR.fullmatch(v) else None
    if depth >= 3: return None
    if isinstance(v, list): return [c for c in (clean(x, depth + 1) for x in v[:32]) if c is not None]
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:40]:
            if isinstance(k, str) and re.fullmatch(r"[a-z0-9_]{1,24}", k):
                c = clean(x, depth + 1)
                if c is not None: out[k] = c
        return out
    return None


def validate(raw):
    """Zdarzenie z telefonu → oczyszczony słownik albo ValueError. Pola serwera (_rx, _ip, pos_*) nie są przyjmowane od klienta."""
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or not re.fullmatch(STR_OK["type"], raw["type"]):
        raise ValueError("nieznany typ zdarzenia")
    if not isinstance(raw.get("node"), str) or not re.fullmatch(STR_OK["node"], raw["node"]):
        raise ValueError("zły identyfikator urządzenia")
    ev = {}
    for k, v in list(raw.items())[:60]:
        if not isinstance(k, str) or k.startswith("_") or k.startswith("pos_") or not re.fullmatch(r"[a-z0-9_]{1,24}", k): continue
        if isinstance(v, str):
            if k in STR_OK and re.fullmatch(STR_OK[k], v): ev[k] = v
            continue
        c = clean(v)
        if c is not None: ev[k] = c
    return ev


rate_log = collections.defaultdict(collections.deque)   # (węzeł, typ) → czasy przyjętych zdarzeń z ostatniej godziny


def over_limit(ev):
    now, q = time.time(), None
    with lock:
        q = rate_log[(ev["node"], ev["type"])]
        while q and now - q[0] > 3600: q.popleft()
        if len(q) >= RATE.get(ev["type"], 60): return True
        q.append(now)
    return False


def coarsen(ev):
    """Sprowadza pozycję do środka kratki ~300 m (ta sama siatka co na telefonie od 0.14) — także w zgłoszeniach ręcznych,
    które z telefonów 0.13 przychodzą z pełną dokładnością GPS. Do namierzania liczy się kierunek, a 300 m przy celu
    kilka km dalej to tyle, co błąd kompasu telefonu."""
    for k, step in zip(("lat", "lon"), GRID):
        if isinstance(ev.get(k), (int, float)): ev[k] = round((math.floor(ev[k] / step) + 0.5) * step, 5)
    if isinstance(ev.get("acc"), (int, float)): ev["acc"] = max(ev["acc"], 300)


def wav_ok(data):
    """Tylko to, co wysyła aplikacja: WAV PCM, mono, 16 bit, 8–48 kHz, do 20 s. Wszystko inne odrzucamy."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:16] != b"WAVEfmt ": return False
    fmt, ch, rate, _, _, bits = struct.unpack("<HHIIHH", data[20:36])
    # dev wysyła po filtrze 600 Hz w 8 kHz: do 60 s (ten sam rozmiar co 20 s w 16 kHz)
    return fmt == 1 and ch == 1 and bits == 16 and 8000 <= rate <= 48000 and len(data) - 44 <= rate * 2 * (60 if rate <= 8000 else 20)


def clips_size():
    tot = 0
    for root, _, files in os.walk(os.path.join(DATA, "clips")):
        for f in files:
            try: tot += os.path.getsize(os.path.join(root, f))
            except OSError: pass
    return tot


def load_alerts():
    p = os.path.join(DATA, "alerts.jsonl")
    if os.path.exists(p):
        for line in open(p):
            try: alerts.append(json.loads(line))
            except ValueError: pass


def load_keep():
    p = os.path.join(DATA, "clips_keep.jsonl")
    if os.path.exists(p):
        for line in open(p):
            try: r = json.loads(line); clip_keep.add((r["node"], r["clip"]))
            except (ValueError, KeyError): pass


def keep_clip(ev, alert_id):
    """Oznacza nagranie wykrycia jako alarmowe (trwałe). Plik może jeszcze nie dojść — nazwa jest znana z wykrycia."""
    if ev.get("type") != "detection" or not ev.get("clip"): return
    k = (clip_dir(ev.get("node", "x")), ev["clip"])
    with lock:
        if k in clip_keep: return
        clip_keep.add(k)
    log("clips_keep", {"node": k[0], "clip": k[1], "alert": alert_id, "t": int(time.time() * 1000)})
    print(f"  nagranie {k[0][:6]}/{k[1]} zachowane na stałe (alarm #{alert_id})", flush=True)


def keep_alarm_clips(al):
    """Alarm (także później odwołany — to cenny przykład fałszywki) utrwala nagrania wykryć z okna wokół niego."""
    if al.get("cancel"): return
    t = al["t"] / 1000
    with lock: evs = [e for e in events if t - ALARM_CLIP_WIN[0] <= e.get("_rx", 0) <= t + ALARM_CLIP_WIN[1]]
    for e in evs: keep_clip(e, al["id"])


def add_alert(level, text, cancel=False, quiet=False):
    """quiet = „Podejrzenie” od samych nowych punktów: telefony 0.14+ pokazują je bez dźwięku."""
    with lock:
        al = {"id": (alerts[-1]["id"] + 1) if alerts else 1, "level": level, "text": text,
              "t": int(time.time() * 1000), "cancel": cancel}
        if quiet: al["quiet"] = True
        alerts.append(al)
    log("alerts", al)
    print(f"ALARM #{al['id']} poziom {level}{' (odwołanie)' if cancel else ''}: {text}", flush=True)
    keep_alarm_clips(al)
    return al


def notify(msg):
    if not (a and a.notify_cmd): return
    def run():
        try:
            cmd = [c.replace("{msg}", msg) for c in shlex.split(a.notify_cmd)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except Exception as e:
            print(f"  błąd powiadomienia: {e}", flush=True)
    threading.Thread(target=run, daemon=True).start()


def attach_pos(ev):
    """Zgłoszenie bez współrzędnych dostaje pozycję z ostatniego pulsu tego telefonu.
    Czas liczony zegarem telefonu (pole t), bo zgłoszenia z kolejki offline przychodzą z opóźnieniem."""
    if ev.get("lat") is not None: return
    with lock: hb, st = nodes.get(ev.get("node", "?")), stations.get(ev.get("node", "?"))
    if st:
        ev.update(lat=st["lat"], lon=st["lon"], acc=300, pos_src="station"); return
    if not hb or hb.get("lat") is None: return
    try: age = abs(ev["t"] - hb["t"]) / 1000
    except (KeyError, TypeError): age = ev["_rx"] - hb["_rx"]
    if age > POS_MAX_AGE: return
    ev.update(lat=hb["lat"], lon=hb["lon"], acc=hb.get("acc"), pos_src="heartbeat", pos_age_s=round(age))


def load_nodes():
    """Po restarcie: ostatni puls każdego telefonu i ostatnie zgłoszenia z dziennika."""
    p = os.path.join(DATA, "events.jsonl")
    if not os.path.exists(p): return
    for line in open(p):
        try: ev = json.loads(line)
        except ValueError: continue
        if ev.get("type") == "heartbeat": nodes[ev.get("node", "?")] = ev
        elif ev.get("type") == "station": stations[ev.get("node", "?")] = {"lat": ev.get("lat"), "lon": ev.get("lon")}
        else: events.append(ev)


def clip_dir(node):
    return "".join(c for c in node if c.isalnum())[:36] or "x"


def dir_size(d):
    tot = 0
    for root, _, files in os.walk(d):
        for f in files:
            try: tot += os.path.getsize(os.path.join(root, f))
            except OSError: pass
    return tot


def trim_oldest(d, quota):
    """Kasuje najstarsze pliki w katalogu d, aż zmieści się w limicie."""
    files = []
    for root, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(root, f)
            try: files.append((os.path.getmtime(p), os.path.getsize(p), p))
            except OSError: pass
    tot = sum(x[1] for x in files)
    for _, size, p in sorted(files):
        if tot <= quota: break
        try: os.remove(p); tot -= size
        except OSError: pass


def dev_node(node):
    """Telefon koordynatora z wersją dev: podpisany kluczem, zaufany, ostatni puls z kanału „dev”."""
    with lock: hb = nodes.get(node) or {}
    return bool(node) and devkeys.trust_of(node) == "trusted" and hb.get("flavor") == "dev"


def clean_dev(v, depth=0):
    """Jak clean(), ale z dłuższymi listami (widmo, punkty toru) — wiersze dziennika dev."""
    if v is None or isinstance(v, bool): return v
    if isinstance(v, (int, float)): return v if math.isfinite(v) and abs(v) < 1e13 else None
    if isinstance(v, str): return v if NESTED_STR.fullmatch(v) else None
    if depth >= 4: return None
    if isinstance(v, list): return [c for c in (clean_dev(x, depth + 1) for x in v[:400]) if c is not None]
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:40]:
            if isinstance(k, str) and re.fullmatch(r"[a-z0-9_]{1,24}", k):
                c = clean_dev(x, depth + 1)
                if c is not None: out[k] = c
        return out
    return None


devlog_rate = collections.defaultdict(collections.deque)


def admin_state(minutes):
    """Widok koordynatora: punkty z ostatnim pulsem + zgłoszenia z ostatnich `minutes` minut."""
    now = time.time()
    with lock:
        evs = [e for e in events if now - e["_rx"] < minutes * 60]
        hbs = list(nodes.values())
        al = alerts[-20:]
    per = collections.Counter(e.get("node") for e in evs if e.get("type") == "detection")
    keep = ("lat", "lon", "acc", "battery", "charging", "app", "flavor", "model", "mode",
            "floor_db", "loc_diag", "t", "_rx")
    def pt(h):
        p = dict({k: h.get(k) for k in keep}, node=h.get("node"), det=per.get(h.get("node"), 0), trust=devkeys.trust_of(h.get("node")))
        st = stations.get(h.get("node"))
        if p["lat"] is None and st: p.update(lat=st["lat"], lon=st["lon"], acc=300, station=True)
        if p["battery"] is None and h.get("battery_bin"): p["battery"] = {"low": "<20", "mid": "20–60", "high": ">60"}[h["battery_bin"]]
        return p
    pts = [pt(h) for h in hbs]
    def out(e):
        o = {k: v for k, v in e.items() if k not in ("_ip", "_net")}
        if e.get("clip") and (clip_dir(e.get("node", "x")), e["clip"]) in clip_keep: o["keep"] = True
        return o
    return {"now": now, "nodes": pts, "alerts": al, "stats": stats(), "keys": devkeys.summary(),
            "events": [out(e) for e in evs][-1500:]}


def flavor(q):
    f = q.get("flavor", ["user"])[0]
    return f if f in FLAVORS else "user"


def stats():
    """Liczba urządzeń: aktywne teraz (puls < 3 min), w ostatniej godzinie i dobie, z podziałem na tryb i wersję."""
    now = time.time()
    with lock: hbs = list(nodes.values())
    def cnt(sec):
        sel = [h for h in hbs if now - h["_rx"] < sec]
        mode = collections.Counter("receiver" if h.get("mode") == "receiver" else "sender" for h in sel)
        return {"total": len(sel), "senders": mode["sender"], "receivers": mode["receiver"],
                "sensors": sum(1 for h in sel if h.get("mode") == "sensor")}
    act = [h for h in hbs if now - h["_rx"] < 180]
    return {"active": cnt(180), "hour": cnt(3600), "day": cnt(86400), "known": len(hbs),
            "versions": dict(collections.Counter(f"{h.get('app', '?')}" for h in act).most_common()),
            "flavors": dict(collections.Counter(h.get("flavor", "user") for h in act))}


def event_time(ev):
    """Czas zdarzenia na zegarze serwera: pole t (zegar telefonu) poprawione o przesunięcie zegara
    z ostatniego pulsu. Zgłoszenia z kolejki offline przychodzą z opóźnieniem — liczy się, kiedy się wydarzyły."""
    with lock: hb = nodes.get(ev.get("node", "?"))
    try:
        skew = hb["_rx"] - hb["t"] / 1000 if hb else 0
        return min(ev["t"] / 1000 + skew, ev["_rx"])
    except (KeyError, TypeError):
        return ev["_rx"]


def km(a, b):
    dy = (a["lat"] - b["lat"]) * 111.32
    dx = (a["lon"] - b["lon"]) * 111.32 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def has_pos(r): return isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))


def judge(grp):
    """Zgłoszenia jednego typu z okna fuzji (po jednym na punkt) → (poziom, cichy, liczba punktów) albo None.
    Zaufane (i stare aplikacje na wspólnym tokenie): ≥2 punkty. Zaufany potwierdza nowe w promieniu CONFIRM_KM.
    Same nowe: ≥NEW_MIN różnych kratek i sieci, wszystko w obszarze SPAN_KM (jeden dron) → ciche „Podejrzenie”."""
    tr = [r for r in grp if r["trust"] in ("trusted", "legacy")]
    nw = [r for r in grp if r["trust"] == "new"]
    if len(tr) >= 2: return 1, False, len(grp)
    if tr:
        near = [r for r in nw if has_pos(r) and any(has_pos(x) and km(r, x) <= CONFIRM_KM for x in tr)]
        return (1, False, 1 + len(near)) if near else None
    pts = [r for r in nw if has_pos(r)]
    cells, nets = {(r["lat"], r["lon"]) for r in pts}, {r["net"] for r in pts if r.get("net")}
    if len(cells) < NEW_MIN or len(nets) < NEW_MIN: return None
    if max(km(a, b) for a in pts for b in pts) > SPAN_KM: return None
    return 1, True, len(pts)


def xy_km(r, o):
    return ((r["lon"] - o["lon"]) * 111.32 * math.cos(math.radians(o["lat"])), (r["lat"] - o["lat"]) * 111.32)


def chain(recs):
    """Łańcuch wykryć: punkty, które słyszały przelot po kolei (nie naraz), układają się wzdłuż jednej linii
    z prędkością drona. Czas punktu = średnia jego wykryć ≈ chwila największego zbliżenia.
    recs: wykrycia tonu z ostatnich CHAIN["window"] s. Zwraca (punkty, kierunek °, rms s) albo None."""
    by = collections.defaultdict(list)
    for r in recs:
        if has_pos(r): by[r["node"]].append(r)
    if len(by) < CHAIN["min"]: return None
    pts = [dict(rs[-1], t=sum(x["t"] for x in rs) / len(rs)) for rs in by.values()]
    T = [p["t"] for p in pts]; tm = sum(T) / len(T); T = [t - tm for t in T]
    if max(T) - min(T) < 30: return None          # wszyscy naraz = źródło w miejscu albo zwykła fuzja
    o = pts[0]; XY = [xy_km(p, o) for p in pts]
    vt = sum(t * t for t in T)
    best = None
    for deg in range(0, 360, 2):
        ux, uy = math.sin(math.radians(deg)), math.cos(math.radians(deg))   # azymut: 0 = północ
        pr = [(x * ux + y * uy) * 1000 for x, y in XY]
        pm = sum(pr) / len(pr)
        sp = sum(t * (q - pm) for t, q in zip(T, pr)) / vt
        if not CHAIN["speed"][0] <= sp <= CHAIN["speed"][1]: continue
        rms = math.sqrt(sum((q - pm - sp * t) ** 2 for t, q in zip(T, pr)) / len(pr)) / sp
        cr = [-x * uy + y * ux for x, y in XY]; cm = sum(cr) / len(cr)
        if max(abs(c - cm) for c in cr) > 3: continue   # km w poprzek: słychać go najwyżej na ~2,5 km
        if best is None or rms < best[2]: best = (pts, deg, rms)
    return best if best and best[2] <= CHAIN["rms_s"] else None


def chain_verdict(pts):
    """Zaufany w łańcuchu → poziom 1; same nowe z ≥CHAIN["min"] różnych kratek i sieci → ciche Podejrzenie."""
    if any(p["trust"] in ("trusted", "legacy") for p in pts): return 1, False, len(pts)
    cells, nets = {(p["lat"], p["lon"]) for p in pts}, {p["net"] for p in pts if p.get("net")}
    if len(cells) >= CHAIN["min"] and len(nets) >= CHAIN["min"]: return 1, True, len(pts)
    return None


def active_alert(now):
    """Najmocniejszy trwający alarm: nieodwołany i nie starszy niż HOLD_S → (poziom, głośny) albo None."""
    act = None
    for al in reversed(alerts):
        if al["cancel"] or now - al["t"] / 1000 > HOLD_S: break
        k = (al["level"], not al.get("quiet"))
        if act is None or k > act: act = k
    return act


def fuse(ev):
    """Fuzja zgłoszeń z poziomami zaufania (judge). Liczą się tylko wykrycia tonu (harmonic) i zgłoszenia ręczne;
    sama głośność nie podnosi alarmu. Nowe punkty nie liczą się, gdy wpływ nowych jest wstrzymany.
    Gdy naraz słyszy za mało punktów, wykrycia ułożone po kolei wzdłuż linii (chain) też się liczą."""
    typ, node, now = ev["type"], ev.get("node", "?"), time.time()
    win = FUSE_WINDOW.get(typ)
    if not win: return
    if typ == "detection" and ev.get("method") != "harmonic": return
    t = event_time(ev)
    what = "zgłoszenie ręczne" if typ == "manual" else "wykrycie dźwięku"
    short = node[:6]
    if now - t > win:
        print(f"  fuzja: pominięte stare {what} z {short} ({round(now - t)} s)", flush=True)
        return
    trust = ev.get("_trust", "legacy")
    if trust == "new" and devkeys.paused["on"]:
        print(f"  fuzja: {what} z nowego punktu {short} pominięte (wpływ nowych wstrzymany)", flush=True)
        return
    with lock:
        recent[:] = [r for r in recent if now - r["t"] < CHAIN["window"]]
        recent.append({"t": t, "typ": typ, "node": node, "trust": trust, "lat": ev.get("lat"), "lon": ev.get("lon"), "net": ev.get("_net")})
        ok = [r for r in recent if r["typ"] == typ and not (r["trust"] == "new" and devkeys.paused["on"])]
        per = {r["node"]: r for r in ok if abs(t - r["t"]) < win}
        act = active_alert(now)
    verdict, how = judge(list(per.values())), None
    if typ == "detection" and not (verdict and not verdict[1]):
        ch = chain(ok)
        cv = chain_verdict(ch[0]) if ch else None
        if cv and (not verdict or not cv[1]): verdict, how = cv, ch
    # trwający alarm blokuje automat, chyba że to „Podejrzenie”, które teraz potwierdził zaufany punkt
    if verdict and (act is None or (1, not verdict[1]) > act):
        level, quiet, n = verdict
        src = f"{n} punktów po kolei słyszy przelot, kierunek ok. {how[1]}°" if how else None
        if quiet:
            add_alert(level, f"Podejrzenie: {src or f'{n} nowych punktów zgłasza {what}'}. Bez potwierdzenia zaufanego punktu.", quiet=True)
            notify(f"SkywatchPL: PODEJRZENIE (ciche), {n} nowych punktów, {what}{' (łańcuch)' if how else ''}")
        else:
            add_alert(level, f"{src or f'Kilka punktów ({n}) zgłasza: {what}'}. Niepotwierdzone — nasłuchuj.")
            notify(f"SkywatchPL: poziom 1, {n} punkty, {what}{' (łańcuch)' if how else ''}")
    elif verdict:
        print(f"  fuzja: {what} z {short} pasuje do trwającego alarmu — bez nowego", flush=True)
    else:
        notify(f"SkywatchPL: pojedyncze {what} z punktu {short} ({trust})")


class H(BaseHTTPRequestHandler):
    server_version = "SkywatchHub/0.1"

    def log_message(self, fmt, *args): pass

    def reply(self, code, obj):
        if code >= 400 and self.path.startswith("/api/") and code != 404:   # odrzucenia telefonów — bez tego „nie łączy się” jest niewidoczne
            print(f"{time.strftime('%H:%M:%S')} odrzucone {self.command} {urlparse(self.path).path} [{code}] "
                  f"{(self.headers.get('X-Skw-Node') or '-')[:6]}: {obj.get('error', '')}", flush=True)
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self):
        """Funnel i tailscale serve łączą się z lokalnego adresu; adres klienta jest w X-Forwarded-For (ostatni wpis)."""
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1") and self.headers.get("X-Forwarded-For"):
            ip = self.headers["X-Forwarded-For"].split(",")[-1].strip()
        return ip

    def token_ok(self):
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {a.token}".encode())

    def authed(self, body=b""):
        """Podpis kluczem urządzenia (od 0.14) albo — przejściowo — wspólny token grupy (trust „legacy”)."""
        node, trust, err = devkeys.check(self.headers, self.command, self.path, body)
        if err:
            self.reply(403 if "zablokowany" in err else 401, {"error": err}); return False
        self.signed, self.trust = node, trust
        if node: return True
        if self.token_ok() and not os.path.exists(os.path.join(DATA, "legacy_off")): self.trust = "legacy"; return True
        self.reply(401, {"error": "zły token" if not self.token_ok() else "stara wersja chwilowo odłączona"}); return False

    def admin_ok(self):
        """Panel ma osobny token: token grupy jest na każdym telefonie, a mapa punktów to dane tylko dla koordynatora."""
        got = self.headers.get("Authorization", "").encode()
        if a.admin_token and hmac.compare_digest(got, f"Bearer {a.admin_token}".encode()): return True
        self.reply(401 if a.admin_token else 403, {"error": "zły token admina" if a.admin_token else "panel wyłączony"})
        return False

    def send_file(self, path, ctype=None):
        data = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    admin_port = False   # panel tylko na osobnym porcie (HA), którego Funnel nie wystawia

    def no_admin_here(self):
        if self.admin_port: return False
        self.reply(404, {"error": "nie ma"}); return True

    def from_internet(self):
        """Tailscale Funnel dokleja ten nagłówek do ruchu z internetu. Panelu tam nie ma (dostęp tylko z LAN i tailnetu)."""
        if self.headers.get("Tailscale-Funnel-Request") is None: return False
        self.reply(404, {"error": "nie ma"}); return True

    def do_admin_get(self, u):
        if self.no_admin_here() or self.from_internet(): return
        if u.path == "/admin":   # adresy w stronie są względne, więc potrzebny końcowy „/”
            self.send_response(301); self.send_header("Location", "/admin/"); self.end_headers(); return
        if u.path == "/admin/":
            return self.send_file(os.path.join(ADMIN, "index.html"), "text/html; charset=utf-8")
        if u.path.startswith("/admin/leaflet/"):
            p = os.path.normpath(os.path.join(ADMIN, u.path[len("/admin/"):]))
            if p.startswith(os.path.join(ADMIN, "leaflet")) and os.path.isfile(p): return self.send_file(p)
            return self.reply(404, {"error": "nie ma"})
        if not self.admin_ok(): return
        q = parse_qs(u.query)
        if u.path == "/admin/api/state":
            return self.reply(200, admin_state(min(int(q.get("min", ["60"])[0]), 24 * 60)))
        if u.path == "/admin/api/stats": return self.reply(200, stats())
        if u.path == "/admin/api/clip":
            name = "".join(c for c in q.get("name", [""])[0] if c.isalnum() or c in "._-")[:64]
            p = os.path.join(DATA, "clips", clip_dir(q.get("node", [""])[0]), name)
            if name and os.path.isfile(p): return self.send_file(p, "audio/wav")
            return self.reply(404, {"error": "brak nagrania"})
        self.reply(404, {"error": "nie ma"})

    def do_apk_get(self, u):
        """Publiczna strona pobierania APK (bez tokenu). /apk = strona, /apk/user i /apk/dev = pliki."""
        if u.path in ("/apk/user", "/apk/dev", "/apk/alarm"):
            fl = u.path.rsplit("/", 1)[1]
            p = os.path.join(DIST, fl, "skywatch.apk")
            if not os.path.isfile(p): return self.reply(404, {"error": "brak APK"})
            try: ver = json.load(open(os.path.join(DIST, fl, "version.json")))["versionName"]
            except (OSError, ValueError, KeyError): ver = "0"
            data = open(p, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Disposition", f'attachment; filename="SkywatchPL-{APK_NAMES[fl]}-{ver}.apk"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            print(f"{time.strftime('%H:%M:%S')} pobranie APK ({fl}) {(self.headers.get('User-Agent') or '-')[:60]}", flush=True)
            return
        if u.path in ("/apk", "/apk/"):
            def ver(fl):
                try: return json.load(open(os.path.join(DIST, fl, "version.json")))["versionName"]
                except (OSError, ValueError, KeyError): return "?"
            html = APK_PAGE.format(uver=ver("user"), dver=ver("dev"), aver=ver("alarm"))
            return self.send_file_bytes(html.encode(), "text/html; charset=utf-8")
        self.reply(404, {"error": "nie ma"})

    def send_file_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self, limit):
        n = int(self.headers.get("Content-Length", 0))
        if n > limit: raise ValueError("za duże")
        return self.rfile.read(n)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/ping": return self.reply(200, {"ok": True, "t": int(time.time() * 1000)})
        if u.path == "/admin" or u.path.startswith("/admin/"): return self.do_admin_get(u)
        if u.path == "/apk" or u.path.startswith("/apk/"): return self.do_apk_get(u)
        if u.path == "/api/update":   # bez podpisu: telefon odrzucany przez serwer musi móc dostać poprawkę
            try: return self.reply(200, json.load(open(os.path.join(DIST, flavor(parse_qs(u.query)), "version.json"))))
            except (OSError, ValueError): return self.reply(200, {"versionCode": 0})
        if not self.authed(): return
        if u.path == "/api/alerts":
            q = parse_qs(u.query)
            since = int(q.get("since", ["-1"])[0])
            with lock:
                last = alerts[-1]["id"] if alerts else 0
                out = [] if since < 0 else [x for x in alerts if x["id"] > since][-20:]
            return self.reply(200, {"last": last, "alerts": out, "t": int(time.time() * 1000)})
        if u.path == "/api/update":
            try: return self.reply(200, json.load(open(os.path.join(DIST, flavor(parse_qs(u.query)), "version.json"))))
            except (OSError, ValueError): return self.reply(200, {"versionCode": 0})
        if u.path == "/api/apk":
            p = os.path.join(DIST, flavor(parse_qs(u.query)), "skywatch.apk")
            if not os.path.exists(p): return self.reply(404, {"error": "brak APK"})
            data = open(p, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"{time.strftime('%H:%M:%S')} pobranie aktualizacji", flush=True)
            return
        if u.path == "/api/status":
            now = time.time()
            with lock:
                alive = [v for v in nodes.values() if now - v["_rx"] < 180]
            return self.reply(200, {"alive": len(alive), "receivers": sum(1 for v in alive if v.get("mode") == "receiver")})
        self.reply(404, {"error": "nie ma"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/admin/api/alert":
            if self.no_admin_here() or self.from_internet() or not self.admin_ok(): return
            try:
                r = json.loads(self.body(4096))
                if r.get("cancel"): al = add_alert(0, r.get("text") or "Odwołanie alarmu", cancel=True)
                else:
                    level = int(r.get("level", 0))
                    if level not in (1, 2, 3) or not r.get("text"): raise ValueError("poziom 1–3 i treść")
                    al = add_alert(level, str(r["text"])[:300])
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self.reply(400, {"error": str(e)})
            return self.reply(200, al)
        if u.path in ("/admin/api/trust", "/admin/api/pause"):
            if self.no_admin_here() or self.from_internet() or not self.admin_ok(): return
            try: r = json.loads(self.body(4096))
            except (ValueError, json.JSONDecodeError): return self.reply(400, {"error": "zła treść"})
            if u.path == "/admin/api/pause":
                devkeys.set_pause(bool(r.get("on")))
                print(f"{time.strftime('%H:%M:%S')} panel: wpływ nowych punktów {'WSTRZYMANY' if r.get('on') else 'przywrócony'}", flush=True)
                return self.reply(200, devkeys.summary())
            node = devkeys.set_trust(str(r.get("code", "")), str(r.get("trust", "")))
            if not node: return self.reply(404, {"error": "nie ma takiego kodu (albo pasuje do kilku punktów)"})
            print(f"{time.strftime('%H:%M:%S')} panel: punkt {node[:6]} → {r.get('trust')}", flush=True)
            return self.reply(200, {"node": node, "trust": r.get("trust")})
        try: raw = self.body(MAX_CLIP if u.path == "/api/clip" else 512 * 1024 if u.path == "/api/devlog" else 64 * 1024)
        except ValueError: return self.reply(413, {"error": "za duże"})
        if u.path == "/api/join":
            code, out, note = devkeys.join(self.headers, self.path, raw, self.client_ip(),
                                           {n for n, h in nodes.items() if devkeys.trust_of(n) == "legacy"}, self.token_ok())
            if code == 200: print(f"{time.strftime('%H:%M:%S')} dołączył punkt {out['node'][:6]} ({out['trust']})", flush=True)
            if note: print(note, flush=True); notify(note)
            return self.reply(code, out)
        if not self.authed(raw): return
        try:
            if u.path == "/api/event":
                ev = validate(json.loads(raw))
                if self.signed and ev["node"] != self.signed: return self.reply(403, {"error": "zdarzenie innego urządzenia"})
                if over_limit(ev):
                    print(f"{time.strftime('%H:%M:%S')} limit: {ev['type']} od {ev['node'][:6]} odrzucone", flush=True)
                    return self.reply(429, {"error": "za dużo zgłoszeń, spróbuj później"})
                ev.pop("moving", None); ev.pop("speed_kmh", None)   # starsze wersje: ruchu telefonu nie przechowujemy
                ev["_rx"] = time.time()
                ev["_ip"] = self.client_ip()   # tylko w pamięci, do dziennika nie trafia
                ev["_net"] = devkeys.net_of(ev["_ip"])
                ev["_trust"] = self.trust
                coarsen(ev)
                if ev["type"] != "heartbeat": attach_pos(ev)
                if ev["type"] == "detection" and ev.get("method") == "harmonic" and ev.get("clip"):
                    with lock: clip_ok[(clip_dir(ev.get("node", "x")), ev["clip"])] = time.time()
                log("events", {k: v for k, v in ev.items() if k not in ("_ip", "_net")})
                if ev["type"] == "heartbeat":
                    with lock: nodes[ev.get("node", "?")] = ev
                elif ev["type"] == "station":
                    if ev.get("lat") is not None:
                        with lock: stations[ev["node"]] = {"lat": ev["lat"], "lon": ev["lon"]}
                else:
                    with lock: events.append(ev)
                    print(f"{time.strftime('%H:%M:%S')} {ev['type']} od {ev.get('node', '?')[:6]}: "
                          f"{ {k: v for k, v in ev.items() if not k.startswith('_') and k not in ('type', 'node', 'lat', 'lon', 'acc', 'loc_diag', 'pos_src', 'pos_age_s')} }",
                          flush=True)
                    fuse(ev)
                    with lock: last = alerts[-1] if alerts else None
                    if last and not last["cancel"] and ev["_rx"] - last["t"] / 1000 <= ALARM_CLIP_WIN[1]:
                        keep_clip(ev, last["id"])
                return self.reply(200, {"ok": True})
            if u.path == "/api/devlog":
                node = self.signed
                if not dev_node(node): return self.reply(403, {"error": "dziennik dev tylko z zaufanego telefonu z wersją dev"})
                now = time.time()
                with lock:
                    q = devlog_rate[node]
                    while q and now - q[0] > 3600: q.popleft()
                    if len(q) >= DEVLOG_RATE: return self.reply(429, {"error": "za często"})
                    q.append(now)
                try: text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(8 * 1024 * 1024 + 1)
                except (OSError, EOFError): return self.reply(400, {"error": "zły gzip"})
                if len(text) > 8 * 1024 * 1024: return self.reply(413, {"error": "za duże"})
                rows = []
                for line in text.decode("utf-8", "replace").splitlines()[:2000]:
                    try: r = clean_dev(json.loads(line))
                    except ValueError: continue
                    if isinstance(r, dict) and isinstance(r.get("t"), (int, float)) and abs(r["t"] / 1000 - now) < 3 * 86400: rows.append(r)
                d = os.path.join(DATA, "devlog", clip_dir(node))
                os.makedirs(d, exist_ok=True)
                by_day = collections.defaultdict(list)
                for r in rows: by_day[time.strftime("%Y-%m-%d", time.gmtime(r["t"] / 1000))].append(r)
                for day, rs in by_day.items():
                    with open(os.path.join(d, day + ".jsonl"), "a") as f:
                        for r in rs: f.write(json.dumps(r, separators=(",", ":")) + "\n")
                return self.reply(200, {"ok": True, "rows": len(rows)})
            if u.path == "/api/clip":
                q = parse_qs(u.query)
                if self.signed and q.get("node", [""])[0] != self.signed: return self.reply(403, {"error": "nagranie innego urządzenia"})
                node = clip_dir(q.get("node", ["x"])[0])
                name = "".join(c for c in q.get("name", ["clip.wav"])[0] if c.isalnum() or c in "._-")[:64]
                data = raw
                # przyjmujemy tylko nagrania po wykryciu tonu podobnego do Shaheda (nie po samej głośności)
                with lock: allowed = time.time() - clip_ok.get((node, name), 0) < 86400
                dev = not allowed and dev_node(self.signed)   # nagranie dev po głośności: osobny katalog i limit
                if not allowed and not dev:
                    print(f"{time.strftime('%H:%M:%S')} odrzucone nagranie od {node[:6]}: {name} (bez wykrycia tonu)", flush=True)
                    return self.reply(200, {"ok": True, "stored": False})
                if not wav_ok(data):
                    print(f"{time.strftime('%H:%M:%S')} odrzucone nagranie od {node[:6]}: {name} (zły format)", flush=True)
                    return self.reply(200, {"ok": True, "stored": False})
                if dev:
                    d = os.path.join(DATA, "devclips", node)
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, name), "wb") as f: f.write(data)
                    trim_oldest(os.path.join(DATA, "devclips"), DEVCLIPS_QUOTA)
                    print(f"{time.strftime('%H:%M:%S')} nagranie dev od {node[:6]}: {name} ({len(data) // 1024} kB)", flush=True)
                    return self.reply(200, {"ok": True})
                if clips_size() + len(data) > CLIPS_QUOTA:
                    print(f"{time.strftime('%H:%M:%S')} odrzucone nagranie od {node[:6]}: {name} (limit miejsca)", flush=True)
                    return self.reply(200, {"ok": True, "stored": False})
                d = os.path.join(DATA, "clips", node)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, name), "wb") as f: f.write(data)
                print(f"{time.strftime('%H:%M:%S')} nagranie od {node[:6]}: {name} ({len(data) // 1024} kB)", flush=True)
                return self.reply(200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as e:
            return self.reply(400, {"error": str(e)})
        self.reply(404, {"error": "nie ma"})


class HA(H):
    """Port panelu koordynatora: dostępny w LAN i w tailnecie (tailscale serve), nigdy przez Funnel."""
    admin_port = True

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/" : self.send_response(302); self.send_header("Location", "/admin/"); self.end_headers(); return
        if u.path == "/admin" or u.path.startswith("/admin/"): return self.do_admin_get(u)
        self.reply(404, {"error": "nie ma"})

    def do_POST(self):
        if urlparse(self.path).path in ("/admin/api/alert", "/admin/api/trust", "/admin/api/pause"): return H.do_POST(self)
        self.reply(404, {"error": "nie ma"})


def prune_events():
    """Usuwa z events.jsonl wpisy starsze niż EVENTS_DAYS. Zostaje ostatni puls każdego telefonu (panel i statystyki)."""
    p = os.path.join(DATA, "events.jsonl")
    if not os.path.exists(p): return
    cut = time.time() - EVENTS_DAYS * 86400
    with log_lock:
        lines = open(p).readlines()
        last_hb, keep = {}, []
        for i, line in enumerate(lines):
            try: ev = json.loads(line)
            except ValueError: continue
            if ev.get("type") == "heartbeat": last_hb[ev.get("node")] = i
            if ev.get("_rx", 0) >= cut: keep.append(i)
        keep = sorted(set(keep) | set(last_hb.values()))
        if len(keep) == len(lines): return
        tmp = p + ".tmp"
        with open(tmp, "w") as f: f.writelines(lines[i] for i in keep)
        os.replace(tmp, p)
    print(f"{time.strftime('%H:%M:%S')} dziennik zdarzeń przycięty: {len(lines)} → {len(keep)} wpisów", flush=True)


def prune_clips():
    """Co godzinę kasuje nagrania starsze niż CLIP_DAYS, przeterminowane zapowiedzi nagrań i stare wpisy dziennika."""
    while True:
        try: prune_events()
        except OSError as e: print(f"  błąd przycinania dziennika: {e}", flush=True)
        cut = time.time() - CLIP_DAYS * 86400
        for root, _, files in os.walk(os.path.join(DATA, "clips")):
            for f in files:
                p = os.path.join(root, f)
                if (os.path.basename(root), f) in clip_keep: continue
                try:
                    if os.path.getmtime(p) < cut: os.remove(p)
                except OSError: pass
        with lock:
            for k in [k for k, t in clip_ok.items() if time.time() - t > 86400]: del clip_ok[k]
        try: prune_devlog()
        except OSError as e: print(f"  błąd przycinania dziennika dev: {e}", flush=True)
        time.sleep(3600)


def prune_devlog():
    """Dziennik dev: dni sprzed wczoraj pakowane gzipem, starsze niż DEVLOG_DAYS kasowane, całość w DEVLOG_QUOTA."""
    root = os.path.join(DATA, "devlog")
    if not os.path.isdir(root): return
    today = time.strftime("%Y-%m-%d", time.gmtime())
    yday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - DEVLOG_DAYS * 86400))
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            if f[:10] < cut: os.remove(p)
            elif f.endswith(".jsonl") and f[:10] not in (today, yday):
                with open(p, "rb") as a, gzip.open(p + ".gz", "ab") as b: b.write(a.read())
                os.remove(p)
    trim_oldest(root, DEVLOG_QUOTA)


def cli_alert(args):
    """Alarm z tej maszyny: dopisuje do dziennika, a działający serwer przeładowuje go co 2 s."""
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, "alerts.jsonl")
    last = 0
    if os.path.exists(p):
        for line in open(p):
            try: last = max(last, json.loads(line)["id"])
            except (ValueError, KeyError): pass
    al = {"id": last + 1, "level": 0 if args.cancel else args.level,
          "text": args.text or ("Odwołanie alarmu" if args.cancel else "Alarm testowy"),
          "t": int(time.time() * 1000), "cancel": bool(args.cancel)}
    log("alerts", al)
    print("dopisano:", al)


def watch_alert_file():
    p = os.path.join(DATA, "alerts.jsonl")
    size = os.path.getsize(p) if os.path.exists(p) else 0
    while True:
        time.sleep(2)
        try:
            s = os.path.getsize(p)
        except OSError:
            continue
        if s <= size: size = s; continue
        with open(p) as f:
            f.seek(size); new = f.read()
        size = s
        with lock:
            known = {x["id"] for x in alerts}
            for line in new.splitlines():
                try: al = json.loads(line)
                except ValueError: continue
                if al["id"] not in known:
                    alerts.append(al)
                    print(f"ALARM #{al['id']} (z CLI) poziom {al['level']}: {al['text']}", flush=True)
                    threading.Thread(target=keep_alarm_clips, args=(al,), daemon=True).start()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "alert":
        ap = argparse.ArgumentParser(prog="hub.py alert")
        ap.add_argument("--level", type=int, default=2, choices=[1, 2, 3])
        ap.add_argument("--text")
        ap.add_argument("--cancel", action="store_true")
        cli_alert(ap.parse_args(sys.argv[2:])); sys.exit()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--admin-port", type=int, default=8766, help="port panelu koordynatora (0 = wyłączony)")
    ap.add_argument("--notify-cmd", help="komenda powiadomienia właściciela, {msg} zostanie podstawione")
    a = ap.parse_args()
    a.token = os.environ.get("SKYWATCH_TOKEN")
    a.admin_token = os.environ.get("SKYWATCH_ADMIN_TOKEN")   # bez niego panel /admin jest wyłączony
    if not a.token: sys.exit("Ustaw SKYWATCH_TOKEN (wspólny token grupy).")
    devkeys.init(DATA)
    load_alerts()
    load_nodes()
    load_keep()
    for al in alerts: keep_alarm_clips(al)   # alarmy sprzed tej zmiany i z czasu, gdy serwer nie działał
    threading.Thread(target=watch_alert_file, daemon=True).start()
    threading.Thread(target=prune_clips, daemon=True).start()
    if a.admin_port:
        threading.Thread(target=ThreadingHTTPServer((a.host, a.admin_port), HA).serve_forever, daemon=True).start()
    print(f"SkywatchPL hub na {a.host}:{a.port}, panel na :{a.admin_port}, alarmów w dzienniku: {len(alerts)}", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()

#!/usr/bin/env python3
"""
SkywatchPL — scenariusze testowe na OSOBNYM hubie (własny katalog danych, własne porty, własne tokeny).
Prawdziwe telefony nic nie dostają: testowy hub nie jest wystawiony przez Funnel, a jego tokeny są jednorazowe.

Każdy scenariusz: start huba → telefony testowe (klucze wpisane z góry do keys.jsonl) → przelot(y) / źródła
dźwięku / ataki / awarie w czasie rzeczywistym → zapis osi czasu i alarmów do out/<scenariusz>.json.

  python3 sim_tests.py --list
  python3 sim_tests.py regresja rzadka-nowe ...       # kilka naraz = równolegle, każdy na swoim porcie
"""
import argparse, base64, json, math, os, random, secrets, shutil, signal, subprocess, sys, threading, time, urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sim_phones import Phone, cell

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/tmp/skw_tests"
OUT = os.path.join(HERE, "test_out")
CENTER = (52.2297, 21.0122)
ROUTE_EW = ["52.2400,21.2000", "52.2330,21.0000", "52.2250,20.8500"]
ROUTE_NS = ["52.3300,21.0300", "52.2297,21.0122", "52.1300,20.9900"]
ROUTE_SW = ["52.1500,20.8500", "52.2297,21.0122", "52.3100,21.1800"]


def km_xy(a, b):
    return ((b[1] - a[1]) * 111.32 * math.cos(math.radians(a[0])), (b[0] - a[0]) * 111.32)


def along(route, s_km):
    for a, b in zip(route, route[1:]):
        dx, dy = km_xy(a, b); seg = math.hypot(dx, dy)
        if s_km <= seg:
            f = s_km / seg
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), math.degrees(math.atan2(dx, dy)) % 360
        s_km -= seg
    return None


def route_len(route): return sum(math.hypot(*km_xy(p, q)) for p, q in zip(route, route[1:]))


class Run:
    def __init__(self, name, port, seed=1):
        self.name, self.port, self.rnd = name, port, random.Random(seed)
        self.dir = os.path.join(ROOT, name)
        shutil.rmtree(self.dir, ignore_errors=True); os.makedirs(self.dir)
        self.token, self.admin_tok = secrets.token_hex(16), secrets.token_hex(16)
        self.url, self.aurl = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port + 1}"
        self.phones, self.log, self.t0, self.proc = [], [], time.time(), None
        self.alerts_seen, self.lat = {}, []

    # --- hub ---
    def start_hub(self):
        env = dict(os.environ, SKYWATCH_TOKEN=self.token, SKYWATCH_ADMIN_TOKEN=self.admin_tok, SKYWATCH_DATA=self.dir)
        f = open(os.path.join(self.dir, "hub.log"), "a")
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "hub.py"), "--host", "127.0.0.1",
                                      "--port", str(self.port), "--admin-port", str(self.port + 1)],
                                     env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(50):
            try: urllib.request.urlopen(self.url + "/api/ping", timeout=1); return
            except OSError: time.sleep(0.2)
        raise RuntimeError("hub nie wstał")

    def stop_hub(self):
        if self.proc: os.killpg(self.proc.pid, signal.SIGKILL); self.proc.wait(); self.proc = None

    def say(self, msg):
        line = f"{time.time() - self.t0:7.1f}s {msg}"
        self.log.append(line); print(f"[{self.name}] {line}", flush=True)

    # --- telefony ---
    def add_phones(self, stations, trust="new", mode="sensor", seed_keys=True, tag="P"):
        """Telefony z kluczem wpisanym do keys.jsonl przed startem huba (bez /api/join → bez limitu napływu)."""
        out = []
        for st in stations:
            i = len(self.phones) + 1
            k = ec.generate_private_key(ec.SECP256R1())
            d = {"i": i, "key": k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()).decode(),
                 "ip": f"10.{80 + i // 250}.{i % 250}.{self.rnd.randint(2, 250)}", "station": cell(*st), "mode": mode}
            p = Phone(d, self.url, self.token); p.trust, p.tag = trust, f"{tag}{i:03d}"
            if seed_keys:
                with open(os.path.join(self.dir, "keys.jsonl"), "a") as f:
                    f.write(json.dumps({"node": p.node, "pub": base64.b64encode(p.der).decode(), "trust": trust,
                                        "t": int(time.time() * 1000), "hw": "software"}) + "\n")
            self.phones.append(p); out.append(p)
        return out

    def send_stations(self, phones=None):
        for p in phones or self.phones:
            code, out = p.event("station", {"lat": p.d["station"][0], "lon": p.d["station"][1], "acc": 300})
            if code != 200: self.say(f"stacja {p.tag}: {code} {out}")

    def admin(self, path, obj):
        req = urllib.request.Request(self.aurl + path, data=json.dumps(obj).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.admin_tok})
        with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())

    # --- odbiorca alarmów (jak telefon 0.13: wspólny token, odpytywanie co 3 s) ---
    def listen(self, stop):
        last = -1
        while not stop.is_set():
            try:
                req = urllib.request.Request(f"{self.url}/api/alerts?since={last}", headers={"Authorization": f"Bearer {self.token}"})
                t = time.time()
                with urllib.request.urlopen(req, timeout=10) as r: js = json.loads(r.read())
                self.lat.append(time.time() - t)
                for al in js["alerts"]:
                    if al["id"] not in self.alerts_seen:
                        self.alerts_seen[al["id"]] = dict(al, rx=time.time() - self.t0, delay=round(time.time() - al["t"] / 1000, 1))
                        kind = "ODWOŁANIE" if al["cancel"] else ("ciche Podejrzenie" if al.get("quiet") else f"POZIOM {al['level']}")
                        self.say(f">>> ALARM #{al['id']} {kind}: {al['text']}")
                last = js["last"]
            except OSError as e:
                self.say(f"odbiorca: {e}")
            stop.wait(3)

    def result(self, extra=None):
        al = sorted(self.alerts_seen.values(), key=lambda x: x["id"])
        lat = sorted(self.lat)
        res = {"scenario": self.name, "log": self.log, "alerts": al,
               "poll_ms": {"p50": round(1000 * lat[len(lat) // 2]) if lat else None,
                           "p95": round(1000 * lat[int(len(lat) * .95)]) if lat else None, "n": len(lat)}, **(extra or {})}
        os.makedirs(OUT, exist_ok=True)
        json.dump(res, open(os.path.join(OUT, f"{self.name}.json"), "w"), ensure_ascii=False, indent=1)
        shutil.copy(os.path.join(self.dir, "hub.log"), os.path.join(OUT, f"{self.name}.hub.log"))
        return res


def detect(p, f0, rng_m, floor=None):
    floor = p.floor if floor is None else floor
    peak = floor + max(6, 30 - 20 * math.log10(max(rng_m, 100) / 100))
    return p.event("detection", {"method": "harmonic", "peak_db": round(peak, 1), "floor_db": round(floor, 1), "source": "UNPROCESSED",
                                 "harm": {"windows": 6, "tonal_frac": 0.8, "f0_med": round(f0, 1), "stable_run_s": 6,
                                          "presence_8s": 7, "shahed_like": True}})


def fly(run, drones, sources=(), hear=2.5, every=15, manual=None, coordinator=False, hooks=None, offline=None, tail=40):
    """drones: [{route, speed m/s, alt, f0, start s}]; sources: [{pos, f0, start, end, hear}] — źródło w miejscu
    (kosiarka, agregat). manual: {phone: kind} — zgłoszenia ręczne przy <1,2 km. coordinator: poziom 2 po 2 ręcznych,
    odwołanie na końcu. hooks: {s: fn}. offline: {phone: (od, do)} — w tym czasie zdarzenia czekają w kolejce."""
    sensors = [p for p in run.phones if p.d["mode"] == "sensor"]
    last_det, reported, lvl2, nman, queue = {}, set(), False, 0, []
    hooks = dict(hooks or {}); offline = offline or {}
    first_heard, heard_by = {}, {}
    t0 = time.time()
    ends = [d["start"] + route_len(d["route"]) * 1000 / d["speed"] for d in drones] + [s["end"] for s in sources]
    total = max(ends)
    run.say(f"start: {len(drones)} dron(y), {len(sources)} źródeł w miejscu, {len(sensors)} czujników, ~{total / 60:.1f} min")
    while True:
        el = time.time() - t0
        for k in [k for k in hooks if k <= el]: hooks.pop(k)(run)
        if el > total: break
        emitters = []
        for di, d in enumerate(drones):
            if el < d["start"]: continue
            pos = along(d["route"], (el - d["start"]) * d["speed"] / 1000)
            if pos is None: continue
            (lat, lon), hdg = pos
            emitters.append((f"dron{di + 1}", (lat, lon), d["alt"], d["f0"], d["speed"], hdg, d.get("hear", hear)))
        for si, s in enumerate(sources):
            if s["start"] <= el <= s["end"]:
                emitters.append((f"źródło{si + 1}", s["pos"], 2, s["f0"], 0, 0, s.get("hear", 1.5)))
        for name, (lat, lon), alt, f0, spd, hdg, hr in emitters:
            vx, vy = spd * math.sin(math.radians(hdg)), spd * math.cos(math.radians(hdg))
            for p in sensors:
                dx, dy = km_xy(tuple(p.d["station"]), (lat, lon)); d = math.hypot(dx, dy)
                key = (p.node, name)
                if d > hr or time.time() - last_det.get(key, 0) < every + random.uniform(-3, 3): continue
                last_det[key] = time.time()
                first_heard.setdefault(name, el); heard_by.setdefault(name, set()).add(p.tag)
                rng_m = math.hypot(d * 1000, alt)
                f = f0 * 343 / (343 + (vx * dx + vy * dy) * 1000 / max(rng_m, 1))
                off = offline.get(p.tag)
                if off and off[0] <= el < off[1]:
                    queue.append((p, int(time.time() * 1000), f, rng_m)); continue
                code, out = detect(p, f, rng_m)
                run.say(f"  {p.tag}{' [Z]' if p.trust == 'trusted' else ''} słyszy {name}: {d:.1f} km, {f:.0f} Hz → {code}"
                        + (f" {out}" if code != 200 else ""))
                if manual and p in manual and p not in reported and d < 1.2 and name.startswith("dron"):
                    reported.add(p)
                    az = math.degrees(math.atan2(dx, dy)) % 360
                    code, out = p.event("manual", {"kind": manual[p], "az": round(az), "el": round(math.degrees(math.atan2(alt, d * 1000))), "compass_acc": 2})
                    run.say(f"  {p.tag} zgłasza ręcznie {manual[p]} → {code}"); nman += 1
        # telefony wracające z trybu offline wysyłają zaległe wykrycia (z pierwotnym czasem t)
        for item in [q for q in queue if el >= offline[q[0].tag][1]]:
            queue.remove(item); p, t_ms, f, rng_m = item
            body = {"method": "harmonic", "peak_db": -10.0, "floor_db": -40.0, "source": "UNPROCESSED",
                    "harm": {"windows": 6, "tonal_frac": 0.8, "f0_med": round(f, 1), "stable_run_s": 6, "presence_8s": 7, "shahed_like": True}}
            code, out = p.send("POST", "/api/event", {"type": "detection", "node": p.node, "t": t_ms, **body})
            run.say(f"  {p.tag} wysyła zaległe wykrycie sprzed {time.time() - t_ms / 1000:.0f} s → {code}")
        if coordinator and nman >= 2 and not lvl2:
            lvl2 = True
            al = run.admin("/admin/api/alert", {"level": 2, "text": "TEST: dron potwierdzony przez obserwatorów. Schowaj się."})
            run.say(f"  KOORDYNATOR: poziom 2 (#{al['id']})")
        time.sleep(1)
    run.say("koniec przelotów")
    time.sleep(tail)
    if coordinator:
        al = run.admin("/admin/api/alert", {"cancel": True, "text": "TEST zakończony"}); run.say(f"KOORDYNATOR: odwołanie (#{al['id']})")
        time.sleep(5)
    return {"first_heard_s": {k: round(v) for k, v in first_heard.items()}, "heard_by": {k: sorted(v) for k, v in heard_by.items()}}


def city_stations(n, rnd, radius=10):
    out = []
    for _ in range(n):
        r, th = radius * math.sqrt(rnd.random()), rnd.uniform(0, 2 * math.pi)
        out.append((CENTER[0] + r * math.cos(th) / 111.32, CENTER[1] + r * math.sin(th) / (111.32 * math.cos(math.radians(CENTER[0])))))
    return out


def today_stations():
    """Stacje 43 czujników z próby 1.10 (te same miejsca, nowe klucze)."""
    st = json.load(open(os.path.join(HERE, "data", "sim_phones.json")))
    return [(tuple(d["station"]), d["i"]) for d in st if d["mode"] == "sensor"]


def drone(route, kmh=180, alt=150, f0=110, start=0, **kw):
    return dict(route=[tuple(map(float, p.split(","))) for p in route], speed=kmh / 3.6, alt=alt, f0=f0, start=start, **kw)


# ---------------- scenariusze ----------------

def sc_regresja(run):
    """Powtórka próby 1.10 po poprawkach: te same stacje, SIM-36 i SIM-47 zaufane, koordynator daje poziom 2."""
    ts = today_stations()
    for st, i in ts:
        run.add_phones([st], trust="trusted" if i in (36, 47) else "new", tag=f"SIM{i:02d}-")
    run.start_hub() if not run.proc else None
    run.send_stations()
    d = drone(ROUTE_EW)
    rt = d["route"]; tot = route_len(rt)
    dist = lambda p: min(math.hypot(*km_xy(tuple(p.d["station"]), along(rt, s)[0])) for s in [i * 0.2 for i in range(int(tot / 0.2))])
    near = sorted(run.phones, key=dist)[:3]
    return fly(run, [d], manual=dict(zip(near, ("see", "hear", "hear"))), coordinator=True)


def sc_bez_koordynatora(run):
    """Ta sama trasa, nikt nic nie zgłasza ręcznie, koordynator śpi — czy łańcuch/fuzja same utrzymają ślad."""
    for st, i in today_stations():
        run.add_phones([st], trust="trusted" if i in (36, 47) else "new", tag=f"SIM{i:02d}-")
    run.send_stations()
    return fly(run, [drone(ROUTE_EW)], tail=20)


def sc_rzadka(run, trusted=0, n=10, seed=7):
    rnd = random.Random(seed)
    st = city_stations(n, rnd)
    ps = run.add_phones(st, trust="new")
    # zaufany: ten najbliżej środka trasy, żeby miał szansę coś usłyszeć
    for p in sorted(ps, key=lambda p: math.hypot(*km_xy(tuple(p.d["station"]), CENTER)))[:trusted]:
        p.trust = "trusted"
    if trusted:   # nadpisz status w keys.jsonl (ostatni wpis wygrywa) — hub jeszcze nie czytał
        with open(os.path.join(run.dir, "keys.jsonl"), "a") as f:
            for p in ps:
                if p.trust == "trusted": f.write(json.dumps({"node": p.node, "trust": "trusted", "t": int(time.time() * 1000)}) + "\n")
    return st


def sc_rzadka_nowe(run):
    """10 czujników na całe miasto, wszystkie nowe."""
    sc_rzadka(run, 0)
    run.start_hub(); run.send_stations()
    return fly(run, [drone(ROUTE_EW)], tail=20)


def sc_rzadka_zaufany(run):
    """10 czujników na całe miasto, jeden zaufany blisko środka."""
    sc_rzadka(run, 1)
    run.start_hub(); run.send_stations()
    return fly(run, [drone(ROUTE_EW)], tail=20)


def sc_dwa_drony(run):
    """Dwa drony naraz: wschód→zachód i z płd.-zach. na płn.-wsch., przecinają się nad centrum. 60 czujników, 4 zaufane."""
    rnd = random.Random(3)
    ps = run.add_phones(city_stations(60, rnd), trust="new")
    with open(os.path.join(run.dir, "keys.jsonl"), "a") as f:
        for p in ps[::15]:
            p.trust = "trusted"; f.write(json.dumps({"node": p.node, "trust": "trusted", "t": int(time.time() * 1000)}) + "\n")
    run.start_hub(); run.send_stations()
    return fly(run, [drone(ROUTE_EW), drone(ROUTE_SW, f0=118, start=20)], tail=20)


def sc_wolny_nisko(run):
    """Wolniejszy dron (120 km/h) nisko (50 m, słychać dalej: 3 km); 43 czujniki z próby, bez zaufanych."""
    for st, i in today_stations(): run.add_phones([st], trust="new", tag=f"SIM{i:02d}-")
    run.send_stations()
    return fly(run, [drone(ROUTE_NS, kmh=120, alt=50, hear=3.0)], tail=20)


def sc_kosiarka(run):
    """Fałszywka: źródło w miejscu (kosiarka/agregat z tonem ~110 Hz, klasyfikator dał się nabrać) przez 5 min.
    Wariant A: 6 nowych telefonów wokół; wariant B (od 6. min): obok stoi jeden zaufany."""
    rnd = random.Random(11)
    a = (52.2600, 20.9300); b = (52.2000, 21.1000)
    near = lambda c, n, r: [(c[0] + rnd.uniform(-r, r) / 111.32, c[1] + rnd.uniform(-r, r) / 68.2) for _ in range(n)]
    run.add_phones(near(a, 6, 1.0), trust="new", tag="A")
    run.add_phones(near(b, 3, 0.8), trust="new", tag="B")
    run.add_phones(near(b, 1, 0.3), trust="trusted", tag="BZ")
    run.start_hub(); run.send_stations()
    return fly(run, [], sources=[dict(pos=a, f0=110, start=0, end=300, hear=1.5),
                                 dict(pos=b, f0=105, start=360, end=600, hear=1.2)], tail=20)


def sc_awarie(run):
    """Trasa z próby; w połowie przelotu nad gęstym skupiskiem hub pada na 40 s; 4 telefony tracą sieć na 90 s
    i potem wysyłają zaległe wykrycia; koordynator daje poziom 2."""
    ts = today_stations()
    for st, i in ts: run.add_phones([st], trust="trusted" if i in (36, 47) else "new", tag=f"SIM{i:02d}-")
    run.send_stations()
    d = drone(ROUTE_EW); rt = d["route"]; tot = route_len(rt)
    dist = lambda p: min(math.hypot(*km_xy(tuple(p.d["station"]), along(rt, s)[0])) for s in [i * 0.2 for i in range(int(tot / 0.2))])
    srt = sorted(run.phones, key=dist)
    def down(r): r.say("!!! hub pada"); r.stop_hub()
    def up(r): r.say("!!! hub wstaje"); r.start_hub()
    off = {p.tag: (100, 190) for p in srt[3:7]}
    return fly(run, [d], manual=dict(zip(srt[:3], ("see", "hear", "hear"))), coordinator=True,
               hooks={135: down, 175: up}, offline=off)


def sc_ataki(run):
    """Bez drona: (1) 35 nowych kluczy dołącza w kilka minut → automatyczne wstrzymanie napływu;
    (2) wszystkie naraz „słyszą drona” w jednym rejonie → przy wstrzymaniu nic; (3) przed wstrzymaniem —
    6 nowych z różnych sieci w jednym rejonie → najwyżej ciche Podejrzenie; (4) powtórzony podpis, cudze zdarzenie,
    zły podpis, limit zgłoszeń ręcznych."""
    rnd = random.Random(5)
    c = (52.2400, 21.0000)
    st = [(c[0] + rnd.uniform(-1.5, 1.5) / 111.32, c[1] + rnd.uniform(-1.5, 1.5) / 68.2) for _ in range(35)]
    ps = run.add_phones(st, trust="new", seed_keys=False, tag="X")
    run.start_hub()
    res = {}
    # (3) pierwsze 6 dołącza i od razu „słyszy” naraz
    for p in ps[:6]:
        code, out = p.join(); run.say(f"{p.tag} dołącza → {code} {out.get('trust', out)}")
    run.send_stations(ps[:6])
    for p in ps[:6]:
        code, _ = detect(p, 110, 800); run.say(f"{p.tag} fałszywe wykrycie → {code}")
    time.sleep(8)
    res["po_6_falszywych"] = [a["text"] for a in run.alerts_seen.values()]
    run.admin("/admin/api/alert", {"cancel": True, "text": "TEST: odwołanie"}); time.sleep(4)
    # (1) reszta dołącza szybko → próg napływu 30/10 min
    for p in ps[6:]:
        code, out = p.join(); run.say(f"{p.tag} dołącza → {code} {out.get('trust', out)}")
    state = json.loads(urllib.request.urlopen(urllib.request.Request(run.aurl + "/admin/api/state?min=5",
                       headers={"Authorization": "Bearer " + run.admin_tok}), timeout=10).read())["keys"]
    run.say(f"stan kluczy: {state}"); res["keys_po_napływie"] = state
    run.send_stations(ps[6:])
    for p in ps[6:]: detect(p, 110, 800)
    time.sleep(8)
    run.say(f"alarmy po 29 fałszywych przy wstrzymaniu: {len(run.alerts_seen)}")
    # (4) nadużycia pojedynczego klucza
    p, q = ps[0], ps[1]
    import hashlib
    body = json.dumps({"type": "detection", "node": p.node, "t": int(time.time() * 1000), "method": "harmonic"}).encode()
    t, nonce = str(int(time.time() * 1000)), secrets.token_hex(12)
    from cryptography.hazmat.primitives import hashes
    sig = base64.b64encode(p.key.sign(f"POST\n/api/event\n{t}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode(),
                                      ec.ECDSA(hashes.SHA256()))).decode()
    hdr = {"Content-Type": "application/json", "X-Skw-Node": p.node, "X-Skw-T": t, "X-Skw-Nonce": nonce, "X-Skw-Sig": sig}
    def post(h, b):
        try:
            with urllib.request.urlopen(urllib.request.Request(run.url + "/api/event", data=b, headers=h, method="POST"), timeout=5) as r: return r.status
        except urllib.error.HTTPError as e: return f"{e.code} {e.read().decode()[:60]}"
    res["oryginał"] = post(hdr, body)
    res["powtórka_podpisu"] = post(hdr, body)
    res["zmieniona_treść"] = post(dict(hdr, **{"X-Skw-Nonce": secrets.token_hex(12)}), body.replace(b"harmonic", b"audio_level"))
    code, out = q.send("POST", "/api/event", {"type": "detection", "node": p.node, "t": int(time.time() * 1000), "method": "harmonic"})
    res["cudze_zdarzenie"] = f"{code} {out}"
    codes = [p.event("manual", {"kind": "see", "az": 0, "el": 5})[0] for _ in range(8)]
    res["limit_ręcznych_8_prób"] = codes
    old = int((time.time() - 900) * 1000)
    res["stary_zegar_15min"] = str(p.send("POST", "/api/event", {"type": "detection", "node": p.node, "t": old, "method": "harmonic"}))
    for k, v in res.items(): run.say(f"{k}: {v}")
    return res


def sc_obciazenie(run):
    """200 telefonów (puls co 60 s, jak aplikacja) + przelot; mierzymy czas odpowiedzi huba i CPU."""
    rnd = random.Random(9)
    run.add_phones(city_stations(200, rnd), trust="new")
    with open(os.path.join(run.dir, "keys.jsonl"), "a") as f:
        for p in run.phones[::25]:
            p.trust = "trusted"; f.write(json.dumps({"node": p.node, "trust": "trusted", "t": int(time.time() * 1000)}) + "\n")
    run.start_hub(); run.send_stations()
    stop = threading.Event(); times = []
    def hb_loop():
        nxt = {p.node: time.time() + rnd.uniform(0, 60) for p in run.phones}
        while not stop.is_set():
            for p in run.phones:
                if time.time() >= nxt[p.node]:
                    t = time.time(); code, _ = p.heartbeat(); times.append(time.time() - t)
                    nxt[p.node] = time.time() + 60
            stop.wait(0.2)
    th = threading.Thread(target=hb_loop, daemon=True); th.start()
    cpu0 = open(f"/proc/{run.proc.pid}/stat").read().split()
    out = fly(run, [drone(ROUTE_NS)], tail=20)
    cpu1 = open(f"/proc/{run.proc.pid}/stat").read().split()
    stop.set(); th.join(5)
    tick = os.sysconf("SC_CLK_TCK")
    used = (int(cpu1[13]) + int(cpu1[14]) - int(cpu0[13]) - int(cpu0[14])) / tick
    rss = int(open(f"/proc/{run.proc.pid}/statm").read().split()[1]) * 4096 // 2**20
    times.sort()
    out.update(heartbeats=len(times), hb_ms_p50=round(1000 * times[len(times) // 2]), hb_ms_p95=round(1000 * times[int(len(times) * .95)]),
               hb_ms_max=round(1000 * times[-1]), hub_cpu_s=round(used, 1), hub_rss_mb=rss)
    run.say(f"obciążenie: {out}")
    return out


SCEN = {"regresja": sc_regresja, "bez-koordynatora": sc_bez_koordynatora, "rzadka-nowe": sc_rzadka_nowe,
        "rzadka-zaufany": sc_rzadka_zaufany, "dwa-drony": sc_dwa_drony, "wolny-nisko": sc_wolny_nisko,
        "kosiarka": sc_kosiarka, "awarie": sc_awarie, "ataki": sc_ataki, "obciazenie": sc_obciazenie}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--port", type=int, default=18700)
    a = ap.parse_args()
    if a.list or not a.names:
        for k, f in SCEN.items(): print(f"{k:18} {f.__doc__.strip().splitlines()[0]}")
        return
    ths = []
    for k, name in enumerate(a.names):
        def go(name=name, port=a.port + 10 * k):
            run = Run(name, port)
            def body(r):
                # scenariusze, które dodają telefony przed startem huba, startują go same; reszta tutaj
                return SCEN[name](r)
            orig = run.start_hub
            started = {"v": False}
            def start_once():
                if not started["v"] or run.proc is None: started["v"] = True; orig()
            run.start_hub = start_once
            def wrapped(r):
                # telefony muszą być w keys.jsonl przed startem — start leniwy przy pierwszym send_stations
                ss = r.send_stations
                def send(ph=None):
                    if r.proc is None: start_once()
                    ss(ph)
                r.send_stations = send
                return body(r)
            stop = threading.Event()
            try:
                extra = None
                th = None
                def listener_when_up():
                    while run.proc is None and not stop.is_set(): time.sleep(0.2)
                    run.listen(stop)
                th = threading.Thread(target=listener_when_up, daemon=True); th.start()
                extra = wrapped(run)
            except Exception as e:
                run.say(f"BŁĄD: {e!r}"); extra = {"error": repr(e)}
            finally:
                time.sleep(4); stop.set()
                res = run.result(extra); run.stop_hub()
            print(f"[{name}] gotowe: {len(res['alerts'])} alarmów", flush=True)
        t = threading.Thread(target=go); t.start(); ths.append(t)
    for t in ths: t.join()


if __name__ == "__main__":
    main()

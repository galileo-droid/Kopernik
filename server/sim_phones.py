#!/usr/bin/env python3
"""
SkywatchPL — testowe nadajniki: N wirtualnych telefonów 0.14 łączących się z hubem jak prawdziwa aplikacja.

Każdy ma własny klucz ECDSA P-256 (programowy, key_hw=software), dołącza przez /api/join, podpisuje żądania,
wysyła stację (kratka 300 m), puls co minutę i od czasu do czasu wykrycie samej głośności (audio_level).
Wykryć tonu (harmonic) NIE wysyła — audio_level nie wchodzi do fuzji, więc symulator nie podnosi alarmów
na prawdziwych telefonach. Każdy telefon ma udawany adres z innej sieci /24 (X-Forwarded-For z localhost),
a dołączenia są rozłożone w czasie (co JOIN_GAP s), żeby nie wyzwolić automatycznego wstrzymania napływu.

  python3 sim_phones.py --n 50                 # start (klucze w data/sim_phones.json, przy restarcie te same)
  python3 sim_phones.py --block                # zablokuj wszystkie testowe punkty w hubie (panel, token admina)
Modele mają nazwy SIM-01…SIM-NN — tak je poznasz w panelu.
"""
import argparse, base64, hashlib, heapq, itertools, json, math, os, random, secrets, sys, time, urllib.request, urllib.error

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "sim_phones.json")
CENTER = (52.2297, 21.0122)     # Warszawa, środek
RADIUS_KM = 10
GRID = (0.0027, 0.0044)
JOIN_GAP = 25                   # s między dołączeniami: ≤24 w 10 min, próg napływu to 30
HB_EVERY = 60
DET_EVERY = (600, 1800)         # s: losowy odstęp między wykryciami samej głośności


def cell(lat, lon):
    return [round((math.floor(v / s) + 0.5) * s, 5) for v, s in ((lat, GRID[0]), (lon, GRID[1]))]


def load_state(n):
    st = json.load(open(STATE)) if os.path.exists(STATE) else []
    rnd = random.Random()
    while len(st) < n:
        i = len(st)
        k = ec.generate_private_key(ec.SECP256R1())
        r, th = RADIUS_KM * math.sqrt(rnd.random()), rnd.uniform(0, 2 * math.pi)
        lat = CENTER[0] + r * math.cos(th) / 111.32
        lon = CENTER[1] + r * math.sin(th) / (111.32 * math.cos(math.radians(CENTER[0])))
        st.append({"i": i + 1, "key": k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()).decode(),
                   "ip": f"10.77.{i + 1}.{rnd.randint(2, 250)}", "station": cell(lat, lon),
                   "mode": "receiver" if rnd.random() < 0.2 else "sensor", "joined": False})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    save_state(st)
    return st[:n]


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


class Phone:
    def __init__(self, d, url, token):
        self.d, self.url, self.token = d, url, token
        self.key = serialization.load_pem_private_key(d["key"].encode(), None)
        self.der = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.node = hashlib.sha256(self.der).hexdigest()[:32]
        self.floor = random.uniform(-45, -30)
        self.batt = random.choice(["high", "high", "mid", "low"])

    def send(self, method, path, obj):
        body = json.dumps(obj).encode()
        t, nonce = str(int(time.time() * 1000)), secrets.token_hex(12)
        msg = f"{method}\n{path}\n{t}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
        sig = base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        req = urllib.request.Request(self.url + path, data=body, method=method, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.token}",
            "X-Skw-Node": self.node, "X-Skw-T": t, "X-Skw-Nonce": nonce, "X-Skw-Sig": sig,
            "X-Forwarded-For": self.d["ip"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode(errors="replace")[:200]}
        except OSError as e:
            return 0, {"error": str(e)}

    def event(self, typ, fields):
        return self.send("POST", "/api/event", {"type": typ, "node": self.node, "t": int(time.time() * 1000), **fields})

    def join(self):
        return self.send("POST", "/api/join", {"pub": base64.b64encode(self.der).decode(), "hw": "software"})

    def heartbeat(self):
        return self.event("heartbeat", {
            "mode": self.d["mode"], "flavor": "user", "battery_bin": self.batt, "charging": random.random() < 0.3,
            "floor_db": round(self.floor + random.gauss(0, 1.5), 2), "source": "UNPROCESSED", "model": f"SIM-{self.d['i']:02d}",
            "sdk": 34, "app": "0.14", "key_hw": "software",
            "loc_diag": {"fine": True, "coarse": True, "enabled": True, "bg": True,
                         "providers": {"fused": "ok 20m", "gps": "ok 12m", "network": "ok 60m"}}})

    def detection(self):
        peak = self.floor + random.uniform(8, 16)
        return self.event("detection", {"method": "audio_level", "peak_db": round(peak, 2), "floor_db": round(self.floor, 2),
                                        "source": "UNPROCESSED", "harm": {"windows": 6, "tonal_frac": 0, "f0_med": 0,
                                        "stable_run_s": 0, "presence_8s": 0, "shahed_like": False}})


def run(a):
    token = os.environ.get("SKYWATCH_TOKEN", "")
    st = load_state(a.n)
    phones = [Phone(d, a.url, token) for d in st]
    q, now, k, seq = [], time.time(), 0, itertools.count()
    for p in phones:   # (czas, kolejność, telefon, akcja)
        if p.d["joined"]: heapq.heappush(q, (now + random.uniform(0, HB_EVERY), next(seq), p, "station"))
        else: heapq.heappush(q, (now + k * JOIN_GAP, next(seq), p, "join")); k += 1
    print(f"{time.strftime('%H:%M:%S')} start: {len(phones)} telefonów, {k} do dołączenia (ostatni za ~{k * JOIN_GAP // 60} min)", flush=True)
    while q:
        t, _, p, act = heapq.heappop(q)
        time.sleep(max(0, t - time.time()))
        name = f"SIM-{p.d['i']:02d} {p.node[:6]}"
        if act == "join":
            code, out = p.join()
            if code != 200:
                print(f"{time.strftime('%H:%M:%S')} {name}: dołączenie {code} {out}", flush=True)
                heapq.heappush(q, (time.time() + 120, next(seq), p, "join")); continue
            p.d["joined"] = True; save_state(st)
            print(f"{time.strftime('%H:%M:%S')} {name}: dołączył ({out.get('trust')}, kod {out.get('code')})", flush=True)
            act = "station"
        if act == "station":
            code, out = p.event("station", {"lat": p.d["station"][0], "lon": p.d["station"][1], "acc": 300})
            heapq.heappush(q, (time.time() + 1, next(seq), p, "hb"))
            if p.d["mode"] == "sensor": heapq.heappush(q, (time.time() + random.uniform(*DET_EVERY), next(seq), p, "det"))
            continue
        code, out = p.heartbeat() if act == "hb" else p.detection()
        if code == 401 and "dołącz" in str(out):
            p.d["joined"] = False; heapq.heappush(q, (time.time() + 5, next(seq), p, "join")); continue
        if code != 200: print(f"{time.strftime('%H:%M:%S')} {name}: {act} {code} {out}", flush=True)
        if act == "hb": heapq.heappush(q, (time.time() + HB_EVERY + random.uniform(-2, 2), next(seq), p, "hb"))
        else: heapq.heappush(q, (time.time() + random.uniform(*DET_EVERY), next(seq), p, "det"))


def block(a):
    tok = os.environ.get("SKYWATCH_ADMIN_TOKEN", "")
    st = json.load(open(STATE)) if os.path.exists(STATE) else []
    for d in st:
        p = Phone(d, a.url, "")
        req = urllib.request.Request(a.admin_url + "/admin/api/trust", method="POST",
                                     data=json.dumps({"code": p.node[:8], "trust": "blocked"}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r: print(f"SIM-{d['i']:02d} {p.node[:6]}: zablokowany")
        except urllib.error.HTTPError as e: print(f"SIM-{d['i']:02d} {p.node[:6]}: {e.code} {e.read()[:100]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--admin-url", default="http://127.0.0.1:8766")
    ap.add_argument("--block", action="store_true")
    a = ap.parse_args()
    block(a) if a.block else run(a)

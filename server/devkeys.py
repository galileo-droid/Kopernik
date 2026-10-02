"""
SkywatchPL — klucze urządzeń i poziomy zaufania (od 0.14).

Telefon przy pierwszym uruchomieniu tworzy parę kluczy ECDSA P-256 w Android Keystore
(StrongBox, a gdy go nie ma — TEE) i dołącza przez /api/join bez żadnej rejestracji.
Identyfikator punktu = pierwsze 32 znaki hex SHA-256 klucza publicznego (DER), kod zaufania = pierwsze 8.
Każde żądanie jest podpisane: METODA \\n ścieżka?zapytanie \\n t \\n nonce \\n sha256(treść).
Serwer odrzuca podpisy starsze/nowsze niż SKEW i powtórzone nonce.

Poziomy: "trusted" (kod 1:1 albo awans w panelu, a także telefony testowe przeniesione z 0.13),
"new" (dołączył sam), "blocked" (unieważniony w panelu), "legacy" (stara aplikacja na wspólnym tokenie).
Stan w data/keys.jsonl (dziennik zmian, ostatni wpis wygrywa). Adresów IP nie zapisujemy.
"""
import base64, collections, hashlib, ipaddress, json, os, threading, time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

SKEW = 300              # s: dopuszczalna różnica zegara telefonu i serwera
JOIN_PER_NET_H = 20     # nowych kluczy na godzinę z jednej sieci (/24, /56) — luźno, CGNAT
INFLUX = (30, 600)      # tyle nowych kluczy w tylu sekundach → automatyczne wstrzymanie wpływu nowych

lock = threading.Lock()
keys = {}               # node → {"pub": DER, "trust": str, "t": ms, "hw": str}
pubs = {}               # node → obiekt klucza (pamięć podręczna)
nonces = {}             # nonce → czas
joins_net = collections.defaultdict(collections.deque)   # sieć → czasy dołączeń (tylko w pamięci)
joins_all = collections.deque()                          # czasy wszystkich dołączeń (licznik napływu)
paused = {"on": False, "auto": False, "t": 0}
DATA = None


def init(data_dir):
    global DATA
    DATA = data_dir
    p = os.path.join(DATA, "keys.jsonl")
    if not os.path.exists(p): return
    for line in open(p):
        try: r = json.loads(line)
        except ValueError: continue
        if r.get("pause") is not None: paused.update(on=r["pause"], auto=r.get("auto", False), t=r.get("t", 0)); continue
        n = r.get("node")
        if not n: continue
        k = keys.setdefault(n, {})
        if r.get("pub"): k["pub"] = base64.b64decode(r["pub"])
        for f in ("trust", "t", "hw", "prev"):
            if r.get(f) is not None: k[f] = r[f]


def _log(rec):
    with open(os.path.join(DATA, "keys.jsonl"), "a") as f: f.write(json.dumps(rec) + "\n")


def node_of(der): return hashlib.sha256(der).hexdigest()[:32]
def code_of(node): return f"{node[:4]}-{node[4:8]}".upper()


def net_of(ip):
    """Sieć do reguły „różnych sieci”: /24 dla IPv4, /56 dla IPv6."""
    try: a = ipaddress.ip_address(ip)
    except ValueError: return "?"
    if a.version == 6 and a.ipv4_mapped: a = a.ipv4_mapped
    return str(ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 56}", strict=False))


def _pub(node):
    p = pubs.get(node)
    if p is None and node in keys and keys[node].get("pub"):
        p = pubs[node] = load_der_public_key(keys[node]["pub"])
    return p


def _verify(pub, sig_b64, msg):
    try:
        pub.verify(base64.b64decode(sig_b64), msg, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def signed_msg(method, path, t, nonce, body):
    return f"{method}\n{path}\n{t}\n{nonce}\n{hashlib.sha256(body or b'').hexdigest()}".encode()


def _fresh(t, nonce):
    """Czas w oknie i nonce niewidziane. Zwraca komunikat błędu albo None."""
    now = time.time()
    try: t = int(t)
    except (TypeError, ValueError): return "zły czas podpisu"
    if abs(now - t / 1000) > SKEW: return "podpis spoza okna czasu (zegar telefonu?)"
    if not nonce or len(nonce) > 40: return "zły nonce"
    with lock:
        if len(nonces) > 50000:
            for k in [k for k, v in nonces.items() if now - v > 2 * SKEW]: del nonces[k]
        if nonce in nonces: return "powtórzony podpis"
        nonces[nonce] = now
    return None


def check(headers, method, path, body):
    """Podpisane żądanie → (node, trust, None); bez podpisu → (None, None, None); zły podpis → (None, None, błąd)."""
    node, sig = headers.get("X-Skw-Node"), headers.get("X-Skw-Sig")
    if not node and not sig: return None, None, None
    with lock: k = keys.get(node or "")
    pub = _pub(node) if k else None
    if not pub: return None, None, "nieznany klucz — dołącz ponownie"
    if not _verify(pub, sig or "", signed_msg(method, path, headers.get("X-Skw-T"), headers.get("X-Skw-Nonce"), body)):
        return None, None, "zły podpis"
    err = _fresh(headers.get("X-Skw-T"), headers.get("X-Skw-Nonce"))
    if err: return None, None, err
    if k.get("trust") == "blocked": return None, None, "klucz zablokowany"
    return node, k.get("trust", "new"), None


def join(headers, path, body, ip, legacy_nodes, token_ok):
    """Dołączenie bez rejestracji. Treść: {"pub": base64 DER, "hw": "strongbox|tee", "prev": stary UUID (z 0.13)}.
    Żądanie podpisane nowym kluczem (dowód posiadania). Zwraca (kod HTTP, odpowiedź, zdarzenie dla operatora)."""
    try:
        r = json.loads(body)
        der = base64.b64decode(r["pub"])
        pub = load_der_public_key(der)
        if not isinstance(pub, ec.EllipticCurvePublicKey) or pub.curve.name != "secp256r1": raise ValueError
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "zły klucz"}, None
    node = node_of(der)
    if headers.get("X-Skw-Node") != node or not _verify(pub, headers.get("X-Skw-Sig") or "",
            signed_msg("POST", path, headers.get("X-Skw-T"), headers.get("X-Skw-Nonce"), body)):
        return 401, {"error": "zły podpis"}, None
    err = _fresh(headers.get("X-Skw-T"), headers.get("X-Skw-Nonce"))
    if err: return 401, {"error": err}, None
    with lock: k = keys.get(node)
    if k:   # ponowne dołączenie tym samym kluczem (np. po wyczyszczeniu danych serwera po stronie telefonu)
        if k.get("trust") == "blocked": return 403, {"error": "klucz zablokowany"}, None
        return 200, {"node": node, "code": code_of(node), "trust": k.get("trust", "new")}, None
    now, net = time.time(), net_of(ip)
    with lock:
        q = joins_net[net]
        while q and now - q[0] > 3600: q.popleft()
        if len(q) >= JOIN_PER_NET_H: return 429, {"error": "za dużo nowych urządzeń z tej sieci, spróbuj za godzinę"}, None
        q.append(now)
        joins_all.append(now)
        while joins_all and now - joins_all[0] > INFLUX[1]: joins_all.popleft()
        influx = len(joins_all)
    # przeniesienie telefonu testowego z 0.13: znany stary identyfikator + wspólny token → zaufany
    prev = r.get("prev") if isinstance(r.get("prev"), str) else None
    migrated = bool(prev and token_ok and prev in legacy_nodes and not any(v.get("prev") == prev for v in keys.values()))
    hw = r.get("hw") if r.get("hw") in ("strongbox", "tee", "software") else "?"
    rec = {"node": node, "pub": base64.b64encode(der).decode(), "trust": "trusted" if migrated else "new",
           "t": int(now * 1000), "hw": hw}
    if migrated: rec["prev"] = prev
    with lock:
        keys[node] = {"pub": der, "trust": rec["trust"], "t": rec["t"], "hw": hw, **({"prev": prev} if migrated else {})}
        _log(rec)
    note = None
    if not paused["on"] and influx >= INFLUX[0]:
        set_pause(True, auto=True)
        note = f"SkywatchPL: napływ {influx} nowych urządzeń w {INFLUX[1] // 60} min — wpływ nowych na alarmy WSTRZYMANY. Sprawdź panel."
    return 200, {"node": node, "code": code_of(node), "trust": rec["trust"]}, note


def set_trust(code_or_node, trust):
    """Panel: awans po kodzie 1:1, blokada albo cofnięcie do nowych. Zwraca node albo None."""
    c = (code_or_node or "").replace("-", "").lower().strip()
    if len(c) < 8 or trust not in ("trusted", "new", "blocked"): return None
    with lock:
        hit = [n for n in keys if n.startswith(c)]
        if len(hit) != 1: return None
        keys[hit[0]]["trust"] = trust
        _log({"node": hit[0], "trust": trust, "t": int(time.time() * 1000)})
    return hit[0]


def set_pause(on, auto=False):
    with lock:
        paused.update(on=bool(on), auto=auto, t=int(time.time() * 1000))
        _log({"pause": bool(on), "auto": auto, "t": paused["t"]})


def trust_of(node):
    with lock: k = keys.get(node)
    return k.get("trust", "new") if k else "legacy"


def summary():
    now = time.time()
    with lock:
        c = collections.Counter(k.get("trust", "new") for k in keys.values())
        day = sum(1 for k in keys.values() if now - k.get("t", 0) / 1000 < 86400)
        hour = sum(1 for k in keys.values() if now - k.get("t", 0) / 1000 < 3600)
        recent = len([t for t in joins_all if now - t < INFLUX[1]])
    return {"trusted": c["trusted"], "new": c["new"], "blocked": c["blocked"], "joined_hour": hour, "joined_day": day,
            "influx": recent, "influx_limit": INFLUX[0], "influx_window_min": INFLUX[1] // 60, "paused": dict(paused)}

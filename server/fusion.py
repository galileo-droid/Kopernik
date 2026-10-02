"""Fuzja zgłoszeń przelotu z wielu telefonów w trasę drona.

Każdy telefon, który usłyszał przelot, zgłasza: swoją pozycję p, chwilę największego zbliżenia τ
(z efektu Dopplera), prędkość i częstotliwość silnika. Dla prostej trasy lecącej w kierunku u
z prędkością s rzut pozycji telefonu na kierunek lotu rośnie liniowo z τ:  p·u = a + s·τ.
Szukamy więc kierunku, dla którego te rzuty układają się w prostą (regresja liniowa po kącie),
a położenie trasy w poprzek (c = p·n) z tego, kto słyszał, a kto w pobliżu nie słyszał.
Zegary telefonów wystarczą z dokładnością do sekund — bez synchronizacji co do milisekundy.

Wynik służy ostrzeganiu ludzi dalej na trasie (zgrubny kierunek i czas), nie naprowadzaniu.
"""
import numpy as np

KMH = 1 / 3.6
CROSS_MAX = 2500.0     # m: dalej od trasy telefon praktycznie nie słyszy Shaheda


class Report:
    __slots__ = ("node", "x", "y", "t", "speed", "f", "rx")

    def __init__(self, node, x, y, t, speed_kmh, f_engine, rx=None):
        self.node, self.x, self.y, self.t = node, x, y, t
        self.speed, self.f = speed_kmh * KMH, f_engine
        self.rx = t if rx is None else rx          # kiedy zgłoszenie dotarło na serwer


def p_detect(d, r50=1500.0, width=350.0):
    """Szansa, że telefon w odległości d (m) od trasy usłyszy i rozpozna przelot."""
    return 1 / (1 + np.exp(np.clip((d - r50) / width, -50, 50)))


def fit_line(reps, speed_min=25.0, speed_max=80.0):
    """Kierunek i prędkość z samych (p, τ). Zwraca (θ, s, a, rms_s) albo None.
    Prędkość z Dopplera działa jako słaby priorytet — przy 2–3 punktach ustala znak i skalę."""
    P = np.array([[r.x, r.y] for r in reps]); T = np.array([r.t for r in reps])
    t0 = T.mean(); T = T - t0
    if np.ptp(T) < 3: return None
    s_dop = np.median([r.speed for r in reps])
    best = None
    for th in np.radians(np.arange(0, 360, 1.0)):
        u = np.array([np.cos(th), np.sin(th)])
        proj = P @ u
        s = (T @ (proj - proj.mean())) / (T @ T)            # nachylenie proj(τ)
        if not speed_min <= s <= speed_max: continue
        a = proj.mean()
        res_t = (proj - a - s * T) / s                        # błąd w sekundach
        cost = np.mean(res_t ** 2) + ((s - s_dop) / (0.15 * s_dop)) ** 2 * 4
        if best is None or cost < best[0]:
            best = (cost, th, s, a, float(np.sqrt(np.mean(res_t ** 2))))
    if best is None: return None
    _, th, s, a, rms = best
    return th, s, a, rms, t0


def fit_cross(reps, th, silent, r50=1500.0, width=350.0):
    """Położenie trasy w poprzek: max wiarygodności — słyszący blisko, cisi w zasięgu daleko."""
    n = np.array([-np.sin(th), np.cos(th)])
    ph = np.array([[r.x, r.y] for r in reps]) @ n
    ps = np.array(silent).reshape(-1, 2) @ n if len(silent) else np.zeros(0)
    cs = np.arange(ph.min() - 2 * r50, ph.max() + 2 * r50, 50.0)
    ll = np.array([np.log(p_detect(abs(ph - c), r50, width) + 1e-9).sum() +
                   np.log(1 - p_detect(abs(ps - c), r50, width) + 1e-9).sum() for c in cs])
    return float(cs[ll.argmax()])


class Track:
    def __init__(self, reps, th, s, a, rms, t0, c):
        self.reps, self.th, self.s, self.a, self.rms, self.t0, self.c = reps, th, s, a, rms, t0, c
        self.f = float(np.median([r.f for r in reps]))

    def pos(self, t):
        """Szacowana pozycja drona w chwili t (m, układ lokalny)."""
        u = np.array([np.cos(self.th), np.sin(self.th)]); n = np.array([-np.sin(self.th), np.cos(self.th)])
        return u * (self.a + self.s * (t - self.t0)) + n * self.c

    @property
    def heading_deg(self):   # azymut geograficzny: 0 = północ, 90 = wschód (x = wschód, y = północ)
        return (90 - np.degrees(self.th)) % 360

    def summary(self):
        return dict(heading_deg=round(self.heading_deg), speed_kmh=round(self.s / KMH),
                    f_engine=round(self.f, 1), reports=len(self.reps), rms_s=round(self.rms, 1))


def fuse(reports, nodes_xy, now, window=300.0, f_tol=0.04, min_reports=3, rms_max=6.0, rng=None,
         max_gap=15_000.0, r50=1500.0):
    """Zgłoszenia z ostatnich `window` s → lista tras. RANSAC po trójkach zgodnych częstotliwością,
    potem dopasowanie na wszystkich pasujących. `nodes_xy`: {node: (x, y)} aktywnych telefonów
    (także tych, które nic nie zgłosiły — ich cisza też jest informacją). `r50`: zasięg słyszenia
    punktów (telefon ~1,5 km; stała stacja z lepszym mikrofonem dalej) — skaluje też próg w poprzek trasy."""
    rng = rng or np.random.default_rng(0)
    cross_max = CROSS_MAX * r50 / 1500.0
    pool = [r for r in reports if now - window <= r.rx <= now]
    ids = list(nodes_xy); XY = np.array([nodes_xy[k] for k in ids], float).reshape(-1, 2)
    tracks = []
    while len(pool) >= min_reports:
        best = None
        idx = list(range(len(pool)))
        for _ in range(60):
            i = rng.choice(idx)
            # ten sam dron: zgodna f, ±180 s i blisko (≤180 s lotu + zasięg słyszenia) — ważne przy
            # wielu dronach naraz na dużym obszarze, inaczej trójki mieszają drony z różnych miejsc
            cand = [j for j in idx if abs(pool[j].f / pool[i].f - 1) < f_tol and abs(pool[j].t - pool[i].t) < 180
                    and np.hypot(pool[j].x - pool[i].x, pool[j].y - pool[i].y) < max_gap]
            if len(cand) < min_reports: continue
            pick = rng.choice(cand, min_reports, replace=False)
            fl = fit_line([pool[j] for j in pick])
            if not fl or fl[3] > rms_max: continue
            th, s, a, rms, t0 = fl
            u = np.array([np.cos(th), np.sin(th)])
            inl = [j for j in cand if abs(((np.array([pool[j].x, pool[j].y]) @ u) - a - s * (pool[j].t - t0)) / s) < 2.5 * rms_max]
            if best is None or len(inl) > len(best): best = inl
        if not best or len(best) < min_reports: break
        # dopasowanie na trójce/inlierach, potem 2× dociągnięcie wszystkich zgodnych zgłoszeń
        # (czas, częstotliwość, odległość od trasy) — inaczej jeden dron rozpada się na kilka tras
        members = best
        for _ in range(3):
            reps = [pool[j] for j in members]
            fl = fit_line(reps)
            if not fl or fl[3] > rms_max: break
            th, s, a, rms, t0 = fl
            heard = {r.node for r in reps}
            # cisi tylko w okolicy słyszących — dalsze telefony (p_detect ≈ 0) nic nie wnoszą, a przy
            # tysiącach telefonów na granicy liczenie po wszystkich trwało godzinami
            H = np.array([[r.x, r.y] for r in reps]); m = 2 * cross_max
            box = np.all((XY >= H.min(0) - m) & (XY <= H.max(0) + m), axis=1)
            silent = [XY[k] for k in np.flatnonzero(box) if ids[k] not in heard]
            c = fit_cross(reps, th, silent, r50)
            u = np.array([np.cos(th), np.sin(th)]); nv = np.array([-u[1], u[0]])
            fm = np.median([r.f for r in reps])
            members = [j for j, r in enumerate(pool)
                       if abs(r.f / fm - 1) < f_tol
                       and abs(((np.array([r.x, r.y]) @ u) - a - s * (r.t - t0)) / s) < 2 * rms_max
                       and abs(np.array([r.x, r.y]) @ nv - c) < cross_max]
            if len(members) < min_reports: break
        if not fl or fl[3] > rms_max or len(members) < min_reports:
            pool = [r for k, r in enumerate(pool) if k not in set(best)]; continue
        reps = [pool[j] for j in members]
        used = set(members) | set(best)
        pool = [r for k, r in enumerate(pool) if k not in used]
        if plausible(reps, th, c, silent, r50, max_cross=cross_max): tracks.append(Track(reps, th, s, a, rms, t0, c))
    return tracks


def plausible(reps, th, c, silent, r50=1500.0, near=0.6, min_frac=0.4, max_cross=CROSS_MAX):
    """Czy trasa ma sens przestrzennie: słyszący leżą przy niej, a telefony tuż przy trasie
    (w odcinku, który słyszano) też w większości słyszały. Losowe fałszywe zgłoszenia z różnych
    końców miasta dają trasę, przy której dużo telefonów milczy — odrzucamy."""
    u = np.array([np.cos(th), np.sin(th)]); n = np.array([-u[1], u[0]])
    H = np.array([[r.x, r.y] for r in reps])
    if np.any(np.abs(H @ n - c) > max_cross): return False
    lo, hi = (H @ u).min(), (H @ u).max()
    close = lambda P: (np.abs(P @ n - c) < near * r50) & (P @ u >= lo) & (P @ u <= hi)
    h = int(close(H).sum())
    q = int(close(np.array(silent).reshape(-1, 2)).sum()) if len(silent) else 0
    return h + q == 0 or h / (h + q) >= min_frac

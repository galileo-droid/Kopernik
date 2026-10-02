"""Symulator sieci telefonów: ile punktów nasłuchu trzeba, żeby z przelotów złożyć trasę drona.

Wirtualne telefony losowo na obszarze miasta, drony po prostych trasach. Każdy telefon słyszy
przelot z prawdopodobieństwem zależnym od odległości i zgłasza go z błędami takimi jak
w aplikacji (chwila przelotu, prędkość z Dopplera, f silnika, pozycja zaokrąglona do ~100 m),
z opóźnieniem (zgłoszenie idzie po końcu toru). Do tego fałszywe przeloty.

  python3 sim_network.py                      # tabela dla kilku gęstości
  python3 sim_network.py --map przyklad.png   # jeden przykładowy scenariusz na mapie
"""
import argparse
import numpy as np
from fusion import Report, fuse, p_detect, KMH

AREA = 20_000.0        # bok obszaru, m (≈ Warszawa w granicach obwodnicy)


def scenario(rng, density, n_drones=1, r50=1500.0, false_per_node_h=0.1, latency="track_end",
             t_span=900.0):
    """Zwraca (telefony {id: (x, y)}, zgłoszenia, drony) dla jednego scenariusza."""
    n = rng.poisson(density * (AREA / 1000) ** 2)
    nodes = {i: tuple(rng.uniform(0, AREA, 2)) for i in range(n)}
    drones, reps = [], []
    for k in range(n_drones):
        th = rng.uniform(0, 2 * np.pi); u = np.array([np.cos(th), np.sin(th)])
        s = rng.uniform(150, 190) * KMH
        alt = rng.uniform(100, 800)
        f = rng.uniform(85, 125)
        mid = rng.uniform(0.3 * AREA, 0.7 * AREA, 2)
        tmid = rng.uniform(300, t_span - 300)
        drones.append(dict(th=th, s=s, mid=mid, tmid=tmid, f=f, alt=alt))
        for i, (x, y) in nodes.items():
            p = np.array([x, y]) - mid
            along = p @ u; cross = abs(p @ np.array([-u[1], u[0]]))
            d = np.hypot(cross, alt)
            if rng.random() > p_detect(d, r50): continue
            t_cpa = tmid + along / s
            if not 0 <= t_cpa <= t_span: continue
            # błędy jak w aplikacji: chwila przelotu ±2 s (+ zegar ±0,5 s), prędkość ±8 %, f ±2 %
            t_rep = t_cpa + rng.normal(0, 2.0) + rng.normal(0, 0.5)
            spd = s / KMH * rng.normal(1, 0.08)
            fe = f * rng.normal(1, 0.02)
            # pozycja wysyłana z dokładnością 0,001° (~110 m N-S, ~70 m W-E) + błąd GPS
            xr = round((x + rng.normal(0, 10)) / 70) * 70; yr = round((y + rng.normal(0, 10)) / 110) * 110
            # opóźnienie: po końcu toru (dron poza zasięgiem + 6 s ciszy) albo zaraz po spadku tonu
            hear = np.sqrt(max(r50 ** 2 - d ** 2, 0)) / s
            delay = hear + 6 + 2 if latency == "track_end" else 15 + 2
            reps.append(Report(i, xr, yr, t_rep, spd, fe, rx=t_cpa + delay))
    # fałszywe „przeloty” (samochody, samoloty, motocykle, które przeszły przez filtry)
    for i, (x, y) in nodes.items():
        for _ in range(rng.poisson(false_per_node_h * t_span / 3600)):
            t = rng.uniform(0, t_span)
            reps.append(Report(i, x, y, t, rng.uniform(60, 250), rng.uniform(75, 135), rx=t + 20))
    return nodes, reps, drones


def evaluate(track, drone):
    """Błędy trasy względem prawdy: kierunek (°), prędkość (km/h), odległość w poprzek (m)."""
    dth = np.degrees(np.angle(np.exp(1j * (track.th - drone["th"]))))
    u = np.array([np.cos(drone["th"]), np.sin(drone["th"])]); n = np.array([-u[1], u[0]])
    t = np.median([r.t for r in track.reps])
    est = track.pos(t); true = drone["mid"] + u * drone["s"] * (t - drone["tmid"])
    return abs(dth), abs(track.s - drone["s"]) / KMH, abs((est - true) @ n), float(np.linalg.norm(est - true))


def match(tracks, drones, max_dth=25):
    """Przypisanie tras do dronów po kierunku i częstotliwości."""
    out = []
    for d in drones:
        best = None
        for tr in tracks:
            dth, dv, dx, dpos = evaluate(tr, d)
            if dth < max_dth and abs(tr.f / d["f"] - 1) < 0.05 and (best is None or dpos < best[1][3]):
                best = (tr, (dth, dv, dx, dpos))
        out.append(best)
    return out


def first_fix(reps, nodes, drone, min_reports=3):
    """Po ilu sekundach od pierwszego zgłoszenia tego drona serwer ma już trasę (≤25° błędu)."""
    rx = sorted({r.rx for r in reps})
    for now in rx:
        tr = fuse(reps, nodes, now, min_reports=min_reports)
        m = match(tr, [drone])[0]
        if m: return now, m[1]
    return None, None


def run(density, trials, rng, **kw):
    ok, errs, lat, false_tracks, hours = 0, [], [], 0, 0.0
    for _ in range(trials):
        nodes, reps, drones = scenario(rng, density, **kw)
        d = drones[0]
        tr = fuse(reps, nodes, now=10_000, window=20_000)
        m = match(tr, drones)[0]
        false_tracks += len(tr) - (1 if m else 0)
        hours += 900 / 3600
        if m:
            ok += 1; errs.append(m[1])
            t_fix, _ = first_fix(reps, nodes, d)
            if t_fix is not None:
                # ile sekund dron leci od pierwszego telefonu, który go usłyszał, do chwili, gdy serwer zna trasę
                mine = [r.t for r in m[0].reps]
                lat.append(t_fix - min(mine))
    e = np.array(errs) if errs else np.zeros((1, 4))
    return dict(density=density, found=ok / trials, dth=np.median(e[:, 0]), dv=np.median(e[:, 1]),
                dx=np.median(e[:, 2]), fix_delay=np.median(lat) if lat else np.nan,
                false_per_h=false_tracks / hours)


def draw(path, rng, density, false_per_node_h=0.1):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, MUTED, GRID, SURF = "#1f1f1f", "#8a8f98", "#d7d9dc", "#fcfcfb"
    BLUE, ORANGE = "#2a78d6", "#eb6834"      # sprawdzone walidatorem palety (CVD, kontrast)
    nodes, reps, drones = scenario(rng, density, n_drones=2, false_per_node_h=false_per_node_h)
    tr = fuse(reps, nodes, now=10_000, window=20_000)
    fig, ax = plt.subplots(figsize=(7, 7.4), dpi=140, facecolor=SURF); ax.set_facecolor(SURF)
    xy = np.array(list(nodes.values())) / 1000
    ax.scatter(xy[:, 0], xy[:, 1], s=5, c=GRID, lw=0, label="telefon (nic nie zgłosił)")
    for k, d in enumerate(drones):
        u = np.array([np.cos(d["th"]), np.sin(d["th"])])
        a, b = d["mid"] - u * 15000, d["mid"] + u * 15000
        ax.plot([a[0] / 1000, b[0] / 1000], [a[1] / 1000, b[1] / 1000], c=INK, lw=1, ls=(0, (4, 3)),
                label="prawdziwa trasa drona" if k == 0 else None)
    used = {id(r) for t in tr for r in t.reps}
    U = np.array([[r.x, r.y] for r in reps if id(r) in used]).reshape(-1, 2) / 1000
    R = np.array([[r.x, r.y] for r in reps if id(r) not in used]).reshape(-1, 2) / 1000
    if len(R): ax.scatter(R[:, 0], R[:, 1], s=36, facecolors="none", edgecolors=MUTED, lw=1.2, label="zgłoszenie odrzucone (fałszywe)", zorder=3)
    ax.scatter(U[:, 0], U[:, 1], s=40, c=ORANGE, edgecolors=SURF, lw=1.5, label="zgłoszenie przelotu użyte w trasie", zorder=4)
    for t in tr:
        ts = [r.t for r in t.reps]
        P = np.array([t.pos(x) for x in np.arange(min(ts) - 300, max(ts) + 300, 2.0)]) / 1000
        P = P[(P >= 0).all(1) & (P <= AREA / 1000).all(1)]
        if len(P) < 3: continue
        ax.plot(P[:-2, 0], P[:-2, 1], c=BLUE, lw=2, zorder=5, solid_capstyle="round")
        ax.annotate("", xy=P[-1], xytext=P[-3], zorder=5,
                    arrowprops=dict(arrowstyle="-|>,head_width=0.45,head_length=0.9", color=BLUE, lw=2))
        sm = t.summary()
        m = P[len(P) // 2]
        ax.text(m[0] + 0.4, m[1] + 0.4, f"{sm['heading_deg']}°  {sm['speed_kmh']} km/h\n{sm['reports']} zgłoszeń",
                color=INK, fontsize=8, zorder=6, bbox=dict(fc=SURF, ec="none", pad=1.5, alpha=0.9))
    ax.plot([], [], c=BLUE, lw=2, label="trasa wyliczona przez serwer")
    ax.set_xlim(0, AREA / 1000); ax.set_ylim(0, AREA / 1000); ax.set_aspect("equal")
    ax.set_xlabel("km (wschód)", color=MUTED, fontsize=8); ax.set_ylabel("km (północ)", color=MUTED, fontsize=8)
    ax.tick_params(colors=MUTED, labelsize=7)
    for sp in ax.spines.values(): sp.set_color(GRID)
    ax.set_title(f"Symulacja: {len(nodes)} telefonów na 20×20 km ({density}/km²), 2 drony", fontsize=10, color=INK, loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2, fontsize=7.5, frameon=False, labelcolor=INK)
    fig.tight_layout(); fig.savefig(path, facecolor=SURF)
    return tr, drones


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--r50", type=float, default=1500)
    ap.add_argument("--false", type=float, default=0.1, help="fałszywe przeloty na telefon na godzinę")
    ap.add_argument("--latency", choices=["track_end", "sweep_end"], default="track_end")
    ap.add_argument("--map")
    ap.add_argument("--density", type=float, default=0.5)
    a = ap.parse_args()
    rng = np.random.default_rng(7)
    if a.map:
        tr, dr = draw(a.map, rng, a.density, a.false)
        for t in tr: print(t.summary())
        for d in dr: print("prawda:", round((90 - np.degrees(d["th"])) % 360), "°", round(d["s"] / KMH), "km/h", round(d["f"], 1), "Hz")
    else:
        print(f"zasięg słyszenia (50 %) {a.r50:.0f} m, fałszywe {a.false}/tel./h, zgłoszenie: {a.latency}")
        print("tel/km²  wykryte  kier.°  prędk.km/h  poprzek m  trasa znana po, s (km lotu)  fałsz. tras/h")
        for dens in (0.05, 0.1, 0.25, 0.5, 1.0):
            r = run(dens, a.trials, rng, r50=a.r50, false_per_node_h=a.false, latency=a.latency)
            print(f"{dens:7.2f}  {r['found']*100:6.0f}%  {r['dth']:6.1f}  {r['dv']:10.0f}  {r['dx']:9.0f}  {r['fix_delay']:19.0f} ({r['fix_delay']*0.047:4.1f} km)  {r['false_per_h']:12.2f}")

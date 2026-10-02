"""Symulacja pasa przygranicznego: telefony w pasie wzdłuż wschodniej granicy, drony przelatują w głąb kraju.

Granica wyprostowana do odcinka: oś x = wzdłuż granicy (0…L), oś y = w głąb Polski (0 = granica).
Telefony tylko w pasie 0 ≤ y ≤ DEPTH. Drony wlatują od wschodu (y < 0) pod kątem do ±40° od
prostopadłej i lecą dalej w głąb. Pytanie: czy serwer zna trasę, zanim dron wyjdzie z pasa,
czyli czy ostrzeżenie „leci w waszą stronę” wyprzedza drona w głębi kraju.

  python3 sim_border.py                          # tabela: 20 dronów w godzinę, rozrzuconych po granicy
  python3 sim_border.py --wave                   # 20 dronów falą: odcinek 60 km, 10 min
  python3 sim_border.py --map pas.png --density 0.25
"""
import argparse
import numpy as np
from fusion import Report, fuse, p_detect, KMH

BORDER = 953_000.0     # m: Białoruś 418 km + Ukraina 535 km
DEPTH = 10_000.0       # m: głębokość pasa z telefonami
STEP = 10.0            # s: co ile serwer przelicza trasy


def scenario(rng, density, n_drones=20, t_span=3600.0, wave=False, r50=1500.0, false_per_node_h=0.1,
             L=BORDER, XY=None):
    """XY podane = stałe stacje (dokładna pozycja, bez kratki prywatności); inaczej losowe telefony."""
    exact = XY is not None
    if XY is None:
        n = rng.poisson(density * L * DEPTH / 1e6)
        XY = np.column_stack([rng.uniform(0, L, n), rng.uniform(0, DEPTH, n)])
    n = len(XY)
    drones, reps = [], []
    x0w = rng.uniform(30_000, L - 90_000)
    for k in range(n_drones):
        dev = np.radians(rng.uniform(-40, 40))                 # odchylenie od kierunku „prosto w głąb”
        th = np.pi / 2 + dev; u = np.array([np.cos(th), np.sin(th)]); nv = np.array([-u[1], u[0]])
        s = rng.uniform(150, 190) * KMH
        alt = rng.uniform(100, 800)
        f = rng.uniform(85, 125)
        if wave:
            xb, tb = x0w + rng.uniform(0, 60_000), rng.uniform(300, 900)
        else:
            xb, tb = rng.uniform(20_000, L - 20_000), rng.uniform(300, t_span - 900)
        mid = np.array([xb, 0.0])                              # punkt przekroczenia granicy, chwila tb
        drones.append(dict(th=th, s=s, mid=mid, tmid=tb, f=f, alt=alt, dev=dev))
        P = XY - mid
        along = P @ u; d = np.hypot(np.abs(P @ nv), alt)
        hit = (rng.random(n) < p_detect(d, r50)) & (along > 0)   # przed granicą dron jeszcze nie był
        for i in np.flatnonzero(hit):
            t_cpa = tb + along[i] / s
            t_rep = t_cpa + rng.normal(0, 2.0) + rng.normal(0, 0.5)
            x, y = XY[i]
            if exact: xr, yr = x, y
            else: xr = round((x + rng.normal(0, 10)) / 70) * 70; yr = round((y + rng.normal(0, 10)) / 110) * 110
            hear = np.sqrt(max(r50 ** 2 - d[i] ** 2, 0)) / s
            reps.append(Report(i, xr, yr, t_rep, s / KMH * rng.normal(1, 0.08), f * rng.normal(1, 0.02),
                               rx=t_cpa + hear + 8))
    span = 1500.0 if wave else t_span
    for i in range(n):
        for _ in range(rng.poisson(false_per_node_h * span / 3600)):
            t = rng.uniform(0, span)
            reps.append(Report(i, *XY[i], t, rng.uniform(60, 250), rng.uniform(75, 135), rx=t + 20))
    nodes = {i: tuple(XY[i]) for i in range(n)}
    return nodes, reps, drones, span


def err(track, d):
    dth = abs(np.degrees(np.angle(np.exp(1j * (track.th - d["th"])))))
    u = np.array([np.cos(d["th"]), np.sin(d["th"])]); nv = np.array([-u[1], u[0]])
    t = np.median([r.t for r in track.reps])
    e = track.pos(t) - (d["mid"] + u * d["s"] * (t - d["tmid"]))
    return dth, abs(track.s - d["s"]) / KMH, abs(e @ nv), float(np.linalg.norm(e))


def assign(tracks, drones, max_dth=25):
    """Każdej trasie najbliższy zgodny dron (kierunek, f); zwraca {nr drona: (trasa, błędy)}."""
    out = {}
    for tr in tracks:
        best = None
        for k, d in enumerate(drones):
            e = err(tr, d)
            if e[0] < max_dth and abs(tr.f / d["f"] - 1) < 0.05 and e[3] < 5000 and (best is None or e[3] < best[1][3]):
                best = (k, e)
        if best and (best[0] not in out or best[1][3] < out[best[0]][1][3]):
            out[best[0]] = (tr, best[1])
    return out


def run(rng, density, **kw):
    nodes, reps, drones, span = scenario(rng, density, **kw)
    r50 = kw.get("r50", 1500.0)
    # na żywo: serwer co STEP s przelicza zgłoszenia z ostatnich 5 min
    first, false_ids = {}, 0
    for now in np.arange(STEP, span + 600, STEP):
        tr = fuse(reps, nodes, now, r50=r50)
        a = assign(tr, drones)
        for k, (t, e) in a.items():
            first.setdefault(k, (now, e))
        false_ids += len(tr) - len(a)
    final = assign(fuse(reps, nodes, now=1e9, window=1e10, r50=r50), drones)
    rows = []
    for k, d in enumerate(drones):
        if k in first:
            t_fix = first[k][0] - d["tmid"]
            depth = d["s"] * t_fix * np.cos(d["dev"])          # ile km w głąb był dron, gdy trasa się pojawiła
        else:
            t_fix = depth = np.nan
        e = final[k][1] if k in final else (np.nan,) * 4
        rows.append(dict(found=k in final, live=k in first, t_fix=t_fix, depth=depth, dth=e[0], dv=e[1], dx=e[2]))
    # fałszywe trasy: liczymy „trasa-przeliczenie” bez drona, przeliczone na unikalne ~ (trasa żyje ~5 min)
    false_h = false_ids * STEP / 300 / (span / 3600)
    return len(nodes), rows, false_h


def table(a):
    rng = np.random.default_rng(11)
    kind = "fala: 20 dronów, odcinek 60 km, 10 min" if a.wave else f"{a.drones} dronów w ciągu godziny, rozrzucone po całej granicy"
    print(f"Pas {BORDER/1000:.0f} km × {DEPTH/1000:.0f} km, {kind}; zasięg słyszenia (50 %) {a.r50:.0f} m, "
          f"fałszywe {a.false}/tel./h; {a.runs} przebiegi na gęstość")
    print("tel/km²  telefonów  trasa  w pasie  czas do trasy s  głęb. km  kier.°  poprzek m  fałsz./h")
    for dens in a.densities:
        R, nn, fh = [], [], []
        for _ in range(a.runs):
            n, rows, f = run(rng, dens, n_drones=a.drones, wave=a.wave, r50=a.r50, false_per_node_h=a.false)
            R += rows; nn.append(n); fh.append(f)
        g = lambda key: np.array([r[key] for r in R], float)
        found = g("found").mean(); inside = (g("depth") <= DEPTH).mean()   # nan (brak trasy) liczy się jako nie
        print(f"{dens:7.2f}  {np.mean(nn):9.0f}  {found*100:4.0f}%  {inside*100:6.0f}%  "
              f"{np.nanmedian(g('t_fix')):15.0f}  {np.nanmedian(g('depth'))/1000:8.1f}  "
              f"{np.nanmedian(g('dth')):6.1f}  {np.nanmedian(g('dx')):9.0f}  {np.mean(fh):8.1f}")


def draw(path, rng, density, r50=1500.0, false=0.1, L=120_000.0, n_drones=4):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, MUTED, GRID, SURF = "#1f1f1f", "#8a8f98", "#d7d9dc", "#fcfcfb"
    BLUE, ORANGE = "#2a78d6", "#eb6834"
    nodes, reps, drones, span = scenario(rng, density, n_drones=n_drones, wave=True, r50=r50,
                                         false_per_node_h=false, L=L)
    tr = fuse(reps, nodes, now=1e9, window=1e10)
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=140, facecolor=SURF); ax.set_facecolor(SURF)
    ax.axhspan(-6, 0, color="#eeeeec", zorder=0)
    ax.axhline(0, color=MUTED, lw=1); ax.axhline(DEPTH / 1000, color=GRID, lw=1, ls=(0, (2, 2)))
    tf = ax.get_yaxis_transform()
    ax.text(0.01, -5.2, "Białoruś / Ukraina", color=MUTED, fontsize=8, transform=tf)
    ax.text(0.01, DEPTH / 1000 + 0.5, "koniec pasa z telefonami (10 km)", color=MUTED, fontsize=8, transform=tf)
    xy = np.array(list(nodes.values())) / 1000
    ax.scatter(xy[:, 0], xy[:, 1], s=3, c=GRID, lw=0, label="telefon")
    for k, d in enumerate(drones):
        u = np.array([np.cos(d["th"]), np.sin(d["th"])])
        a, b = d["mid"] - u * 8000, d["mid"] + u * 30000
        ax.plot([a[0] / 1000, b[0] / 1000], [a[1] / 1000, b[1] / 1000], c=INK, lw=1, ls=(0, (4, 3)),
                label="prawdziwa trasa drona" if k == 0 else None)
    used = {id(r) for t in tr for r in t.reps}
    U = np.array([[r.x, r.y] for r in reps if id(r) in used]).reshape(-1, 2) / 1000
    R = np.array([[r.x, r.y] for r in reps if id(r) not in used]).reshape(-1, 2) / 1000
    if len(R): ax.scatter(R[:, 0], R[:, 1], s=20, facecolors="none", edgecolors=MUTED, lw=1, label="zgłoszenie odrzucone", zorder=3)
    ax.scatter(U[:, 0], U[:, 1], s=24, c=ORANGE, edgecolors=SURF, lw=1, label="zgłoszenie użyte w trasie", zorder=4)
    for t in tr:
        ts = [r.t for r in t.reps]
        P = np.array([t.pos(x) for x in np.arange(min(ts) - 60, max(ts) + 200, 2.0)]) / 1000
        P = P[(P[:, 1] >= -2) & (P[:, 1] <= 16)]
        if len(P) < 3: continue
        ax.plot(P[:-2, 0], P[:-2, 1], c=BLUE, lw=2, zorder=5)
        ax.annotate("", xy=P[-1], xytext=P[-3], zorder=5,
                    arrowprops=dict(arrowstyle="-|>,head_width=0.4,head_length=0.8", color=BLUE, lw=2))
        m = P[-1]; dev = (np.degrees(t.th) - 90 + 180) % 360 - 180     # odchylenie od „prosto w głąb”
        ax.text(m[0] + 0.8, m[1] - 0.6, f"{dev:+.0f}°  {t.s / KMH:.0f} km/h", color=INK, fontsize=7, zorder=6)
    ax.plot([], [], c=BLUE, lw=2, label="trasa wyliczona przez serwer")
    xs = [d["mid"][0] / 1000 for d in drones]
    ax.set_xlim(max(0, min(xs) - 15), min(L / 1000, max(xs) + 25)); ax.set_ylim(-6, 17); ax.set_aspect("equal")
    ax.set_xlabel("km wzdłuż granicy", color=MUTED, fontsize=8); ax.set_ylabel("km w głąb", color=MUTED, fontsize=8)
    ax.tick_params(colors=MUTED, labelsize=7)
    for sp in ax.spines.values(): sp.set_color(GRID)
    ax.set_title(f"Wycinek pasa przygranicznego: {density} tel./km², {n_drones} drony falą", fontsize=10, color=INK, loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=7.5, frameon=False, labelcolor=INK)
    fig.tight_layout(); fig.savefig(path, facecolor=SURF)
    return tr, drones


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drones", type=int, default=20)
    ap.add_argument("--wave", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--r50", type=float, default=1500)
    ap.add_argument("--false", type=float, default=0.1)
    ap.add_argument("--densities", type=float, nargs="+", default=[0.05, 0.1, 0.25, 0.5, 1.0])
    ap.add_argument("--map"); ap.add_argument("--density", type=float, default=0.25)
    a = ap.parse_args()
    if a.map:
        tr, dr = draw(a.map, np.random.default_rng(5), a.density, a.r50, a.false)
        for t in tr: print(t.summary())
    else:
        table(a)

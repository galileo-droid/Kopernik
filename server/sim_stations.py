"""Symulacja pasa przygranicznego ze STAŁYMI stacjami (ESP32 / Raspberry Pi + lepszy mikrofon) zamiast telefonów.

Te same założenia co sim_border.py (granica 953 km wyprostowana, drony wlatują pod ±40°), ale punkty
stoją w regularnych rzędach równoległych do granicy, rzędy co drugi przesunięte o pół odstępu.
Stacja zna dokładną pozycję (bez kratki prywatności), słyszy dalej (r50) i rzadziej się myli.
Zasięg stacji to ZAŁOŻENIE do sprawdzenia prototypem, nie pomiar.

  python3 sim_stations.py                      # tabela układów, drony rozrzucone w godzinę
  python3 sim_stations.py --wave               # to samo, fala 20 dronów na 60 km
  python3 sim_stations.py --map pas.png --layout 3x6
"""
import argparse
import numpy as np
from fusion import fuse, KMH
from sim_border import BORDER, scenario, run

# nazwa: (y rzędów w km od granicy, odstęp w rzędzie km)
LAYOUTS = {
    "1x4":  ([3], 4),
    "2x6":  ([2, 8], 6),
    "3x6":  ([1, 5, 9], 6),
    "3x4":  ([1, 5, 9], 4),
    "4x5":  ([1, 5, 9, 13], 5),
    "5x4":  ([1, 5, 9, 13, 17], 4),
    "4x4":  ([1, 4, 7, 10], 4),          # gęsto przy samej granicy: wszystko w pierwszych 10 km
}


def stations(rows_km, step_km, L=BORDER):
    pts = []
    for k, y in enumerate(rows_km):
        x0 = (step_km / 2 if k % 2 else 0) * 1000
        xs = np.arange(x0, L, step_km * 1000)
        pts += [(x, y * 1000) for x in xs]
    return np.array(pts, float)


def table(a):
    rng = np.random.default_rng(11)
    kind = "fala: 20 dronów, odcinek 60 km, 10 min" if a.wave else f"{a.drones} dronów w godzinę, rozrzucone po granicy"
    print(f"Granica {BORDER/1000:.0f} km, {kind}; {a.runs} przebiegi na układ. "
          f"„przed 10 km” = trasa znana, zanim dron wleciał 10 km w głąb.")
    print("układ          zasięg  stacji  trasa  przed 10 km  czas do trasy s  głęb. km  kier.°  poprzek m  fałsz./h")
    cases = [("telefony 0.25/km²", None, 1500.0, 0.1)]
    for r50 in a.r50s:
        cases += [(name, name, r50, a.false) for name in a.layouts]
    for label, lay, r50, false in cases:
        R, nn, fh = [], [], []
        for _ in range(a.runs):
            XY = stations(*LAYOUTS[lay]) if lay else None
            n, rows, f = run(rng, 0.25, n_drones=a.drones, wave=a.wave, r50=r50, false_per_node_h=false, XY=XY)
            R += rows; nn.append(n); fh.append(f)
        g = lambda key: np.array([r[key] for r in R], float)
        name = f"{lay[0]} rz. co {lay[2:]} km" if lay else label
        print(f"{name:17s} {r50/1000:4.1f} km {np.mean(nn):6.0f}  {g('found').mean()*100:4.0f}%  "
              f"{(g('depth') <= 10_000).mean()*100:10.0f}%  {np.nanmedian(g('t_fix')):15.0f}  "
              f"{np.nanmedian(g('depth'))/1000:8.1f}  {np.nanmedian(g('dth')):6.1f}  "
              f"{np.nanmedian(g('dx')):9.0f}  {np.mean(fh):8.1f}", flush=True)


def draw(path, layout, r50, false, seed=5, L=120_000.0, n_drones=4):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, MUTED, GRID, SURF = "#1f1f1f", "#8a8f98", "#d7d9dc", "#fcfcfb"
    BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#2e8b57"
    rows, step = LAYOUTS[layout]
    XY = stations(rows, step, L)
    nodes, reps, drones, span = scenario(np.random.default_rng(seed), 0, n_drones=n_drones, wave=True, r50=r50,
                                         false_per_node_h=false, L=L, XY=XY)
    tr = fuse(reps, nodes, now=1e9, window=1e10, r50=r50)
    top = max(rows) + 6
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=140, facecolor=SURF); ax.set_facecolor(SURF)
    ax.axhspan(-6, 0, color="#eeeeec", zorder=0); ax.axhline(0, color=MUTED, lw=1)
    tf = ax.get_yaxis_transform()
    ax.text(0.01, -5.2, "Białoruś / Ukraina", color=MUTED, fontsize=8, transform=tf)
    xy = XY / 1000
    for p in xy:
        ax.add_patch(plt.Circle(p, r50 / 1000, color=GREEN, alpha=0.06, lw=0, zorder=1))
    ax.scatter(xy[:, 0], xy[:, 1], s=14, marker="^", c=GREEN, lw=0, label=f"stacja (zasięg {r50/1000:.1f} km)", zorder=2)
    for k, d in enumerate(drones):
        u = np.array([np.cos(d["th"]), np.sin(d["th"])])
        a, b = d["mid"] - u * 8000, d["mid"] + u * (top + 10) * 1000
        ax.plot([a[0] / 1000, b[0] / 1000], [a[1] / 1000, b[1] / 1000], c=INK, lw=1, ls=(0, (4, 3)),
                label="prawdziwa trasa drona" if k == 0 else None)
    used = {id(r) for t in tr for r in t.reps}
    U = np.array([[r.x, r.y] for r in reps if id(r) in used]).reshape(-1, 2) / 1000
    Rj = np.array([[r.x, r.y] for r in reps if id(r) not in used]).reshape(-1, 2) / 1000
    if len(Rj): ax.scatter(Rj[:, 0], Rj[:, 1], s=30, facecolors="none", edgecolors=MUTED, lw=1, label="zgłoszenie odrzucone", zorder=3)
    if len(U): ax.scatter(U[:, 0], U[:, 1], s=34, c=ORANGE, edgecolors=SURF, lw=1, label="zgłoszenie użyte w trasie", zorder=4)
    for j, t in enumerate(sorted(tr, key=lambda t: t.pos(np.median([r.t for r in t.reps]))[0])):
        ts = [r.t for r in t.reps]
        P = np.array([t.pos(x) for x in np.arange(min(ts) - 60, max(ts) + 300, 2.0)]) / 1000
        P = P[(P[:, 1] >= -2) & (P[:, 1] <= top - 1.5 - 1.6 * (j % 3))]
        if len(P) < 3: continue
        ax.plot(P[:-2, 0], P[:-2, 1], c=BLUE, lw=2, zorder=5)
        ax.annotate("", xy=P[-1], xytext=P[-3], zorder=5,
                    arrowprops=dict(arrowstyle="-|>,head_width=0.4,head_length=0.8", color=BLUE, lw=2))
        dev = (np.degrees(t.th) - 90 + 180) % 360 - 180
        ax.text(P[-1][0] + 0.8, P[-1][1] - 0.8, f"{dev:+.0f}°  {t.s / KMH:.0f} km/h", color=INK, fontsize=7, zorder=6)
    ax.plot([], [], c=BLUE, lw=2, label="trasa wyliczona przez serwer")
    xs = [d["mid"][0] / 1000 for d in drones]
    ax.set_xlim(max(0, min(xs) - 15), min(L / 1000, max(xs) + 25)); ax.set_ylim(-6, top); ax.set_aspect("equal")
    ax.set_xlabel("km wzdłuż granicy", color=MUTED, fontsize=8); ax.set_ylabel("km w głąb", color=MUTED, fontsize=8)
    ax.tick_params(colors=MUTED, labelsize=7)
    for sp in ax.spines.values(): sp.set_color(GRID)
    n_all = len(stations(rows, step))
    ax.set_title(f"Stałe stacje: {len(rows)} rzędy co {step} km ({n_all} na całą granicę), {n_drones} drony falą",
                 fontsize=10, color=INK, loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3, fontsize=7.5, frameon=False, labelcolor=INK)
    fig.tight_layout(); fig.savefig(path, facecolor=SURF)
    return tr, drones


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drones", type=int, default=20)
    ap.add_argument("--wave", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--r50s", type=float, nargs="+", default=[3000, 2000])
    ap.add_argument("--false", type=float, default=0.05)
    ap.add_argument("--layouts", nargs="+", default=list(LAYOUTS))
    ap.add_argument("--map"); ap.add_argument("--layout", default="3x6"); ap.add_argument("--r50", type=float, default=3000)
    a = ap.parse_args()
    if a.map:
        tr, dr = draw(a.map, a.layout, a.r50, a.false)
        print(f"{len(dr)} dronów, {len(tr)} tras"); [print(t.summary()) for t in tr]
    else:
        table(a)

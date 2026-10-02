#!/usr/bin/env python3
"""
SkywatchPL — próba całego łańcucha na żywym hubie: przelot jednego drona nad testowymi nadajnikami (sim_phones).

  1. dron leci trasą (punkty --route) z prędkością --speed, na wysokości --alt;
  2. nadajniki SIM w trybie czujnika w promieniu --hear km co ~15 s wysyłają wykrycie tonu (harmonic, z Dopplerem);
  3. hub sam robi fuzję: same nowe punkty → ciche „Podejrzenie”, po usłyszeniu przez zaufany → alarm poziomu 1;
  4. trzy nadajniki najbliżej trasy wysyłają zgłoszenie ręczne (słyszę / widzę) z kierunkiem na drona;
  5. „koordynator” (ten skrypt, przez panel) po 2 zgłoszeniach ręcznych podnosi poziom 2, a po przelocie odwołuje alarm.
Na czas próby --trusted nadajników przy trasie dostaje status „zaufany”; na końcu wracają do „nowy”.

  (set -a; . ./hub.env; set +a; python3 sim_flight.py --delay 180)
"""
import argparse, json, math, os, random, time, urllib.request

from sim_phones import Phone, STATE

R_EARTH = 6371.0


def km_xy(a, b):
    """Przesunięcie b względem a w km: (wschód, północ)."""
    return ((b[1] - a[1]) * 111.32 * math.cos(math.radians(a[0])), (b[0] - a[0]) * 111.32)


def along(route, s_km):
    """Punkt na łamanej po s_km od startu; None, gdy za końcem."""
    for a, b in zip(route, route[1:]):
        dx, dy = km_xy(a, b)
        seg = math.hypot(dx, dy)
        if s_km <= seg:
            f = s_km / seg
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), math.degrees(math.atan2(dx, dy)) % 360
        s_km -= seg
    return None


def admin(path, obj):
    req = urllib.request.Request("http://127.0.0.1:8766" + path, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["SKYWATCH_ADMIN_TOKEN"]})
    with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())


def say(msg): print(time.strftime("%H:%M:%S"), msg, flush=True)


def main(a):
    route = [tuple(map(float, p.split(","))) for p in a.route]
    total = sum(math.hypot(*km_xy(p, q)) for p, q in zip(route, route[1:]))
    phones = [Phone(d, "http://127.0.0.1:8765", os.environ["SKYWATCH_TOKEN"]) for d in json.load(open(STATE))]
    phones = [p for p in phones if p.d.get("joined")]
    sensors = [p for p in phones if p.d["mode"] == "sensor"]

    def dist_to_route(p):
        return min(math.hypot(*km_xy(tuple(p.d["station"]), along(route, s)[0])) for s in [i * 0.2 for i in range(int(total / 0.2))])

    near = sorted(sensors, key=dist_to_route)
    heard = [p for p in near if dist_to_route(p) <= a.hear]
    # zaufane: z drugiej połowy listy po kolejności przelotu, żeby najpierw było widać ciche „Podejrzenie”
    def first_s(p):
        return min((s for s in [i * 0.2 for i in range(int(total / 0.2))]
                    if math.hypot(*km_xy(tuple(p.d["station"]), along(route, s)[0])) <= a.hear), default=1e9)
    by_time = sorted(heard, key=first_s)
    trusted = by_time[len(by_time) // 2: len(by_time) // 2 + a.trusted]
    reporters = {p.node: k for p, k in zip(sorted(heard, key=dist_to_route)[:3], ("see", "hear", "hear"))}
    say(f"trasa {total:.1f} km, {a.speed * 3.6:.0f} km/h → przelot {total / a.speed * 1000 / 60:.1f} min; "
        f"usłyszy {len(heard)} z {len(sensors)} czujników; zaufane na czas próby: "
        + ", ".join(f"SIM-{p.d['i']:02d}" for p in trusted))

    for p in trusted: admin("/admin/api/trust", {"code": p.node[:8], "trust": "trusted"})
    try:
        if a.delay: say(f"start za {a.delay} s"); time.sleep(a.delay)
        t0, last_det, reported, lvl2, done_rep = time.time(), {}, set(), False, 0
        say("START — dron wlatuje")
        while True:
            el = time.time() - t0
            pos = along(route, el * a.speed / 1000)
            if pos is None: break
            (lat, lon), hdg = pos
            vx, vy = a.speed * math.sin(math.radians(hdg)), a.speed * math.cos(math.radians(hdg))
            for p in heard:
                dx, dy = km_xy(tuple(p.d["station"]), (lat, lon))
                d = math.hypot(dx, dy)
                if d > a.hear or time.time() - last_det.get(p.node, 0) < a.every + random.uniform(-3, 3): continue
                last_det[p.node] = time.time()
                rng = math.hypot(d * 1000, a.alt)
                v_rad = (vx * dx + vy * dy) * 1000 / max(rng, 1)            # >0: oddala się
                f0 = a.f0 * 343 / (343 + v_rad)
                peak = p.floor + max(6, 30 - 20 * math.log10(max(rng, 100) / 100))
                code, out = p.event("detection", {
                    "method": "harmonic", "peak_db": round(peak, 1), "floor_db": round(p.floor, 1), "source": "UNPROCESSED",
                    "harm": {"windows": 6, "tonal_frac": 0.8, "f0_med": round(f0, 1), "stable_run_s": 6, "presence_8s": 7,
                             "shahed_like": True}})
                tag = " [ZAUFANY]" if p in trusted else ""
                say(f"  SIM-{p.d['i']:02d}{tag} słyszy: {d:.1f} km, ton {f0:.0f} Hz → {code}" + (f" {out}" if code != 200 else ""))
                if p.node in reporters and p.node not in reported and d < 1.2:
                    reported.add(p.node)
                    az = math.degrees(math.atan2(dx, dy)) % 360
                    elv = math.degrees(math.atan2(a.alt, d * 1000))
                    code, out = p.event("manual", {"kind": reporters[p.node], "az": round(az), "el": round(elv), "compass_acc": 2})
                    say(f"  SIM-{p.d['i']:02d} zgłasza ręcznie „{'widzę' if reporters[p.node] == 'see' else 'słyszę'}”, "
                        f"kierunek {az:.0f}°, {elv:.0f}° nad horyzontem → {code}")
                    done_rep += 1
            if done_rep >= 2 and not lvl2:
                lvl2 = True
                al = admin("/admin/api/alert", {"level": 2, "text": "TEST (symulacja): dron potwierdzony przez obserwatorów, "
                                                f"leci na kierunek {hdg:.0f}°. Schowaj się, nie wychodź na zewnątrz."})
                say(f"  KOORDYNATOR: poziom 2 (alarm #{al['id']})")
            time.sleep(1)
        say("KONIEC przelotu — dron poza zasięgiem")
        time.sleep(30)
        al = admin("/admin/api/alert", {"cancel": True, "text": "TEST zakończony: odwołanie alarmu."})
        say(f"KOORDYNATOR: odwołanie (alarm #{al['id']})")
    finally:
        for p in trusted: admin("/admin/api/trust", {"code": p.node[:8], "trust": "new"})
        say("zaufane z próby wróciły do „nowy”")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", nargs="+", default=["52.2400,21.2000", "52.2330,21.0000", "52.2250,20.8500"],
                    help="punkty trasy lat,lon")
    ap.add_argument("--speed", type=float, default=50, help="m/s (Shahed ~ 50)")
    ap.add_argument("--alt", type=float, default=150, help="m nad ziemią")
    ap.add_argument("--hear", type=float, default=2.5, help="km: zasięg słyszalności")
    ap.add_argument("--every", type=float, default=15, help="s między wykryciami jednego czujnika")
    ap.add_argument("--f0", type=float, default=110, help="Hz: ton podstawowy silnika")
    ap.add_argument("--trusted", type=int, default=2)
    ap.add_argument("--delay", type=int, default=0, help="s czekania przed startem")
    main(ap.parse_args())

#!/usr/bin/env python3
"""
SkywatchPL / node / vision — naziemne wykrywanie dronów na niebie z kamery
(telefon z IP Webcam / RTSP / plik wideo). Tylko wykrywanie i alarm.

Tor: klatka → YOLO (ONNX, CPU) → śledzenie między klatkami → potwierdzenie (N trafień w oknie)
     → azymut/elewacja z ustawienia kamery → alarm na Telegram (zdjęcie + kierunek) + log jsonl.

Źródła:
  IP Webcam (Android):  http://<ip-telefonu>:8080/video     (MJPEG)
  RTSP:                 rtsp://<ip>:8080/h264_ulaw.sdp
  plik:                 nagranie.mp4

Przykład:
  python3 skywatch.py --src http://192.168.1.50:8080/video --heading 180 --tilt 20 --notify

Alarmy (--notify), do wyboru:
  • Telegram bot:  zmienne środowiskowe SKYWATCH_TG_TOKEN i SKYWATCH_TG_CHAT
  • dowolna komenda: --notify-cmd 'moj_skrypt.sh {msg} {image}'   ({msg}, {image} zostaną podstawione)

Licencja: Apache-2.0
"""
import argparse, json, math, os, shlex, subprocess, threading, time, uuid, urllib.request
from datetime import datetime
import cv2, numpy as np, onnxruntime as ort

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--model", default=os.path.join(HERE, "models/detector.onnx"))
ap.add_argument("--conf", type=float, default=0.35, help="próg pewności pojedynczej detekcji")
ap.add_argument("--confirm", type=int, default=3, help="ile trafień w oknie potwierdza obiekt")
ap.add_argument("--window", type=int, default=6, help="okno (klatek) do potwierdzenia")
ap.add_argument("--cooldown", type=float, default=60, help="min. sekund między alarmami")
ap.add_argument("--heading", type=float, default=0, help="azymut osi kamery [°], 0 = północ, 90 = wschód")
ap.add_argument("--tilt", type=float, default=15, help="pochylenie osi kamery nad horyzont [°]")
ap.add_argument("--hfov", type=float, default=66, help="poziomy kąt widzenia kamery [°] (typowy telefon: 60–70)")
ap.add_argument("--notify", action="store_true", help="wysyłaj alarmy (bez tego: tylko log)")
ap.add_argument("--notify-cmd", help="komenda alarmu z {msg} i {image}, zamiast bota Telegram")
ap.add_argument("--max-frames", type=int, default=0)
ap.add_argument("--outdir", default=os.path.join(HERE, "alerts"))
a = ap.parse_args()
os.makedirs(a.outdir, exist_ok=True)

so = ort.SessionOptions(); so.intra_op_num_threads = os.cpu_count(); so.inter_op_num_threads = 1
sess = ort.InferenceSession(a.model, so, providers=["CPUExecutionProvider"])
names = eval(sess.get_modelmeta().custom_metadata_map.get("names", "{0: 'drone'}"))


def detect(img):
    h, w = img.shape[:2]; s = 640 / max(h, w)
    nh, nw = int(round(h * s)), int(round(w * s)); px, py = (640 - nw) // 2, (640 - nh) // 2
    canvas = np.full((640, 640, 3), 114, np.uint8)
    canvas[py:py + nh, px:px + nw] = cv2.resize(img, (nw, nh))
    x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
    out = sess.run(None, {"images": x})[0][0]
    return [(np.array([(x1 - px) / s, (y1 - py) / s, (x2 - px) / s, (y2 - py) / s]), float(sc), int(c))
            for x1, y1, x2, y2, sc, c in out[out[:, 4] >= a.conf]]


# ─── źródło: osobny wątek zawsze trzyma NAJNOWSZĄ klatkę (strumień na żywo nie może się kolejkować) ───
class Source:
    def __init__(s, src):
        s.src, s.live = src, "://" in src
        s.cap = cv2.VideoCapture(src); s.frame = None; s.n = 0; s.ok = True
        if s.live: threading.Thread(target=s._loop, daemon=True).start()

    def _loop(s):
        while True:
            ok, f = s.cap.read()
            if ok: s.frame, s.n = f, s.n + 1
            else:
                print(f"[{now()}] brak obrazu — ponowne łączenie za 5 s"); time.sleep(5)
                s.cap.release(); s.cap = cv2.VideoCapture(s.src)

    def read(s):
        if not s.live:
            ok, f = s.cap.read(); s.ok = ok; return f if ok else None
        while s.frame is None: time.sleep(0.05)
        f, s.frame = s.frame, None
        return f


# ─── proste śledzenie: łączenie detekcji z torami po odległości środków ───
class Track:
    ids = 0

    def __init__(s, box, sc, c):
        Track.ids += 1; s.id = Track.ids
        s.box, s.best, s.cls = box, sc, c; s.hist = [1]; s.alerted = False; s.path = [center(box)]


def center(b): return np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])


def update(tracks, dets, diag):
    used = set()
    for t in tracks:
        cands = [(np.linalg.norm(center(d[0]) - center(t.box)), i) for i, d in enumerate(dets) if i not in used]
        cands = [c for c in cands if c[0] < 0.08 * diag]
        if cands:
            _, i = min(cands); used.add(i)
            t.box, t.best = dets[i][0], max(t.best, dets[i][1]); t.hist.append(1); t.path.append(center(t.box))
        else:
            t.hist.append(0)
        t.hist = t.hist[-a.window:]
    tracks += [Track(*d) for i, d in enumerate(dets) if i not in used]
    return [t for t in tracks if any(t.hist)]


def angles(cx, cy, w, h):
    f = (w / 2) / math.tan(math.radians(a.hfov) / 2)
    az = (a.heading + math.degrees(math.atan((cx - w / 2) / f))) % 360
    el = a.tilt - math.degrees(math.atan((cy - h / 2) / f))
    return az, el


def compass(az): return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((az + 22.5) // 45) % 8]
def now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def alert(frame, t, fno, dets):
    h, w = frame.shape[:2]
    cx, cy = center(t.box); az, el = angles(cx, cy, w, h)
    img = frame.copy()
    for b, sc, _ in dets:                          # pozostałe obiekty w kadrze — cienka ramka
        b = b.astype(int); cv2.rectangle(img, (b[0] - 8, b[1] - 8), (b[2] + 8, b[3] + 8), (0, 200, 255), 1)
    x1, y1, x2, y2 = t.box.astype(int)
    pad = 12
    cv2.rectangle(img, (x1 - pad, y1 - pad), (x2 + pad, y2 + pad), (0, 0, 255), 2)
    cv2.putText(img, f"{names.get(t.cls, t.cls)} {t.best:.2f}", (x1 - pad, max(18, y1 - pad - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(a.outdir, f"alert_{ts}_t{t.id}.jpg"); cv2.imwrite(path, img)
    rec = dict(time=now(), frame=fno, track=t.id, cls=names.get(t.cls, t.cls), conf=round(t.best, 2),
               azimuth=round(az), dir=compass(az), elevation=round(el, 1), image=path)
    with open(os.path.join(a.outdir, "alerts.jsonl"), "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    msg = (f"🛑 Skywatch: wykryto obiekt ({rec['cls']}, pewność {rec['conf']}), w kadrze: {len(dets)}\n"
           f"🕒 {rec['time']}\n🧭 kierunek {rec['azimuth']}° ({rec['dir']}), {rec['elevation']}° nad horyzontem")
    print(f"[{rec['time']}] ALARM track#{t.id} conf={rec['conf']} az={rec['azimuth']}° ({rec['dir']}) el={rec['elevation']}° -> {path}")
    if a.notify:
        threading.Thread(target=notify, args=(msg, path), daemon=True).start()


def notify(msg, image):
    try:
        if a.notify_cmd:
            cmd = [c.replace("{msg}", msg).replace("{image}", image) for c in shlex.split(a.notify_cmd)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            return
        token, chat = os.environ.get("SKYWATCH_TG_TOKEN"), os.environ.get("SKYWATCH_TG_CHAT")
        if not (token and chat):
            print("  (brak SKYWATCH_TG_TOKEN/SKYWATCH_TG_CHAT — alarm tylko w logu)"); return
        b = uuid.uuid4().hex
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n"
                f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{msg}\r\n"
                f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"alert.jpg\"\r\n"
                f"Content-Type: image/jpeg\r\n\r\n").encode() + open(image, "rb").read() + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", body,
                                     {"Content-Type": f"multipart/form-data; boundary={b}"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print(f"  błąd wysyłki alarmu: {e}")


def main():
    src = Source(a.src); tracks = []; last_alert = 0.0; fno = 0; t0 = time.time()
    print(f"[{now()}] skywatch start: {a.src} | model {os.path.basename(a.model)} | kamera az {a.heading}°, tilt {a.tilt}°, HFOV {a.hfov}°"
          f" | alarmy {'włączone' if a.notify else 'tylko log'}")
    while True:
        f = src.read()
        if f is None: break
        fno += 1
        dets = detect(f)
        tracks = update(tracks, dets, math.hypot(*f.shape[:2]))
        for t in tracks:
            if not t.alerted and sum(t.hist) >= a.confirm:
                t.alerted = True
                if time.time() - last_alert >= a.cooldown:
                    alert(f, t, fno, dets); last_alert = time.time()
        if fno % 100 == 0:
            print(f"[{now()}] {fno} klatek, {fno / (time.time() - t0):.1f} FPS, aktywne tory: {len(tracks)}")
        if a.max_frames and fno >= a.max_frames: break


if __name__ == "__main__":
    main()

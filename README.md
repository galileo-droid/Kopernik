# SkywatchPL

**Otwarty, tani zestaw do wykrywania dronów na niebie — do zbudowania samemu.**
Słucha (mikrofon) i patrzy (kamera), a gdy wykryje drona, wysyła alarm z kierunkiem.

> **Tylko wykrywanie i alarm.** Żadnego zakłócania, przechwytywania, naprowadzania ani przeciwdziałania — nigdy.
> Zobacz [ZASADY.md](ZASADY.md).

*English summary below.*

## Dlaczego

Drony uderzeniowe typu Shahed/Geran lecą nisko, nocą, z charakterystycznym dźwiękiem silnika jak motorower.
Radar to setki tysięcy złotych. Mikrofon i stary telefon — prawie zero. Sieć wielu tanich punktów
potrafi dać wczesne ostrzeżenie tam, gdzie nie ma nic innego (tak działa m.in. ukraiński system Sky Fortress).

## Jak to działa

```
 mikrofon ─► rozpoznawanie dźwięku silnika ─┐
                                             ├─► potwierdzenie ─► alarm (Telegram / dowolna komenda) + dziennik
 kamera ──► YOLO ─► śledzenie w kadrze ─────┘         │
                                                       └─► kierunek (azymut) i wysokość kątowa
```

- **Dźwięk jest podstawą:** słychać dalej niż widać, działa w nocy i przez chmury.
- **Kamera potwierdza** i daje zdjęcie. Tania kamera zobaczy obiekt wielkości Shaheda realnie z ok. 300–500 m.
- **Kilka punktów** → z przecięcia kierunków da się wyznaczyć przybliżone położenie (planowane).

## Status

| moduł | stan |
|---|---|
| `node/vision` — kamera + YOLO + śledzenie + azymut + alarm | ✅ działa (testy na nagraniach) |
| `node/audio` — rozpoznawanie dźwięku silnika | 🚧 w przygotowaniu |
| model wizyjny na otwartej licencji | 🚧 do wytrenowania (zob. [models](node/vision/models/README.md)) |
| sieć wielu punktów / wyznaczanie położenia | 📋 koncepcja: [docs/siec.md](docs/siec.md) |
| `app/android` + `server` — aplikacja-czujnik i serwer grupy | 🧪 MVP 0.1 do testów: [docs/aplikacja.md](docs/aplikacja.md) |

## Szybki start (telefon, 0 zł)

Zobacz [docs/start-telefon.md](docs/start-telefon.md). Koncepcja punktu „tylko telefon”: [docs/punkt-telefon.md](docs/punkt-telefon.md). Sprzęt dla stałego punktu: [docs/sprzet.md](docs/sprzet.md).

## Pokrewne projekty

- [VolAnti](https://github.com/agamrossen/VolAnti) — otwarty akustyczny detektor małych dronów wielowirnikowych (ESP32, LoRa).
  SkywatchPL celuje w lukę obok: drony ze spalinowym silnikiem (Shahed/Geran) i potwierdzenie kamerą.
  Licencja Apache-2.0 wybrana m.in. dla zgodności z VolAnti.

## Licencja

Kod: [Apache-2.0](LICENSE). Dokumentacja: CC BY-SA 4.0. Modele: zob. licencje danych w `node/vision/models/README.md`.

---

## English summary

SkywatchPL is an open, low-cost, DIY sky-watch kit: a microphone and a camera (old Android phone or Raspberry Pi)
that detect drones — primarily engine-driven one-way attack drones such as Shahed/Geran — and send an alert
with bearing and elevation. **Detection and alert only. No jamming, interception, targeting or countermeasures, ever.**
Code under Apache-2.0.

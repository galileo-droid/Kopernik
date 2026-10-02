# node/audio — rozpoznawanie dźwięku silnika (w przygotowaniu)

Plan:
1. Wejście: mikrofon USB / mikrofon telefonu (strumień audio z IP Webcam) — 16 kHz mono.
2. Okna 1 s z nakładaniem → mel-spektrogram → mały klasyfikator (CNN) na CPU / ESP32-S3 / Raspberry Pi.
3. Klasy: silnik drona spalinowego (Shahed/Geran), małe drony wielowirnikowe, tło (ruch uliczny, motocykle, kosiarki, samoloty, wiatr, deszcz).
4. Alarm dopiero po kilku kolejnych trafieniach; pierwszy sygnał „słychać” → kamera szuka w obrazie.

Kluczowe ryzyko: **dane**. Motocykle i skutery brzmią podobnie — potrzeba dużo nagrań tła,
a nagrania Shahedów muszą mieć licencję pozwalającą na użycie w otwartym projekcie.

## Stan (30.09.2026): detektor harmoniczny

Zamiast sieci neuronowej na start — fizyka: silnik tłokowy na stałych obrotach daje w widmie
grzebień harmonicznych k·f0. `harmonic.py` (Python, wzorzec) = `Harmonic.kt` w aplikacji (od 0.4).
Okno 1 s → wybielone log-widmo → najlepsze f0 w 40–250 Hz i siła grzebienia (> 1.2 = ton).

Wstępny test na nagraniach z YouTube (mało, brudne — tylko wskazówka):
- Shahed: f0 ≈ 92–122 Hz, ton w 73–98 % okien, f0 zmienia się < 2 %/s;
- motocykle/skutery: ton w 4–50 % okien, f0 skacze 5–67 %/s;
- mylący: samolot tłokowy (stabilny ton, ale niższy — Cessna 421 ≈ 73 Hz).

Aplikacja 0.4 dołącza podsumowanie (`harm`) do każdego wykrycia i sama nagrywa przy stabilnym
tonie ≥ 8 s (`method: harmonic`). Na razie **bez alarmu** — zbieramy odsetek fałszywych trafień.

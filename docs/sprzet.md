# Sprzęt

Ceny orientacyjne (PLN, 2026), do weryfikacji przy zakupie.

| poziom | sprzęt | koszt | analiza |
|---|---|---|---|
| **0 – na start** | stary telefon z Androidem + aplikacja IP Webcam + ładowarka | 0–300 zł | na serwerze / komputerze w domu |
| **1 – samodzielny punkt** | Raspberry Pi 5 (4 GB) + Camera Module 3 + mikrofon USB + obudowa + zasilacz | ok. 600–800 zł | na miejscu, wysyła tylko alarmy |
| **2 – mocniejszy** | poziom 1 + moduł AI Hailo-8L + drugi, węższy obiektyw w kierunku typowych nadlotów | ok. 1200–1500 zł | na miejscu, pełna prędkość, większy zasięg kamery |

## Dlaczego zasięg kamery jest mały — liczymy

Rozpiętość Shaheda ≈ 2,5 m. Telefon: kąt widzenia ≈ 66° na 1920 px → ≈ 0,034°/px.
Z 1 km obiekt ma kątowo 2,5/1000 rad ≈ 0,14° → **≈ 4 px**. Detektor potrzebuje ~10+ px →
realny zasięg szerokiego obiektywu ≈ **300–500 m**. Węższy obiektyw (np. 25°) wydłuża zasięg ~2,5×,
ale widzi mniejszy wycinek nieba. Dlatego podstawą jest dźwięk, a kamera — potwierdzeniem.

## Montaż (ogólne wskazówki)

- Kamera: stabilnie, osłonięta od deszczu, skierowana w niebo (15–30° nad horyzont), w stronę typowych nadlotów.
- Mikrofon: z dala od wentylatorów, klimatyzacji i ulicy; osłona przeciwwietrzna (gąbka / „futerko”).
- Zasilanie: stałe (ładowarka / PoE). Telefon — wyłączyć optymalizację baterii dla IP Webcam.

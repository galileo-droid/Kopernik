# Aplikacja Android + serwer grupy — MVP 0.1

Pierwsza wersja do testów, zakres z [punkt-telefon.md](punkt-telefon.md) sekcja 7.1 („Minimum do pierwszego testu”).

## Co jest

**Aplikacja** (`app/android`, Kotlin, bez bibliotek AndroidX, minSdk 29, target 36 / Android 16):
- nasłuch w tle (usługa pierwszoplanowa, źródło `UNPROCESSED`, jeśli telefon je ma, inaczej `VOICE_RECOGNITION`);
- bez modelu: głośność po filtrze 80 Hz względem śledzonego tła; ≥2 s o próg (domyślnie 10 dB) głośniej → nagranie WAV 16 kHz (5 s wstecz, do 25 s) + wykrycie wysłane na serwer;
- puls „żyję” co 60 s (bateria, ładowanie, tło, źródło, model telefonu, pozycja zaokrąglona do ~100 m);
- odbiór alarmów (odpytywanie co 4 s); poziom 1 = powiadomienie, poziom 2–3 = pełny ekran + dźwięk budzika (strumień alarmowy, słychać przy „nie przeszkadzać”, jeśli alarmy są w nim dozwolone), odwołanie wycisza;
- po alarmie: „potwierdzam” / „to nie dron” → informacja zwrotna na serwer;
- zgłoszenie ręczne: celujesz tylnym aparatem, azymut od północy geograficznej (poprawka deklinacji), kąt nad horyzontem, pozycja;
- kolejka zdarzeń, gdy nie ma łączności; nagrania wysyłane tylko przyciskiem.

**Serwer** (`server/hub.py`, sam Python): przyjmuje zdarzenia i nagrania, dziennik w `server/data/*.jsonl`, alarmy z CLI (`hub.py alert --level 2 --text …`, `hub.py alert --cancel`).
Fuzja MVP: ≥2 różne punkty w oknie 60 s (wykrycia) / 120 s (zgłoszenia ręczne) → alarm poziomu 1. Poziomy 2–3 tylko ręcznie.

## Aktualizacje (od 0.2)

Serwer wystawia `server/dist/skywatch.apk` + `version.json` (`server/publish_apk.sh` buduje i wystawia). Aplikacja sprawdza co ~30 min i przy każdym otwarciu; instaluje przez PackageInstaller. Za pierwszym razem trzeba zezwolić na „instalowanie nieznanych aplikacji” i potwierdzić; potem aplikacja jest instalatorem samej siebie i Android 12+ może aktualizować bez pytania. Wszystkie wersje muszą być podpisane tym samym kluczem.

## Świadome uproszczenia (do zmiany)

| MVP | docelowo |
|---|---|
| HTTP + odpytywanie | MQTT (push), potem Meshtastic |
| wspólny token grupy, HTTP bez TLS | klucze na telefonie, podpisy, TLS / tunel |
| próg głośności | model LiteRT (klasy: Shahed, skuter, samolot, …) |
| adres serwera wpisywany ręcznie | dołączenie kodem QR od koordynatora |
| brak startu po restarcie telefonu | Android 14+ nie pozwala uruchomić mikrofonu z tła — trzeba otworzyć aplikację |

## Budowanie

```
. /home/openclaw/opt/android-env.sh
cd app/android && gradle assembleDebug     # APK: app/build/outputs/apk/debug/app-debug.apk
```
Domyślny adres i token można wstrzyknąć przez `app/android/local.properties` (`skywatch.url=…`, `skywatch.token=…`, poza repo).

Serwer: `SKYWATCH_TOKEN=… python3 server/hub.py --port 8765`.

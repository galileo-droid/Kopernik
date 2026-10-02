# Punkt = sam telefon — co da się wycisnąć

> **Status:** burza mózgów, 30.09.2026. Założenie: uczestnik ma **tylko smartfon**, bez komputera, bez Raspberry Pi, bez radia.
> Uzupełnia [siec.md](siec.md). **❓** = pytanie otwarte, **✅** = propozycja do zatwierdzenia.

---

## 1. Co telefon ma na pokładzie

| element | do czego się przyda | ograniczenia |
|---|---|---|
| **mikrofon** (zwykle 2–3) | główny czujnik: rozpoznawanie dźwięku silnika | tłumienie szumów w systemie „wycina” buczenie silnika (zob. 3.2). Słabe niskie tony poniżej ~100 Hz |
| **procesor / NPU** | klasyfikator dźwięku lokalnie, na telefonie | bateria przy pracy ciągłej |
| **GPS** | pozycja punktu, **dokładny czas** | w budynku gorzej, ale pozycję wystarczy ustalić raz |
| **kompas + żyroskop** | kierunek, w który wskazuje telefon (zgłoszenie ręczne, kamera) | kompas myli się przy metalu i magnesach, trzeba go skalibrować |
| **aparat** | potwierdzenie w dzień, zdjęcie | w nocy prawie bezużyteczny. Mały cel widać realnie z kilkuset metrów |
| **LTE / Wi-Fi** | wysyłanie komunikatów do sieci | przy ataku może paść |
| **Bluetooth / Wi-Fi Direct** | awaryjna łączność telefon–telefon | zasięg dziesiątki metrów |
| **głośnik, latarka, wibracja** | alarm dla właściciela i otoczenia | — |
| **człowiek obok telefonu** | najlepszy czujnik kierunku: ucho + ręka wskazująca | śpi, bywa w panice, myli skuter z dronem |

---

## 2. Dwa rodzaje telefonów w sieci

### 2.1 Telefon-czujnik (stacjonarny) ✅ główna rola
Stary telefon z szuflady, **stale na ładowarce**, na balkonie, parapecie przy uchylonym oknie albo w skrzynce na zewnątrz.
- Nasłuchuje 24/7, sam rozpoznaje dźwięk i wysyła wykrycia.
- Opcjonalnie aparat w niebo (w dzień).
- To jest właściwy „punkt” z [siec.md](siec.md).

### 2.2 Telefon osobisty (w kieszeni)
W kieszeni ciągły nasłuch nie ma sensu: tarcie materiału, bateria, szum. Ale taki telefon może:
- **odbierać alarmy** z sieci (to ma każdy uczestnik);
- **zgłaszać ręcznie** (zob. 4);
- **budzić się na żądanie:** gdy sieć ma alarm poziomu 2–3 i przewiduje trasę, telefony osobiste w pasie przed dronem przełączają się na kilka minut w tryb nasłuchu. Pomysł z burzy mózgów: **tanie zagęszczenie sieci tam, gdzie akurat trzeba**, bez kosztu baterii na co dzień. ❓ Czy nasłuch z kieszeni / ze stołu da coś sensownego? Do przetestowania.

---

## 3. Nasłuch na telefonie: fizyka i pułapki

### 3.1 Szyba to największy wróg
Zamknięte okno tłumi dźwięk o ok. 25–35 dB, a szyby zespolone jeszcze bardziej. Z grubsza: **każde −6 dB to o połowę mniejszy zasięg** (w wolnej przestrzeni).
Wniosek: telefon za zamkniętym oknem słyszy może kilka razy bliżej niż na zewnątrz. **Instrukcja montażu jest tak samo ważna jak model:** uchylone okno, balkon, a najlepiej wodoodporna skrzynka na zewnątrz i gąbka na mikrofon przeciw wiatrowi.

### 3.2 System „poprawia” dźwięk, czyli psuje nam dane
Android domyślnie włącza tłumienie szumów, automatyczne wzmocnienie i usuwanie echa. Buczenie silnika to dla takiego filtra typowy „szum do wycięcia”.
Wniosek: nagrywać przez źródło `UNPROCESSED` (a jeśli telefon go nie ma, przez `VOICE_RECOGNITION`), bez automatycznego wzmocnienia. **Tego nie da się ustawić w Termuxie ani w przeglądarce.** To jeden z głównych argumentów za własną aplikacją.

### 3.3 Klasyfikator
- Okna ~1 s, mel-spektrogram, mały model typu CNN (kilkaset kB) w LiteRT (dawniej TensorFlow Lite). Na współczesnym telefonie jedno okno to milisekundy, obciążenie procesora pojedyncze procenty.
- Wzorzec do podpatrzenia: **BirdNET**. To otwarta aplikacja, która rozpoznaje ptaki po głosie lokalnie na telefonie. Ten sam problem techniczny, rozwiązany i sprawdzony.
- Klasy tła, które muszą być w danych: skuter/motocykl, kosiarka, piła spalinowa, samochód, ciągnik, samolot, śmigłowiec, wiatr, deszcz, telewizor za ścianą.

### 3.4 Kierunek z samego telefonu
- Mikrofony w telefonie są ~10–15 cm od siebie. Przy dźwięku ~150 Hz fala ma 2,3 m, więc różnica czasu dotarcia to ułamki milisekundy. **Kierunku z tego praktycznie nie będzie**, co najwyżej „z lewej / z prawej”.
- Kierunek dają: **człowiek** (zgłoszenie ręczne), **aparat + kompas** (w dzień) albo **sieć** (kilka punktów, metoda A/C z [siec.md](siec.md)).

### 3.5 Czas
- Telefon zna czas z sieci komórkowej / NTP z dokładnością ~10–50 ms. **To wystarcza na metodę A** (kto słyszy i kiedy).
- ❓ Czy da się dojść do ~1 ms (metoda C) z samego telefonu: czas GPS z surowych pomiarów GNSS + znaczniki czasu z interfejsu audio (AAudio)? Ciekawe, ale na razie tylko pytanie badawcze.

### 3.6 Bateria i niezawodność
- Ciągły nasłuch + klasyfikacja zużywa rząd kilku % baterii na godzinę. **Telefon-czujnik musi być na ładowarce.** Przy braku prądu zasila go powerbank, co wystarcza na dzień–dwa.
- Android zabija aplikacje w tle (tryb Doze, „optymalizacja baterii”, u niektórych producentów wyjątkowo agresywnie: Xiaomi, Huawei, Samsung). Potrzebne: usługa pierwszoplanowa typu „mikrofon” ze stałym powiadomieniem, wyłączenie optymalizacji baterii i **puls „żyję”**, żeby sieć wiedziała, że punkt zamilkł.
- Długa praca na ładowarce w upale może doprowadzić do spuchnięcia baterii. W instrukcji: cień, wentylacja, limit ładowania do 80%, jeśli telefon ma taką opcję.

---

## 4. Zgłoszenie ręczne: człowiek jako czujnik ✅

Istniejące rozwiązanie: ukraińska aplikacja **ePPO**. Użytkownik słyszy lub widzi drona, **celuje telefonem w jego stronę i naciska przycisk**. Aplikacja wysyła pozycję GPS, kierunek z kompasu, kąt nachylenia i czas. Z wielu takich zgłoszeń wychodzi przecięcie kierunków.

U nas:
- Duży przycisk „Słyszę / widzę drona” → celujesz telefonem na dźwięk → przytrzymujesz → wysłane.
- Przy okazji telefon nagrywa kilka sekund dźwięku, a klasyfikator ocenia go od razu. Zgłoszenie ma wtedy wyższą wagę, jeśli dźwięk pasuje.
- Zgłoszenie ręczne liczy się w fuzji jak metoda B (kierunek), ale z **niższą wiarygodnością**, dopóki nie potwierdzą go inne punkty. Chroni to przed paniką i żartownisiami.
- To działa **nawet bez modelu** i od pierwszego dnia. Ludzie słyszą Shaheda z daleka i wskazują kierunek zaskakująco dobrze.

---

## 5. Alarm na telefonie

- Kanał powiadomień o najwyższym priorytecie, **przebijający tryb „nie przeszkadzać”**, z alarmem pełnoekranowym jak budzik. Android na to pozwala, ale wymaga zgody użytkownika.
- Treść: poziom, „w twojej okolicy / za ~2 min”, kierunek, z którego leci. **Bez współrzędnych** (ZASADY pkt 2).
- Telefon-czujnik może sam głośno zawyć, co działa jak lokalna syrena dla domowników i sąsiadów bez aplikacji.
- Informacja zwrotna: „to był skuter” / „potwierdzam”. Z tego powstają dane do douczania modelu i ocena wiarygodności punktów.

---

## 6. Łączność z samego telefonu

| droga | kiedy | uwagi |
|---|---|---|
| LTE / Wi-Fi → MQTT | na co dzień | ✅ podstawa |
| SMS | słaby zasięg, internet padł | tylko krótkie wykrycie/alarm. ❓ koszt, bramka |
| Bluetooth / Wi-Fi Direct / Wi-Fi Aware między telefonami | brak sieci komórkowej, telefony blisko siebie (blok, osiedle) | zasięg dziesiątki m, przekazywanie dalej jak w Bridgefy. ❓ realnie trudne na Androidzie w tle |
| dodatek: radio LoRa/Meshtastic przez Bluetooth (~100 zł) | tereny bez zasięgu, odporność | już nie „tylko telefon”, ale najtańsze ulepszenie |

---

## 7. Termux, przeglądarka czy własna aplikacja? → aplikacja

| | **Termux** (odrzucony) | **Przeglądarka (PWA)** | **Własna aplikacja Android** |
|---|---|---|---|
| instalacja dla zwykłego człowieka | trudna (F-Droid, komendy) | zero, link | APK / F-Droid / Sklep Play |
| nasłuch w tle 24/7 | kiepsko: `termux-microphone-record` nagrywa do plików, Android i tak ubija proces | ❌ tylko przy włączonym ekranie i otwartej karcie | ✅ usługa pierwszoplanowa |
| surowy dźwięk (bez tłumienia szumów) | ❌ | ❌ | ✅ |
| model lokalnie | możliwy (Python), ciężki | możliwy (WebAssembly / TF.js), tylko przy otwartej karcie | ✅ LiteRT, oszczędnie |
| GPS, kompas | przez `termux-api` | ✅ (z ograniczeniami) | ✅ |
| alarm przebijający „nie przeszkadzać” | ❌ | ❌ | ✅ |
| iPhone | ❌ | ✅ częściowo | osobna aplikacja (❓ później) |
| czas zrobienia | dni | dni–tygodnie | tygodnie–miesiące |

**Decyzja ✅ (30.09): stawiamy na własną aplikację Android.**
1. **Aplikacja Android (Kotlin, natywna)** to główny produkt, dla telefonu-czujnika i telefonu osobistego jednocześnie (zob. 7.1). Natywnie, a nie Flutter czy React Native, bo kluczowe rzeczy (usługa pierwszoplanowa z mikrofonem, surowe źródło dźwięku, AAudio, LiteRT, alarm pełnoekranowy) i tak wymagają kodu Androida. Otwarty kod, dystrybucja przez APK/F-Droid. ❓ Sklep Play ma wymagania dla aplikacji ze stałym mikrofonem: da się, ale z uzasadnieniem i jawnym powiadomieniem.
2. **Serwer** robi resztę: fuzję wykryć, koordynację sieci, a w fazie testów także analizę nagrań z telefonów (IP Webcam / dyktafon). Uwaga: takie nagrania mają systemowe tłumienie szumów (3.2), więc zmierzony w ten sposób zasięg jest **zaniżony**, to dolna granica.
3. ~~Termux~~: odrzucony (30.09), zbędny, skoro mamy serwer.
4. **Przeglądarka (PWA): odłożona.** Ewentualnie później jako lekkie wejście dla iPhone’ów (alarmy + zgłoszenie ręczne).
5. **iPhone: później.** iOS pozwala nagrywać w tle, ale Apple bywa restrykcyjne w przeglądach, a stare iPhone’y rzadziej leżą w szufladach.

### 7.1 Zakres aplikacji (szkic)

Jedna aplikacja, dwa tryby wybierane przy pierwszym uruchomieniu (można zmienić później):

**Tryb „Czujnik”** (stary telefon na ładowarce)
- nasłuch 24/7: usługa pierwszoplanowa, źródło `UNPROCESSED` / `VOICE_RECOGNITION`, okna 1 s → LiteRT;
- opcjonalnie aparat w niebo w dzień, z azymutem z kompasu (logika jak w `node/vision`);
- kreator montażu: test szumu tła („za głośno, przenieś telefon”), test mikrofonu, kalibracja kompasu, ostrzeżenie „jesteś za szybą”;
- puls „żyję” (bateria, temperatura, szum tła, stan czujników);
- lokalna syrena przy alarmie;
- tryb oszczędny przy zaniku zasilania.

**Tryb „Osobisty”** (telefon w kieszeni)
- odbieranie alarmów (pełnoekranowy, przebija „nie przeszkadzać”);
- przycisk „Słyszę / widzę drona”: celowanie + kompas + nagranie kilku sekund (sekcja 4);
- budzenie na żądanie: kilkuminutowy nasłuch, gdy sieć przewiduje trasę przez okolicę (❓ do przetestowania).

**Wspólne**
- dołączenie do grupy przez kod / QR od koordynatora, klucze generowane na telefonie;
- podpisywanie komunikatów, MQTT + kolejka offline (później Meshtastic przez Bluetooth);
- informacja zwrotna po alarmie („potwierdzam” / „to był skuter”) i wysyłka fragmentu nagrania tylko za zgodą;
- ekran stanu: „nasłuchuję / ostatnie wykrycie / sieć: 12 punktów aktywnych w grupie” (bez mapy punktów, zob. [siec.md](siec.md) sekcja 8);
- zbieranie danych diagnostycznych o zgodności telefonu (czy obsługuje `UNPROCESSED`, jak agresywnie producent ubija tło).

**Minimum do pierwszego testu (MVP)**
1. Nasłuch w tle z surowego źródła + zapis fragmentów po przekroczeniu progu głośności (jeszcze bez modelu).
2. Wysyłka wykryć i pulsu na serwer (MQTT).
3. Odbiór alarmu z serwera.
4. Zgłoszenie ręczne z kompasem.

Model dochodzi w drugim kroku, gdy zbierzemy nagrania. MVP samo w sobie jest narzędziem do ich zbierania.

---

## 8. Prywatność na telefonie

- Dźwięk analizowany w pamięci i od razu wyrzucany. Rozmowy nigdy nie opuszczają telefonu.
- Zapisywany jest tylko kilkusekundowy fragment wykrycia, lokalnie. Wysyłany do douczania **tylko po kliknięciu przez użytkownika**.
- Stała ikona / powiadomienie „SkywatchPL nasłuchuje” (Android i tak ją wymusza, i dobrze).
- Pozycja telefonu-czujnika jest przekazywana koordynatorowi raz, szyfrowana. Telefon osobisty wysyła pozycję tylko w zgłoszeniu ręcznym.

---

## 9. Pytania otwarte

- ❓ Realny zasięg: telefon na balkonie vs za szybą vs w skrzynce na zewnątrz. Pierwszy pomiar można zrobić legalnym źródłem o podobnym dźwięku (motoparalotnia, skuter 2T) i porównać z nagraniami Shahedów.
- ❓ Które telefony obsługują źródło `UNPROCESSED`? Lista zgodności budowana przez uczestników.
- ❓ Czy „budzenie na żądanie” telefonów osobistych daje coś przy nasłuchu z kieszeni / ze stołu?
- ❓ Web push jako kanał alarmowy: czy wystarczająco niezawodny, czy PWA tylko do zgłoszeń?
- ❓ Mesh Bluetooth/Wi-Fi między telefonami: warto czy od razu polecać radio LoRa?
- ❓ Dystrybucja aplikacji: F-Droid, Sklep Play, czy oba?

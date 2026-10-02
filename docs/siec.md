# Sieć SkywatchPL — koncepcja działania

> **Status:** szkic do dyskusji (burza mózgów, 30.09.2026). Nic z tego nie jest jeszcze zbudowane.
> Pytania otwarte oznaczone **❓**. Decyzje podjęte oznaczone **✅**.

---

## 1. Po co sieć

Jeden punkt słyszy Shaheda na ok. 1–3 km i nie wie, skąd on leci ani dokąd. Dopiero wiele punktów razem daje:

- **pewność:** to samo wykrycie z 2–3 punktów to prawie na pewno nie kosiarka ani skuter;
- **położenie i kurs:** z tego, które punkty i kiedy słyszą obiekt, da się wyznaczyć, gdzie jest i dokąd leci;
- **czas na reakcję:** ludzie dalej na trasie dostają ostrzeżenie *zanim* dron do nich doleci;
- **wiedzę o własnych lukach:** widać, które punkty nie działają i gdzie sieć jest ślepa.

### Liczby, na których stoi projekt

| wielkość | wartość | skutek |
|---|---|---|
| prędkość Shaheda | ok. 180 km/h ≈ 50 m/s | przelot 1 km ≈ 20 s |
| zasięg słyszalności (1 mikrofon, cicha noc) | ok. 1–3 km (❓ do zmierzenia) | punkty co 2–4 km dają ciągłe pokrycie |
| opóźnienie dźwięku | 343 m/s → z 2 km dźwięk idzie ~6 s | w tym czasie dron przelatuje ~300 m. **Słyszymy, gdzie dron *był*, a nie gdzie jest.** Obliczenia muszą to uwzględniać |
| czas ostrzeżenia | wykrycie 10 km przed miejscowością → ~3 min | sieć ma sens, gdy sięga *daleko przed* chroniony obszar |

---

## 2. Cele i nie-cele

**Cele**
- Wczesne ostrzeżenie dla uczestników sieci i ich otoczenia.
- Tanie punkty: stary telefon, Raspberry Pi, ESP32 z mikrofonem.
- Działanie bez internetu i przy awarii prądu.
- Otwarty, zrozumiały protokół, który każdy może zaimplementować.

**Nie-cele (twarde, zob. [ZASADY.md](../ZASADY.md))**
- ❌ Dane do naprowadzania czegokolwiek na cel, integracja z systemami przechwytującymi.
- ❌ Publiczna mapa na żywo.
- ❌ Zastępowanie RCB, syren i komunikatów służb.

---

## 3. Elementy systemu

```
  [punkt]  [punkt]  [punkt]  ...        ← czujniki: mikrofon, opcjonalnie kamera
      \       |       /
       \      |      /      komunikaty: wykrycie, puls „żyję”
        \     |     /
      [ warstwa transportu ]            ← MQTT (internet/LTE) i/lub LoRa-Meshtastic (radio)
              |
      [ łącznik / fuzja ]               ← łączy wykrycia, liczy położenie i kurs, nadaje poziom alarmu
              |
      [ powiadomienia ]                 ← Telegram, syrena/buzzer w punkcie, SMS, lokalny głośnik
```

### 3.1 Punkt (węzeł)
- Rozpoznaje dźwięk silnika, a jeśli ma kamerę, potwierdza obraz.
- Zna własną pozycję (GPS albo wpisaną ręcznie) i dokładny czas.
- Wysyła **krótkie** komunikaty. Nigdy nie wysyła surowego dźwięku ani zdjęć do sieci, najwyżej miniaturę przez internet.
- Umie zaalarmować lokalnie (buzzer, Telegram właściciela) nawet bez sieci.

### 3.2 Łącznik (fuzja)
- **❓ Centralny czy rozproszony?** Propozycja: oba.
  - *Centralny* (Raspberry Pi / serwer grupy): pełna fuzja, historia, panel dla koordynatora.
  - *Rozproszony*: każdy punkt słyszy przez radio komunikaty sąsiadów i sam liczy uproszczoną wersję. Gdy serwer padnie, sieć dalej ostrzega.
- Zadania: grupowanie wykryć w czasie i przestrzeni → wyznaczenie położenia → śledzenie kursu → ostrzeżenie dla stref na trasie.

### 3.3 Uczestnicy
- **Właściciel punktu:** dostaje alarmy ze swojego punktu i alarmy sieci dla swojej okolicy.
- **Koordynator grupy** (np. sołectwo, OSP, osiedle): przyjmuje nowe punkty, zarządza kluczami, widzi stan sieci.
- **❓ Służby:** czy i jak przekazywać im potwierdzone wykrycia (oficjalny kanał, a nie publiczny czat)?

---

## 4. Skąd wiadomo, gdzie jest dron

Tu jest najważniejszy wniosek z burzy mózgów: **jeden mikrofon nie daje kierunku.** Mówi tylko „słyszę” i jak głośno. Są trzy sposoby na położenie:

| metoda | co potrzeba | dokładność | koszt |
|---|---|---|---|
| **A. Kto słyszy i jak głośno** | pojedyncze mikrofony, czas z NTP/GPS (~0,1 s) | zgrubna, setki m – 1 km; wystarczy do kursu i ostrzeżeń | najtaniej ✅ na start |
| **B. Kierunek z każdego punktu** | 4 mikrofony w punkcie (różnica czasu dotarcia dźwięku) albo kamera | ±5–15°, przecięcie 2–3 kierunków daje punkt | średni |
| **C. Różnica czasu między punktami (TDOA)** | czas z GPS z dokładnością ~1 ms (moduł GPS z sygnałem PPS, ~40 zł) | najlepsza, dziesiątki m | wymaga GPS w każdym punkcie |

**Propozycja:** start z metodą **A** (działa z tym, co ludzie mają). **B** dla punktów „premium”. **C** później, jako rozwinięcie. We wszystkich trzech trzeba poprawiać na opóźnienie dźwięku (sekcja 1).

Kurs i prędkość wynikają z kolejności: punkt 1 → punkt 2 → punkt 3. Shahed leci zwykle prosto i ze stałą prędkością, co bardzo ułatwia przewidywanie.

---

## 5. Poziomy alarmu

| poziom | warunek | kto dostaje |
|---|---|---|
| **0 · Sygnał** | 1 punkt, niska pewność | tylko dziennik |
| **1 · Wykrycie** | 1 punkt, wysoka pewność albo dźwięk + kamera | właściciel punktu |
| **2 · Potwierdzone** | ≥ 2 niezależne punkty w oknie czasu zgodnym z prędkością drona | grupa w promieniu X km |
| **3 · Śledzone** | ≥ 3 wykrycia układające się w kurs | strefy na przewidywanej trasie, z szacowanym czasem („za ~2 min”) |
| **Odwołanie** | brak kolejnych wykryć na trasie przez N minut | ci sami, co dostali alarm |

❓ Progi X km, N minut, wymagana pewność: do ustalenia po testach.

---

## 6. Komunikaty (szkic protokołu)

Krótkie, żeby zmieściły się w jednym pakiecie LoRa (~200 bajtów). JSON przez MQTT, zwarta forma binarna przez radio.

**Wykrycie**
```json
{
  "v": 1, "typ": "wykrycie",
  "punkt": "sw-7f3a",
  "t": 1790000000.42,          // czas początku dźwięku, UTC
  "czujnik": "audio",          // audio | wizja
  "klasa": "silnik_spalinowy", // shahed | silnik_spalinowy | wielowirnikowiec | samolot | ...
  "pewnosc": 0.83,
  "glosnosc_db": 61,           // metoda A
  "azymut": null,              // metoda B, jeśli punkt umie
  "elewacja": null
}
```

**Puls „żyję”** (co 60 s): punkt, czas, bateria, stan czujników, poziom szumu tła (w hałasie punkt słyszy mniej, więc fuzja musi to wiedzieć).

**Pozycja punktu:** wysyłana **tylko przy dołączaniu do sieci**, do koordynatora, szyfrowana. Nie w każdym komunikacie (sekcja 8).

❓ Zgodność z formatem VolAnti: sprawdzić ich protokół LoRa i w miarę możliwości wspólną ramkę.

---

## 7. Transport

| warstwa | zalety | wady | rola |
|---|---|---|---|
| **MQTT** (Mosquitto) przez internet/LTE | proste, darmowe, duża przepustowość | pada razem z internetem/LTE | ✅ podstawowa na start |
| **LoRa / Meshtastic** | bez internetu, kilka km, punkty przekazują dalej, mały pobór prądu | wolne (kilka komunikatów/min), mało danych | zapas i tereny bez zasięgu |
| SMS | działa na słabym zasięgu | koszt, opóźnienia | ❓ tylko alarmy końcowe? |

Zasada: **punkt wysyła wszystkimi dostępnymi drogami**, a łącznik usuwa duplikaty.

---

## 8. Bezpieczeństwo — sieć sama jest celem

Burza mózgów pokazała cztery zagrożenia:

1. **Fałszywe alarmy wstrzyknięte przez kogoś** → panika i „wilk, wilk”, ludzie przestają reagować.
   *Obrona:* podpis każdego komunikatu kluczem punktu. Poziom 2+ wymaga niezależnych punktów. Limit komunikatów na punkt. Punkt, który często „widzi sam”, dostaje niższą wiarygodność.
2. **Mapa czujników w rękach przeciwnika** → trasy omijające sieć.
   *Obrona:* pozycje punktów zna tylko koordynator. W radiu komunikaty szyfrowane kluczem grupy. Brak publicznej listy punktów.
3. **Wyciek wykryć na żywo** → przeciwnik koryguje kolejne ataki (ZASADY pkt 2).
   *Obrona:* sieć zamknięta, alarm mówi „w twojej okolicy / za ~2 min”, a nie współrzędne. Historia z opóźnieniem i zgrubnie.
4. **Przejęty lub skradziony punkt.**
   *Obrona:* koordynator może unieważnić klucz punktu. Jeden punkt nie jest w stanie sam podnieść alarmu powyżej poziomu 1.

❓ Kto i jak przyjmuje nowych uczestników (weryfikacja osobista, zaproszenia)?

---

## 9. Odporność

- Zasilanie: powerbank / akumulator + panel. Punkt ma działać ≥ 24 h bez prądu z sieci.
- Każdy punkt alarmuje lokalnie nawet w całkowitej izolacji.
- Kolejka komunikatów: jeśli nie ma łącza, wysyła po odzyskaniu (z oryginalnym czasem).
- Puls „żyję” → mapa pokrycia dla koordynatora („punkt 7 milczy od 20 min”).

---

## 10. Prywatność

- Dźwięk analizowany na bieżąco w punkcie, rozmowy nie są przechowywane ani wysyłane.
- Do sieci trafiają tylko metadane wykrycia.
- Zachowywany jest tylko kilkusekundowy fragment dźwięku z wykrycia, lokalnie, do weryfikacji i douczania modelu, za zgodą właściciela.

---

## 11. Etapy

1. **Protokół + symulator:** wirtualne punkty i przelatujący dron, test fuzji i poziomów alarmu bez sprzętu.
2. **MQTT + łącznik centralny:** 2–3 prawdziwe punkty (telefony), metoda A.
3. **Moduł dźwiękowy w punktach** (osobne zadanie, `node/audio`).
4. **LoRa/Meshtastic** jako druga droga, fuzja rozproszona.
5. **Punkty premium:** 4 mikrofony / kamera (metoda B), GPS PPS (metoda C).
6. Test terenowy na nagraniach / przelotach legalnego źródła dźwięku (np. motoparalotnia).

---

## 12. Pytania otwarte (zbiorczo)

- ❓ Zasięg słyszalności Shaheda na tanim mikrofonie, w mieście i na wsi: skąd wziąć nagrania i jak zmierzyć?
- ❓ Fuzja centralna, rozproszona, czy obie?
- ❓ Jak gęsto trzeba rozstawić punkty dla metody A?
- ❓ Kto jest koordynatorem grupy i jak przyjmuje uczestników?
- ❓ Kanał do służb: czy w ogóle, a jeśli tak, to jaki oficjalny?
- ❓ Aspekty prawne w Polsce: rozpowszechnianie informacji o przelotach, nagrywanie dźwięku w przestrzeni publicznej.
- ❓ Wspólny format z VolAnti?
- ❓ Jak sieć ma ostrzegać ludzi spoza sieci (sąsiedzi bez aplikacji): lokalna syrena/głośnik w punkcie?

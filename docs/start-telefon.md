# Szybki start: stary telefon (poziom 0)

1. Na telefonie z Androidem zainstaluj **IP Webcam** i kliknij *Start server*. Zapamiętaj adres, np. `http://192.168.1.50:8080`.
2. Ustaw telefon stabilnie, obiektywem w niebo. Kompasem sprawdź, w którą stronę patrzy (np. 180° = południe) i ile jest uniesiony.
3. Na komputerze w tej samej sieci:
   ```bash
   pip install -r requirements.txt
   # model: zob. node/vision/models/README.md
   export SKYWATCH_TG_TOKEN=...   # token bota od @BotFather (opcjonalnie)
   export SKYWATCH_TG_CHAT=...    # twój chat id
   python3 node/vision/skywatch.py --src http://192.168.1.50:8080/video --heading 180 --tilt 20 --notify
   ```
4. Każdy alarm: zdjęcie z ramką, godzina, kierunek i wysokość kątowa. Dziennik: `node/vision/alerts/alerts.jsonl`.

Spoza sieci domowej (działka, LTE): połącz telefon i komputer darmowym [Tailscale](https://tailscale.com).

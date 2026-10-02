# Modele

`skywatch.py` domyślnie ładuje `models/detector.onnx` (YOLO, eksport ONNX z NMS, wyjście `[1, N, 6]`: x1, y1, x2, y2, score, class).

**Repo nie zawiera wag.** Każdy model w projekcie musi mieć jasną licencję — swoją i danych, na których się uczył.

| model | licencja | status |
|---|---|---|
| Hsu187/shahed-drone-detection (YOLO26n, 1 klasa) | **brak licencji** — tylko do lokalnych testów, nie do rozpowszechniania | używany w testach |
| własny SkywatchPL (klasy: shahed, samolot, śmigłowiec, ptak, mały dron) | Apache-2.0 + licencje danych (CC BY 4.0) | 🚧 do wytrenowania |

Planowane dane: publiczne zbiory z Roboflow Universe (CC BY 4.0), syntetyczne rendery (Kaggle `banderastepan/drone-detection`),
negatywy: ptaki / samoloty (np. Drone-vs-Bird). Trening: Google Colab (darmowe GPU), notatnik w `training/` (planowany).

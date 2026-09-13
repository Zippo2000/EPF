# Testspezifikation – EPF (e-paper ESP32 Frame)

**Version:** 1.0 · **Stand:** 2026-09-13
**Gegenstand:** Server/Backend (`app.py` + `cpy.so`) · Firmware (`Arduino/`) separat, siehe §9

---

## 1. Ziel & Geltungsbereich

Diese Spezifikation definiert die Tests für den **Python-Server** (`app.py` + `cpy.so`), der:

- Fotos aus **Immich** (v3 REST-API) holt,
- sie skaliert, mit **Atkinson-Dithering** zu einem 16-Level-Grayscale-Raster quantisiert,
- das Raster als C-Array (`.c`) an die ESP32-Firmware ausliefert,
- die Konfiguration via Web-UI (`/setting`) und `config.yaml` verwaltet,
- Schlaf-/Weck-Planung (`/sleep`) und Ladezustand (`batteryCap`-Header) behandelt.

### 1.1 In Scope
- Immich-v3-API-Contract (Album-, Asset-, Original-Endpoints)
- Download-Pipeline (Skalierung, Dithering, `.c`-Generierung)
- Konfigurationshandling (Defaults, Deep-Copy, Live-Reload, Persistenz)
- Web-UI `settings.html` (alle Return-Pfade)
- Schlafplan / Wake-up / Batterie-Logik
- Fehler- & Grenzfälle (negatives Testing)

### 1.2 Out of Scope (separater Plan)
- ESP32-E-Firmware (Wi-Fi-Captive-Portal, EPD-Refresh, Deep-Sleep-Aktualstrom)
- Hardware-Integrität (LiPo-Zyklus, EPD-Panel-Lebensdauer)
- Langzeit-Integration (Wochen+ Dauerbetrieb)

---

## 2. Testumgebung

| Komponente | Wert / Voraussetzung |
|---|---|
| Host-OS | Windows (Docker Desktop ≥ 4.x), alternativ Linux |
| Container-Runtime | Docker Engine ≥ 24, `docker compose` v2 |
| Immich-Server | **v3** (API liefert `assets` nicht mehr über `GET /api/albums/{id}`) |
| Immich-Erreichbarkeit | `http://<HOST>:2283`, vom Container aus erreichbar |
| Referenz-Server (Referenzlauf) | `http://<IMMICH_HOST>:2283`, Album `eink`, 42 Assets |
| Python | 3.9 (im Image), Flask 3.1, Pillow 11, rawpy 0.23 |
| Artefakt | `cpy.so` (committed, vorkompiliert – **nicht** ohne Cython-Rebuild ersetzen) |

### 2.1 Setup (vor jedem Testlauf)
```bash
# 1) API-Key bereitstellen (Fixture F2, §3)
cp .env.example .env            # IMMICH_API_KEY=... eintragen

# 2) Build + Start
docker compose up -d --build

# 3) Verifikation, dass die Instanz up ist
docker compose ps                       # STATUS = running, HEALTHY
docker compose logs epf | grep "Configuration updated"
```

### 2.2 Abbruch-Kriterium
Ein Test gilt als **FEHLGESCHLAGEN**, wenn
- der HTTP-Status von der erwarteten abweicht, **ODER**
- im Container-Log ein Traceback / `TypeError` / `UndefinedError` / `IndexError` erscheint, **ODER**
- ein Artefakt (`.c`, `config.yaml`, `tracking.txt`) inhaltlich unkonform ist.

---

## 3. Testdaten & Fixtures

| ID | Beschreibung | Verwendung |
|---|---|---|
| **F1** | Album `eink` (42 Assets, JPEG mit EXIF) | Haupt-Referenzalbum |
| **F2** | API-Key **mit** `asset.download`-Permission | happy-path Tests |
| **F3** | API-Key **ohne** `asset.download` (nur `album.read`/`asset.read`) | negativer Permission-Test |
| **F4** | Valides `config.yaml` (§3.1) | Standard |
| **F5** | Leeres `config.yaml` (0 Bytes) | `load_config`-Edge |
| **F6** | `config.yaml` nur Kommentar (`# x\n`) | `load_config`-Edge |
| **F7** | Inexistentes `config.yaml` | `load_config`-Exception-Pfad |
| **F8** | `tracking.txt` mit **allen** Album-IDs (Format §3.2) | „newest“-Guard |
| **F9** | 1×1-px-Testbild (JPEG / HEIC / RAW je eines) | Dithering-Regression |

### 3.1 Referenz-`config.yaml`
```yaml
immich:
  url: "http://<IMMICH_HOST>:2283"
  album: "eink"
  rotation: 270
  enhanced: 1.3
  contrast: 0.9
  strength: 0.8
  display_mode: "fill"
  image_order: "newest"
  sleep_start_hour: 23
  sleep_start_minute: 0
  sleep_end_hour: 6
  sleep_end_minute: 0
  wakeup_interval: 60
```

### 3.2 `tracking.txt`-Format (kritisch!)
```
<Zeile 1>      = Album-Name (z.B. "eink")
<Zeilen 2..n>   = eine Asset-ID pro Zeile (UUID)
```
> ⚠️ `load_downloaded_images()` vergleicht Zeile 1 mit dem aktuellen Album-Namen.
> Stimmt er nicht überein → leere Menge (Reset). Tests, die die „alle-bereits-gezeigt“-
> Situation brauchen, **müssen** den Album-Namen auf Zeile 1 setzen (vgl. TC-U03).

---

## 4. Kategorien & Priorität

| Präfix | Kategorie | Automatisierbar? |
|---|---|---|
| **U** | Unit (reine Python-Funktion, kein Netz) | ✅ pytest |
| **API** | Immich-v3-REST-Contract | ⚠️ Mock/Live-Server |
| **D** | Download-Pipeline (E2E) | ⚠️ Live-Server |
| **C** | Configuration / Persistenz | ✅ teils (Docker) |
| **S** | Settings-Web-UI | ✅ HTTP |
| **SL** | Sleep / Wake-up / Batterie | ✅ pytest (Zeit) |
| **N** | Negativ / Fehlerpfade | ✅/⚠️ |

**Priorität:** 🔴 P0 (Release-Blocker) · 🟠 P1 (wichtig) · 🟡 P2 (nice-to-have)

---

## 5. Testfälle

### A. Unit-Tests (Kategorie U)

#### TC-U01 · `deepcopy` isoliert `current_config` von `DEFAULT_CONFIG`
🔴 P0 · commit `c6ac86e` · ✅

- **Voraussetzung:** Modul `app` geladen.
- **Schritte:**
  1. `addr_a = id(current_config["immich"])`, `addr_b = id(DEFAULT_CONFIG["immich"])`.
  2. `current_config["immich"]["url"] = "http://changed"`.
  3. Lies `DEFAULT_CONFIG["immich"]["url"]`.
- **Erwartet:** `addr_a != addr_b`; `DEFAULT_CONFIG["immich"]["url"]` unverändert.
- **Pass:** Defaults nicht mutiert.
- **Referenz:** ✅ (shallow → `…99.99`, deepcopy → `192.168.1.10`).

#### TC-U02 · `load_config()` liefert niemals `None`
🔴 P0 · commit `05de873` · ✅

| Fall | Eingabe | Erwartet |
|---|---|---|
| a | leeres File (F5) | `DEFAULT_CONFIG` (Dict, **nicht** `None`) |
| b | nur Kommentar (F6) | `DEFAULT_CONFIG` |
| c | nicht existent (F7) | `DEFAULT_CONFIG` (via `except`) |
| d | valide Config (F4) | geparstes Dict, `["immich"]["url"]` = URL aus F4 |

- **Pass:** kein Fall liefert `None`; `isinstance(result, dict)` immer wahr.
- **Referenz:** ✅ (4/4).

#### TC-U03 · „newest“-Ordering: Guard bei leerer verbleibender Menge
🔴 P0 · commit `cac36d2` · ✅ (Mock `load_downloaded_images`)

- **Voraussetzung:** `image_order = "newest"`.
- **Szene:** alle Asset-IDs in `downloaded_images` → Filter-Menge = `[]`.
- **Schritte:** Asset-Liste (≥1) mocken; `downloaded_images` = {alle IDs}; „newest“-Branch ausführen.
- **Erwartet:** `remaining_images` ≠ ∅; `reset_tracking_file()` genau 1×; `remaining_images[0]` = neues Asset.
- **Negativ-Kontrolle:** Alt-Code ⇒ `IndexError`.
- **Pass:** kein `IndexError`; Element = neuestes Asset.
- **Referenz:** ✅ (E2E mit 42 IDs → HTTP 200, §7 TC-D01).

#### TC-U04 · „random“-Ordering: Guard bleibt erhalten (Regression)
🟠 P1 · ✅

- **Voraussetzung:** `image_order = "random"`, alle IDs in `downloaded_images`.
- **Erwartet:** `reset_tracking_file()` aufgerufen; `remaining_images` = Vollmenge; kein `IndexError`.
- **Pass:** kein `IndexError` (Pfad war bereits korrekt – darf nicht brechen).

#### TC-U05 · `calculate_battery_percentage()` – Monotonie & Rand
🟠 P1 · ✅

- **Schritte:** evaluier für `v ∈ {3300, 3400, 3600, 3800, 4000, 4200, 4300}`.
- **Erwartet:** `≤ 3400 → 0`, `≥ 4200 → 100`, dazwischen monoton steigend; Ergebnis ∈ `[0,100]`.
- **Pass:** Monotonie + Clamping.

#### TC-U06 · Schlaf-Fenster – TZ-Korrektheit
🟠 P1 · ✅ (`freezegun` / monkeypatch)

- **Voraussetzung:** `sleep_start=23:00`, `sleep_end=06:00`, `interval=60`, TZ=`Europe/Berlin`.
- **Erwartet:** 23:30 → SLEEP; 10:00 → nächster Slot 11:00; Fenster mit Wrap-around über Mitternacht zusammenhängend erkannt.
- **Pass:** Window-Logik inkl. Mitternacht-Übergang korrekt.
- **Begründung:** ohne `TZ` läuft alles in UTC (Motiv des `TZ`-Env in `docker-compose.yml`).

---

### B. Immich-v3-API-Contract (Kategorie API)

> Basis: `GET /api/albums/{id}` (liefert in v3 kein `assets`) → ersetzt durch paginiertes
> `POST /api/search/metadata`.

#### TC-API01 · Album-Auflösung über `GET /api/albums`
🔴 P0 · ⚠️

- **Schritte:** `GET {url}/api/albums` (mit `x-api-key`); suche `albumName == current_albumname`.
- **Erwartet:** 200, JSON-Array; genau 1 Match; `albumid` = dessen `id`.
- **Negativ (01n):** unbekannter Name → 404 `{"error":"Album not found"}`.
- **Pass:** `albumid` korrekt; unbekanntes Album → 404.

#### TC-API02 · Paginiertes Fetch via `POST /api/search/metadata`
🔴 P0 · ⚠️

- **Voraussetzung:** Album mit **> `size`** Assets (42, `size=1000`).
- **Schritte:** `POST /api/search/metadata` `{albumIds:[id], size:1000, page:1, withExif:true}`; `nextPage` folgen, bis leer.
- **Erwartet:** 200 pro Seite; gesammelte `items` = Vollmenge (42); Terminierung; jedes Asset trägt `id`, `originalFileName`, `exifInfo.dateTimeOriginal`.
- **Pass:** exakte Anzahl; Terminierung; EXIF vorhanden.
- **Referenz:** ✅ (5×`size=5`→25; `size=50`→42).

#### TC-API03 · Asset-Original-Download
🔴 P0 · ⚠️

- **Schritt:** `GET {url}/api/assets/{id}/original` (streamed).
- **Erwartet:** 200; `Content-Type: image/*`; nicht-leer; als Bild parsebar (PIL/rawpy).
- **Pass:** Bild lädt, Dimensionen > 0.

#### TC-API04 · Permission-Verweigerung (negativ)
🟠 P1 · Fixture F3 · ⚠️

- **Voraussetzung:** Key **ohne** `asset.download`.
- **Erwartet:** 403; Body `{"message":"Missing required permission: asset.download"}`; App leitet kontrolliert weiter (kein ungehanderter Crash).
- **Pass:** saubere 4xx/5xx + Log.
- **Referenz:** ✅ (403 beobachtet).

#### TC-API05 · `X-Photo-Url`-Header-Format
🟠 P1 · commit `ca4287f` · ⚠️

- **Schritt:** nach `/download` Header `X-Photo-Url` prüfen.
- **Erwartet:** `https://my.immich.app/albums/{albumId}/photos/{assetId}`, IDs konsistent mit der `.c`.
- **Pass:** Header vorhanden, Format + IDs stimmig.
- **Referenz:** ✅ (`…/photos/1d1d3364-…`).

---

### C. Download-Pipeline / E2E (Kategorie D)

#### TC-D01 · Kompletter Download-Lauf (happy path)
🔴 P0 · ⚠️

- **Voraussetzung:** F2, F4, Album F1.
- **Schritt:** `GET /download` mit `batteryCap: 3950`.
- **Erwartet:**
  - HTTP **200**;
  - `Content-Type: text/plain`, `Content-Disposition: attachment; filename=image_<assetId>.c`;
  - Body = kommasepariertes 16-Level-Raster (Werte ∈ 0..15) der Zielgröße;
  - Header `X-Photo-Url` (TC-API05);
  - kein Traceback im Log.
- **Pass:** 200 + wohlgeformtes `.c` + Header + log-frei.
- **Referenz:** ✅ (200, 588 003 Bytes, `image_1d1d3364…`).

#### TC-D02 · Dithering-Regression (Determinismus)
🟠 P1 · Fixture F9 · ✅

- **Schritt:** gleiches Quellbild 2× durch `convert_image_atkinson` → Bytevergleich.
- **Erwartet:** bit-gleich; keine NaN; Palette ⊂ {0..15}.
- **Pass:** identische Ausgaben; Wertebereich ok.
- **Zweck:** schützt die Atkinson-Integration (Fork-spezifisch, nicht im Original) vor Regression.

#### TC-D03 · Bildformat-Handling (RAW / HEIC / JPEG)
🟠 P1 · ⚠️

- **Schritt:** je ein Asset je Format (`.dng`, `.heic`, `.jpg`) durch die Pipeline.
- **Erwartet:** RAW via `rawpy.postprocess`, HEIC via `pillow_heif`, JPEG via PIL → RGB.
- **Negativ:** fehlerhaftes RAW → kontrollierter Fehler (kein Segfault).
- **Pass:** 3 Formate dekodiert; kaputte Datei → sauberer 500.

#### TC-D04 · Skalierungs-Modi `fit` vs `fill`
🟡 P2 · ✅

- **Schritt:** gleiches Bild mit `display_mode=fit` / `fill`.
- **Erwartet:** `fit` → Aspect-Ratio erhalten (Letterbox); `fill` → Crop/Stretch auf Zielaspect.
- **Pass:** unterschiedliche Geometrien; keine Exception.

---

### D. Konfiguration & Persistenz (Kategorie C)

#### TC-C01 · `docker-compose` verweigert Start ohne `IMMICH_API_KEY`
🔴 P0 · commit `6e9d7c7` · ✅

- **Schritt:** `docker compose up` **ohne** `.env`/Key.
- **Erwartet:** Interpolations-Fehler `required variable IMMICH_API_KEY is missing`; Container startet **nicht**.
- **Pass:** keine Instanz; eindeutige Meldung.
- **Referenz:** ✅ (beobachtet).

#### TC-C02 · Persistenz `config.yaml` über Container-Erneuerung
🔴 P0 · commit `6e9d7c7` · ✅

- **Schritte:** 1) `/setting`-Wert ändern + speichern; 2) `docker compose up -d --force-recreate`; 3) `/config/config.yaml` lesen.
- **Erwartet:** Einstellung **überlebt** (bind-mount); negativ: ohne Mount Rückfall zu Defaults.
- **Pass:** Wert nach Recreation == gesetzter Wert.

#### TC-C03 · Persistenz `tracking.txt` (Foto-Historie)
🟠 P1 · commit `6e9d7c7` · ✅

- **Schritt:** wie C02, prüfe `/photos/tracking.txt`.
- **Erwartet:** Album-Name + Historie erhalten (kein Reset).
- **Pass:** Historie intact.

#### TC-C04 · Live-Reload bei `config.yaml`-Änderung
🟠 P1 · ✅

- **Schritt:** laufender Container, `config.yaml` ändern (z. B. `rotation`).
- **Erwartet:** Watchdog feuert; Log `Configuration updated: …` (neue Werte); `/setting` reflektiert.
- **Pass:** Änderung ohne Neustart wirksam; kein Crash.

#### TC-C05 · `.gitignore`-Abdeckung
🟠 P1 · commit `602fe97` · ✅ (git)

- **Schritt:** `git status --porcelain` nach Anlegen von `.env`, `config/`, `photos/`, `__pycache__/`.
- **Erwartet:** keiner davon erscheint als zu-commiten.
- **Regression:** `cpy.so` **bleibt** tracked (darf NICHT ignoriert sein).
- **Pass:** Secrets/Rundlaufdaten draußen; `cpy.so` im Index.

---

### E. Settings-Web-UI (Kategorie S)

#### TC-S01 · Settings-Seite rendert mit Batterie-Info
🟠 P1 · ✅

- **Schritt:** `GET /setting`.
- **Erwartet:** 200; Batterie-Meter rendert; ohne Reading `0.0 %`/„no data“.
- **Pass:** 200; kein `UndefinedError` (`battery_voltage`/`battery_percentage` immer im Kontext).

#### TC-S02 · Ungültige Rotation → kontrollierter Fehler, kein 500
🔴 P0 · commit `9c01295` · ✅

- **Schritt:** `POST /setting` mit `rotation=45`.
- **Erwartet:** **200** mit „Rotation must be 0, 90, 180, or 270 degrees“; Batterie-Meter sichtbar.
- **Negativ (Alt-Code):** fehlende `battery_*` → Jinja `UndefinedError` → 500.
- **Pass:** 200 + Nachricht + Batterie; kein 500.
- **Referenz:** ✅ (200, kein Traceback).

#### TC-S03 · Save-Exception-Pfad rendert (kein 500)
🟠 P1 · commit `9c01295` · ⚠️ (read-only Mount)

- **Schritt:** `/config` read-only; `POST /setting` (gültige Werte).
- **Erwartet:** Schreib-Exception → renderndes „Error saving configuration: …“ mit Batterie; 200.
- **Pass:** kein 500; Meldung vorhanden.

#### TC-S04 · Gültiges Save → Redirect & Persist
🟠 P1 · ✅

- **Schritt:** `POST /setting` mit konsistenten, gültigen Werten.
- **Erwartet:** **302**; `config.yaml` aktualisiert; `update_app_config`-Log.
- **Pass:** 302 + Datei enthält neue Werte.
- **Referenz:** ✅ (302).

---

### F. Sleep / Wake-up / Batterie (Kategorie SL)

#### TC-SL01 · `/sleep` außerhalb des Fensters → nächster Slot
🟠 P1 · ✅

- **Voraussetzung:** `interval=60`, Zeit 10:00 (außerhalb 23–06).
- **Erwartet:** JSON `next_wakeup = 11:00`; `sleep_duration` konsistent.
- **Pass:** Round-up auf Intervallgrenze korrekt.

#### TC-SL02 · `/sleep` innerhalb des Fensters → SLEEP
🟠 P1 · ✅

- **Voraussetzung:** Zeit 23:30 (innerhalb 23–06).
- **Erwartet:** SLEEP-Signal; `sleep_duration > 0`.
- **Pass:** Wrap-around-Fenster als „asleep“ erkannt.

#### TC-SL03 · Batterie-Reading aus `batteryCap`
🟡 P2 · ✅

- **Schritt:** `GET /download` mit `batteryCap: 3950`; danach `GET /setting`.
- **Erwartet:** `last_battery_voltage = 3950`; `/setting` zeigt passenden %.
- **Negativ:** leerer/nicht-numerischer Header → kein Crash (bleibt 0).
- **Pass:** Reading gespeichert & ausgegeben; ungültiger Header toleriert.

#### TC-SL04 · Stale-Batterie-Timeout (1 h)
🟡 P2 · ✅ (fake clock)

- **Schritt:** `last_battery_update = now − 2 h`; `GET /setting`.
- **Erwartet:** Batterie = `0` (nicht verfügbar), nicht der alte Wert.
- **Pass:** 3600-s-Timeout greift.

---

## 6. Negative / Grenzwert-Tests (Kategorie N – Querschnitt)

> Konsolidiert alle „was geht schief“-Pfade; viele verweisen bereits auf die obigen Fälle.

| ID | Szenario | Ausgelöst über | Erwartet | Verknüpfung |
|---|---|---|---|---|
| TC-N01 | Unbekanntes Album | Config `album` = „föö“ | HTTP 404, `{"error":"Album not found"}` | TC-API01n |
| TC-N02 | Album ohne Bilder | leeres Album | HTTP 404, `{"error":"No images found in album"}` | TC-D01 |
| TC-N03 | Immich nicht erreichbar | Server down / falsche URL | kontrollierter 500 (keins Traceback-leak) | TC-API01/02 |
| TC-N04 | API-Key ungültig/rotiert | `x-api-key` → 401 | kontrollierter Fehler, kein Stacktrace in Body | TC-API04 |
| TC-N05 | Download-Fehler (Asset) | `.../original` → 5xx | kontrolliertes `{"error":"Failed to download image"}` (500), sauber | TC-D01 |
| TC-N06 | `config.yaml` halbgem (Write-in-flight) | Watchdog + Teil-Schreib | `load_config` → Defaults (keine `None`) | TC-U02 |
| TC-N07 | `batteryCap` = NaN/leer | `/download` | kein Crash, Batterie bleibt 0 | TC-SL03 |
| TC-N08 | `config.yaml` fehlt komplett | Container frisch, kein Mount | Defaults geladen, App startet | TC-U02 / C01 |
| TC-N09 | Zyklische `nextPage` (API-Bug) | Mock: `nextPage` wiederholt | Safety-Abort nach Max-Paginierung (optional) | TC-API02 |
| TC-N10 | Rotation=45 (ungültig) | `POST /setting` | 200 + Validierungs-Error, kein 500 | TC-S02 |

**Pass-Kriterium (global, Kategorie N):** In keinem der 10 Fälle erscheint ein ungehanderter
Stacktrace im HTTP-Body, und der Container bleibt danach funktionsfähig (nicht crash-loop).

---

## 7. Ausführung & CI-Integration

### 7.1 Laufvarianten

| Variante | Umfang | Dauer | Wann |
|---|---|---|---|
| **L0 – Smoke** | TC-U01..U02, TC-C01, TC-D01, TC-S02 | ~2 min | jeden Push (CI) |
| **L1 – Core** | alle P0 | ~10 min | Tag-/Merge-Lauf |
| **L2 – Voll** | alle P0+P1 (+P2) | ~30 min | Release |

### 7.2 Automatisierung

**Offline (kein Netz, kein Immich) – 12 Tests:**
```bash
sh scripts/run-tests.sh
```
Weil `app.py` das Cython-Modul `cpy.so` (Linux-ELF) importiert, läuft die Suite in einem
Linux-Container, den das Skript selbst baut und startet.
- `tests/test_battery.py` → TC-U05 (Spannung→%: Clamping + Monotonie)
- `tests/test_config.py` → TC-U01 (Deep-Copy-Isolation), TC-U02 (`load_config` liefert nie `None`)
- `tests/test_settings.py` → TC-S01 (GET rendert), TC-S02 (ungültige Rotation → 200, kein 500)
- `tests/test_sleep.py` → TC-SL01/SL02 (Struktur-/Kontrakt-Check von `/sleep`)

**Live (echter Immich-Server) – 4 Tests:**
```bash
sh scripts/run-live-tests.sh          # IMMICH_URL / IMMICH_ALBUM / IMMICH_API_KEY aus .env
# optional mit One-off-Override:
sh scripts/run-live-tests.sh http://<host>:2283 eink
```
- `tests/test_live.py` → TC-API01 (Album-Auflösung), TC-API02 (paginiertes `search/metadata`
  mit Vollständigkeits-Assertion), TC-API03 (Original-Download), TC-D01 + TC-API05 (komplette
  `/download`-Pipeline + `X-Photo-Url`). Die Tests skippen sauber, wenn kein `.env`/Server da ist.

> **Noch nicht automatisiert** (bleibt im manuellen Tier): TC-U03/U04 (Ordering-Guards),
> TC-C01–C04 (Compose-Persistenz / Live-Reload), TC-API04 (Permission-Verweigerung, braucht
> einen restriktiven Key), TC-D02–D04, TC-SL03/SL04.

### 7.3 Manueller Ad-hoc-Check (curl)

> Primärer automatisierter Live-Weg ist die pytest-Suite oben (`tests/test_live.py`).
> Dieser Harness dient als schneller manueller Check gegen einen *lokal laufenden*
> Server (Port 15001).
```bash
B=http://localhost:15001
# D01 – happy path
curl -s -D - -o /dev/null -H "batteryCap: 3950" $B/download | grep -E "HTTP|X-Photo-Url"
# S02 – invalid rotation
curl -s -o /dev/null -w "%{http_code}\n" -X POST $B/setting \
     -d "url=x&album=y&rotation=45&enhanced=1.3&contrast=0.9&strength=0.8\
&display_mode=fill&image_order=newest&sleep_start_hour=23&sleep_start_minute=0\
&sleep_end_hour=6&sleep_end_minute=0&wakeup_interval=60"
# SL01/02 – sleep
curl -s $B/sleep
# C01 – key missing => compose refuse
env -u IMMICH_API_KEY docker compose up --abort-on-container-exit 2>&1 | grep -i missing
```

---

## 8. Definition of Done (Release)

Ein Release gilt als testabgeschlossen, wenn:

1. **alle P0** (TC-U01, U02, U03, API01/02/03, D01, C01, C02, S02) = **PASS**;
2. **alle P1** = PASS **oder** explizit vertrackt (Known-Issue mit Ticket);
3. **globaler N-Guard:** kein ungehanderter Stacktrace in irgendeinem HTTP-Body (TC-N01..N10);
4. **Determinismus:** TC-D02 (Dithering) bit-stabil über 10 Wiederholungen;
5. **Persistenz:** TC-C02/C03 nach `--force-recreate` intakt;
6. **TZ:** TC-U06/SL01/SL02 bestätigen korrekte lokale Zeitfenster;
7. `git status` zeigt keine versehentlich gecommitten Secrets/`config/`/`photos/` (TC-C05);
8. Test-Artefakte aufgeräumt (Container gestoppt, `__pycache__` entfernt).

---

## 9. Firmware-Handover (Out of Scope, hier nur Schnittstelle)

Der Server↔Firmware-Contract, den die Firmware-Seite abdecken muss:

| Schnittstelle | Richtung | Test auf FW-Seite |
|---|---|---|
| `GET /download` → `.c`-Array (16-Level) | Server → FW | Parser erzeugt valides Bild; Größe/Format stabil (TC-D01) |
| Header `X-Photo-Url` | Server → FW | NFC-Deep-Link; **FW liest den Header aktuell noch nicht** – Feature-Pending |
| Header `batteryCap` (mV) | FW → Server | FW sendet reale Spannung; Server-Log zeigt korrekten % (TC-SL03) |
| `GET /sleep` → `sleep_duration` | Server → FW | FW schläft exakt so lange; Weck am `next_wakeup` (TC-SL01/02) |
| Deep-Sleep-Aktualstrom | FW-Intern | ~16 µA (HW-Test, kein Server-Test) |

> **Hinweis:** `X-Photo-Url` ist reine Zusatzinfo; der Download funktioniert unabhängig davon.
> Firmware-Adaption dafür ist ein separater Backlog-Item.

---

## Anhang A – Traceability: Änderung → Testfälle

| # | Änderung (Commit) | Primär-TC | Sekundär-TC |
|---|---|---|---|
| v3-API | Immich-v3 + NFC (`ca4287f`) | TC-API02, TC-D01, TC-API05 | TC-API01/03/04, TC-N01/N02/N05 |
| 1 | `deepcopy` (`c6ac86e`) | TC-U01 | TC-C02 (Reset korrekte Defaults) |
| 2 | `docker-compose.yml` + `.env.example` (`6e9d7c7`) | TC-C01, TC-C02, TC-C03 | TC-U06 (TZ) |
| 3 | README Env-Namen (`d0bba80`) | (Doku) | TC-C01 |
| 4 | `.gitignore` (`602fe97`) | TC-C05 | — |
| 5 | `battery_*` in Fehlerpfaden (`9c01295`) | TC-S02, TC-S01 | TC-S03 |
| 6 | „newest“-Guard (`cac36d2`) | TC-U03 | TC-U04 (Regression), TC-D01 |
| 7 | `load_config()` None-Guard (`05de873`) | TC-U02 | TC-N06, TC-N08 |

## Anhang B – Beobachtete Referenzergebnisse (Live, 2026-09-13)

| TC | Beobachtung |
|---|---|
| TC-U01 | shallow → Defaults mutiert; deepcopy → isoliert ✅ |
| TC-U02 | 4/4 Fälle → nie `None` ✅ |
| TC-API02 | `size=5`×5 → 25; `size=50` → 42; `nextPage` terminiert ✅ |
| TC-API04 | 403 `Missing required permission: asset.download` (bevor Key-Fix) ✅ |
| TC-D01 | 200, 588 003 B, `image_1d1d3364-…`, `X-Photo-Url` gesetzt ✅ |
| TC-S02 | `rotation=45` → HTTP 200, Error gerendert, kein Traceback ✅ |
| TC-S04 | gültiges Save → HTTP 302 ✅ |
| TC-U03 (E2E) | 42 IDs in Tracking (Album-Name Zeile 1) → 200, Reset, neuestes geliefert ✅ |
| TC-C01 | `docker compose` ohne Key → Interpolation-Fehler, kein Start ✅ |
| Health-check | `GET /sleep` → 200 (docker-compose-Health-Target) ✅ |

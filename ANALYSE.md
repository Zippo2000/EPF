# EPF – E-paper ESP32 Frame: Vollständige Projektanalyse

> **Repos:** [`Zippo2000/EPF`](https://github.com/Zippo2000/EPF) · **Status laut Autor:** *Work in Progress* · **Lizenz:** MIT

---

## 0. Inhaltsverzeichnis

1. [Zweck & Idee](#1-zweck--idee)
2. [Gesamtsystem-Architektur](#2-gesamtsystem-architektur)
3. [Repo-Struktur im Detail](#3-repo-struktur-im-detail)
4. [Python/Flask-Server (`app.py`)](#4-pythonflask-server-apppy)
5. [Cython-Beschleunigung (`cpy.pyx`)](#5-cython-beschleunigung-cpyx)
6. [ESP32-Firmware](#6-esp32-firmware)
7. [Ende-zu-Ende-Workflow (Sequenz)](#7-ende-zu-ende-workflow-sequenz)
8. [HTTP-Vertrag zwischen ESP32 und Server](#8-http-vertrag-zwischen-esp32-und-server)
9. [Konfigurationsdatenschema (`config.yaml`)](#9-konfigurationsdatenschema)
10. [Bemerkungen, Schwachstellen & offene Punkte](#10-bemerkungen-schwachstellen--offene-punkte)
11. [Fazit](#11-fazit)

---

## 1. Zweck & Idee

**EPF** ist ein **akkugespastetes E-Paper-Fotorahmen** auf Basis eines **DFRobot FireBeetle ESP32-E**, angebunden an eine **7,3“-Farb-E-Paper-Platte von Waveshare (Spectra 6 / „E6“)**, 800×480, 6 Farben).

Das zentrale Designprinzip ist die **Trennung von Berechnung und Anzeige**:

- **Schwere Arbeit** (Foto-Download, Crop, Scaling, Farbkorrektur, Dithering/Quantisierung, Umwandlung in Panel-Daten) läuft **komplett auf einem Server** (NAS/Cloud) als **Flask-App**, meist in Docker.
- Der **ESP32 ist 99 % der Zeit im Deep-Sleep** (~**16 µA**) und wacht nur auf, um (a) ein fertiges Bild abzuholen, (b) es ins E-Paper zu schreiben und (c) wieder einzuschlafen.
- **Quellfotos** kommen aus **[Immich](https://immich.app)** (Self-Hosted-Photomanagement). Ein bestimmtes **Album** ist die Quelle; neue Fotos erscheinen automatisch.

**Ergebnis:** Foto-Refresh typisch **< 30 s**, extrem niedriger Stromverbrauch, kein dauerhaft aktiver WLAN-Client am Gerät.

### Kernfeatures (aus README)
- **Captive-Portal** zum Wi-Fi + Server einrichten (lange halten beim Boot), bis zu **5 gespeicherte SSIDs**.
- **Vollautomatisches Foto-Management** über Immich-Album (kein manueller Upload).
- **Atkinson-Dithering** (6-Farben-Quantisierung).
- **Ultra-niedriger Stromverbrauch** (Deep Sleep ~16 µA).
- **Web-UI** für Rotation, Farb-Enhancement, Kontrast, Dithering-Stärke, Album, Display-Modus (fit/fill), Reihenfolge (random/neueste), Schlafzeitfenster, Wakeup-Intervall.
- **Cython** für ~5× schnellere Bildverarbeitung.
- **HTTPS**-Support (`WiFiClientSecure` + `setInsecure()`).
- **Schlafzeitfenster** (z. B. 23:00–06:00) – Gerät wacht darin nicht auf.
- **Ein Button:** kurz = Wakeup+Restart, lang (~3–5 s beim Boot) = Setup-Modus.

---

## 2. Gesamtsystem-Architektur

```
┌───────────────────────────┐   ┌────────────────────────────────┐   ┌──────────────────────────────────────┐
│   IMMICH (Cloud/NAS)      │   │   FLASK-SERVER (NAS/Cloud)      │   │   ESP32-E (FireBeetle) + EPD           │
│   /api/albums             │   │   (Docker, Port 5000)          │   │   Waveshare 7.3" E6 (800x480)         │
│   /api/albums/{id}        │   │   app.py + cpy.so (Cython)      │   │   • Deep-Sleep (RTC-Timer / GPIO)     │
│   /api/assets/{id}/original│  │   • Pillow / pillow-heif /      │   │   • Wi-Fi (≤5 SSIDs, Captive)         │
│                          │   │     rawpy / ntplib / watchdog    │   │   • EPD SPI @ 4 MHz                  │
│   Auth: Header x-api-key  │──►│   Routes: / /setting /download  │──►│   • 1 Button (Setup / Wakeup)         │
│                          │JSON│            /sleep               │HTTP│   • LiPo-ADC (GPIO34, Spannungs-     │
└───────────────────────────┘   │   /download -> .c-Text (Hex)    │   │       teiler x2)                     │
                                  └────────────────────────────────┘   └──────────────────────────────────────┘
```

**Datenfluss (Kurzfassung):**
1. ESP32 wacht auf → `GET {server}/download` (Header `batteryCap` = LiPo-Spannung in mV).
2. Server ruft das **Album** aus Immich ab, wählt ein **noch nicht gezeigtes** Foto (random *oder* neueste), lädt die **Originaldatei** (RAW/DNG/HEIC/JPG/…).
3. Pipeline: **EXIF-Orientierung** → `load_scaled` (Cython) → **Color/Contrast-Enhance** → **Atkinson-Quantisierung** (Cython) → BMP im RAM → **Depaletisieren** → **Hex-Byte-String**.
4. Server antwortet mit einer **`.c`-Textdatei** (Array `XX,XX,…};`).
5. ESP32 parst Bytes → `SendCommand(0x10)` + `SendData` über SPI → `TurnOnDisplay` → `Sleep`.
6. ESP32 ruft `GET {server}/sleep` → erhält `sleep_duration` (ms) → **Deep-Sleep** für genau diese Dauer (Fallback: Default 24 h).

---

## 3. Repo-Struktur im Detail

| Pfad | Typ | Zweck |
|------|-----|-------|
| `app.py` | Python | **Flask-Server** (Kern): Config-Handling, Immich-Anbindung, Bildpipeline, Routes `/`,`/setting`,`/download`,`/sleep`, NTP-Sync, Batteriespannung, Config-Dateiwächter. |
| `cpy.pyx` | Cython | **Quelle** des beschleunigten Moduls: `load_scaled`, `convert_image` (Floyd–Steinberg), `convert_image_atkinson` (**aktiv**). |
| `cpy.c` | C | Vom Cython **generiertes** C-File (~1,3 MB) – Build-Artefakt. |
| `cpy.so` | Binary | **Vorkompilierte** Shared Library, die der Server zur Laufzeit importiert (`from cpy import …`). ⚠ Nicht vom Dockerfile neu gebaut (s. §10.8.1). |
| `setup.py` | Python | **Build-Script** für das Cython-Modul (`cythonize` + numpy-Include). |
| `requirements.txt` | – | Flask, requests, numpy, pillow, pillow_heif, PyYAML, rawpy, watchdog, DateTime, ntplib, **Cython**. |
| `Dockerfile` | Docker | `python:3.9-slim` → `COPY .` → `pip install -r requirements.txt` → `EXPOSE 5000` → `CMD ["python","app.py"]`; Env `IMMICH_API_KEY`. |
| `templates/settings.html` | Jinja/HTML/JS | **Web-UI**: Konfig-Formular, Batterie-Balken, Reset-Modal, Fetch-basiertes Speichern. |
| `.gitattributes` | Git | `* text=auto` (LF-Normalisierung). |
| `README.md` | Docs | Beschreibung, Hardware-Liste, Install (Docker + Arduino), `config.yaml`-Beispiel. |
| `Arduino/epd7in3e.ino` | C++ | **Firmware-Hauptteil**: `class EpaperManager` (`begin`,`update`,`downloadImage`,`processImageData`,`hibernate`,…) + `setup()/loop()`. |
| `Arduino/config.h` | C++ | Makros/Pins: `CONFIG_PIN=2`, `WAKEUP_PIN=GPIO_NUM_2`, `SLEEP_INTERVAL=3600`, `BUFFER_SIZE=96000`, `MAX_RETRIES=5`, `SERVER_BASE_URL` (Platzhalter), Preferences-Keys. |
| `Arduino/button.h` | C++ | `class Button`: Debounce + **Long-Press** (~3 s) → Setup-Modus. |
| `Arduino/epdif.h/.cpp` | C++ | **Waveshare-SPI-Layer** (`IfInit`, `SpiTransfer`, `DigitalRead/Write`); Pins BUSY=25, RST=4, DC=13, CS=14; **SPI 4 MHz**. |
| `Arduino/epd7in3e.h/.cpp` | C++ | **EPD-Treiber** 7.3" E6: `Init` (Command-Sequenz), `TurnOnDisplay`, `Sleep`, `Clear`, `EPD_7IN3E_Display`. Farb-Indizes `EPD_7IN3E_*` (0..7). |
| `Arduino/filesystem.h/.cpp` | C++ | `fs_init()`/`fs_deinit()` um **SPIFFS** (weiterer Teil kommentiert/legacy). |
| `Arduino/WifiCaptive.h` | C++ | `class WifiCaptive` + Konstanten: `WIFI_SSID="ESP32_ePAPER"`, max. **5 Credentials**, `LocalIPURL="http://4.3.2.1"`. |
| `Arduino/WifiCaptive.cpp` | C++ | **Captive-Portal** (aus TRMNL abgeleitet): Soft-AP, DNS-Redirect, ESPAsyncWebServer (`/scan`,`/connect`,`/soft-reset`), Credentials **und Server-URL** in Preferences speichern, `autoConnect()` (bestes Signal). |
| `Arduino/WifiCaptivePage.h` | C++ | **Inlined HTML** der Portal-Seite (`INDEX_HTML`). |
| `CAD/*.STEP` | 3D | Gedrucktes **Gehäuse**: `bottom_panel`, `display_card`, `display_holder`, `pcb_holder`, `upper_panel`. |
| `LICENSE` | – | MIT. |

**Hardware-Liste** (README): DFRobot FireBeetle ESP32-E · Waveshare 7.3" E-Paper Spectra-6 HAT · LiPo-Batterie mit PH2.0-Header · Taster · Standardbilderrahmen.

---

## 4. Python/Flask-Server (`app.py`)

Der Server ist die **intelligente Hälfte** des Systems. Aufbau:

### 4.1 Konfiguration (Zweiquellen-Modell)
- **`DEFAULT_CONFIG`** (`immich.*`): Defaults für URL, Album, Rotation, `enhanced`, `contrast`, `strength`, `display_mode` (fit/fill), `image_order` (random/newest), Schlaf-Start/-Ende, `wakeup_interval`.
- **Aktive Config** wird global gespiegelt (`url`, `albumname`, `rotationAngle`, `img_enhanced`, …) und zusätzlich in `app.config[...]` geschrieben.
- **Persistenz:** `/config/config.yaml` (YAML). **`ConfigFileHandler`** (watchdog) lädt bei Änderung neu und ruft `update_app_config(new_config)` auf → **Hot-Reload** ohne Restart.
- **Wichtig:** Laut README ist `config.yaml` **nicht mehr primär** – Einstellungen laufen heute über die **Web-UI** (`/setting`), die dieselbe YAML-Datei schreibt und `update_app_config` sofort aufruft.
- **Env-Variablen:** `IMMICH_API_KEY` (Pflicht *in der Praxis*; der Code validiert sie **nicht** – fehlt sie, ist der Header-Wert `None` und die Immich-Aufrufe fehlschlagen ⇒ 500 ⇒ 10-s-Poll-Loop, s. §10.1.4), `IMMICH_PHOTO_DEST` (Default `/photos`), davon `tracking.txt`.
- **Headers** an Immich: `Accept: application/json`, `x-api-key: <key>`.

### 4.2 Tracking-Zustand (`tracking.txt`)
- Persistiert, **welche Asset-IDs bereits gezeigt** wurden, pro Album.
- Format: **Zeile 1 = Albumname**, darunter je eine `asset_id`.
- `load_downloaded_images()` liest den Set; **stimmt die erste Zeile nicht mit dem aktuellen Album überein → Datei wird geleert** (Albumwechsel!).
- `save_downloaded_image(id)` appendet die neue ID; `reset_tracking_file()` leert die Datei.
- **Zweck:** Ermöglicht „zeige nie das gleiche Foto doppelt, bis das Album leer ist“. Bei `newest`/`random` wird daraus die Liste **restlicher** Bilder gebildet; leer ⇒ Reset + ganzes Album.

### 4.3 Bildpipeline (`/download` + Helpers)
Der Herzstück-Ablauf pro Request (Funktion `process_and_download`):

1. **Batterievoltage** aus Header `batteryCap` lesen & merken (`last_battery_voltage` / `_update`) – fließt später in die Web-UI.
2. **Album auffinden:** `GET {url}/api/albums` → `albumName == albumname` → `albumid` (sonst 404). ⚠ Feldname-Kopplung, s. §10.5.6.
3. **Assets abrufen:** `POST {url}/api/search/metadata (paginiert, nextPage-Loop)` → `data['assets']` (sonst 404 „no images“).
4. **Auswahl** je nach `image_order` (in der reinen Funktion `choose_next_image()`):
   - **newest:** letztes (neustes per EXIF `dateTimeOriginal`) Bild; ist es *neu*, → Tracking-Reset + absteigend sortiert; sonst nur die **unseen** Assets, absteigend.
   - **random:** unseen Assets, sonst Reset + alle; **zufällig** eines ausgewählt.
5. **`save_downloaded_image(asset_id)`** – Auswahl wird als „gezeigt“ protokolliert.
6. **Original-Download:** `GET {url}/api/assets/{asset_id}/original` (stream) → `BytesIO`.
7. **Dekodieren** je nach Endung:
   - RAW/DNG/ARW/CR2/NEF → **`rawpy.postprocess`** (Camera-WB)
   - HEIC → **Pillow + pillow_heif** (`convert("RGB")`)
   - sonst → **Pillow** direkt.
8. **`scale_img_in_memory(image)`** → verarbeitet:
   - EXIF `datetime` (Tag 36867 / Fallback 306) lesen.
   - `ImageOps.exif_transpose` (korrekte Orientierung).
   - **`load_scaled(image, rotation, display_mode)`** (Cython) → exakte **800×480** (fit = Letterbox auf weiß; fill = Cover/Crop).
   - **`ImageEnhance.Color(*).enhance(enhanced)`** + **`ImageEnhance.Contrast(*).enhance(contrast)`**.
   - **`convert_image_atkinson(enhanced_img, dithering_strength=strength)`** (Cython) → 6-Farben-Bild (RGB).
   - *Datums-Overlay* ist vorhanden, aber **auskommentiert** (WIP) – inkl. Rotation-abhängiger Textplatzierung & rotiertem Text-Bild.
9. **`convert_to_c_code_in_memory(Image)`** → erzeugt die finale Ausgabelänge:
   - `np.array(bild)`, `depalette_image(pixels, palette)` → für jeden Pixel den Index in der Palette.
   - **Packen:** zwei aufeinanderfolgende 2-Bit-Indizes pro Byte: `(idx[y,x] << 4) | idx[y,x+1]` → als Hex-Zahl `XX,`.
   - Ausgabe: Array `XX,XX,…};` (je 16 Bytes eine Zeile) – das ist der **Body der Antwort**.
   - **Antwort:** `send_file(c_code, mimetype="text/plain", as_attachment=True, download_name="image_<id>.c")`.

> **`depalette_image`-Eigenheit:** Nach dem Argmin-Match auf 6 Farben (`0..5`) wird `indices[indices>3] += 1` ausgeführt → Indizes `4,5` werden zu `5,6`. Das reproduziert eine Referenz-C-Logik, die bestimmte 2-Bit-Slots des Panels belegt/vermeidet (s. §10.5.5 + §10.6).

### 4.4 Schlaf-Dauer berechnen (`/sleep` → `get_sleep_duration`)
Der Server diktiert, **wie lange** das Gerät schlafen soll:
1. Aktuelle Systemzeit + `wakeup_interval` (Minuten) → nächstes Slot-Zeitfenster (`calculate_next_interval_time`).
2. **Schlafzeitfenster** (`sleep_start`…`sleep_end`, auch über Mitternacht hinweg korrigiert): fällt der nächste Wakeup hinein, → er wird auf **`sleep_end`** verschoben.
3. Ergebnis `sleep_duration` in **Millisekunden**; bei < 10 min wird der übernächste Slot geprüft.
4. Response: `{ "current_time", "next_wakeup", "sleep_duration" }` (ms).

### 4.5 NTP-Abgleich (Hintergrund-Thread)
- `run_daily_ntp_sync()` (Daemon-Thread, startet in `main()`): schläft bis **täglich 04:11**, ruft dann `sync_time_with_ntp()` (`pool.ntp.org`) auf; Fehler → Retry in 1 h.
- Zwecks korrektem Schlaf-/Wakeup-Berechnen trotz ungenauer NAS-Uhr.

### 4.6 Batterie (mV → %)
- `BATTERY_LEVELS`-Tabelle (mV → %) + `calculate_battery_percentage()` mit **stücklinearer Interpolation**.
- Eingang = letztes gültiges `batteryCap` (nur wenn < 1 h alt, sonst 0). Wird in der Web-UI als Balken angezeigt.

### 4.7 Web-UI & Routes
- **`/`** → redirect auf **`/setting`**.
- **`/setting` (GET):** rendert `settings.html` mit Config + Batteriewerten.
- **`/setting` (POST):** nimmt Formularwerte (int/float-cast), **validiert Rotation** (0/90/180/270), **schreibt `config.yaml`**, ruft `update_app_config()` (→ Watcher sieht die Änderung ebenfalls) und redirectet.
- JS: Slider, Batterie-Visualisierung, **Reset-Modal** (setzt Client-seitig Defaults), **Fetch-POST** (keine Page-Reload) mit Toast-Benachrichtigung.

### 4.8 Einstiegspunkt
- `main()`: Config-Pfad festlegen → **Watcher starten** → Initial-Config laden & anwenden → **NTP-Thread** starten → **`app.run(host=0.0.0.0, port=5000, use_reloader=False)`**.
---

## 5. Cython-Beschleunigung (`cpy.pyx`)

`cpy.pyx` ist der **Hot-Path** der Bildverarbeitung. Mit `boundscheck/wraparound/nonecheck=False`, `nogil`-Helper-Funktionen und numpy-Views (`cdef double[:, :]`, `np.ndarray[uint8_t,ndim=3]`) wird der Python-Overhead minimiert -> laut README **~5x Speedup** vs. reinem PIL/NumPy.

Konstanten: `EPD_W = 800`, `EPD_H = 480`.

### 5.1 `load_scaled(image, angle, display_mode='fit')`
- Bild -> RGB -> `rotate(angle, expand=True)`.
- **`fill`:** Seitenverhältnis Image vs. Panel vergleichen -> **Cover + Center-Crop** auf exakt 800x480 (LANCZOS).
- **`fit`:** **Contain** (Letterbox) -> zentriert auf weißem 800x480-Grundbild.
- Ergebnis: exakt 800x480 RGB, fertig für Enhance + Atkinson.

### 5.2 `convert_image(input_image, preview_path=None, dithering_strength=1.0)`  *(Floyd-Steinberg, aktuell NICHT aktiv)*
- 6-Farben-Palette `epd_colors` (schwarz/weiss/gelb/rot/blau/gruen, 0..1 normalisiert).
- Pro Pixel: nächstmögliche Farbe per **einfachem euklidischem Abstand** (0..1-Raum) suchen; dann **Floyd-Steinberg-Fehlerdiffusion** (7/16, 3/16, 5/16, 1/16) skaliert mit `dithering_strength`.
- Gamma-Korrektur (`gamma_linear`) ist **auskommentiert**; `preview_path` ist ein **No-Op** (es wird nichts geschrieben).

### 5.3 `convert_image_atkinson(input_image, preview_path=None, dithering_strength=1.0)`  *(AKTIV verwendet)*
- Gleiche 6-Farben-Palette `epd_colors` (reine Farben: `255,255,0` gelb; `255,0,0` rot; `0,0,255` blau; `0,255,0` grün).
- Pro Pixel: nächste Palette-Farbe (ebenfalls **einfacher euklidischer Abstand**), dann **Atkinson-Fehlerdiffusion** mit **sechs** Nachbarn (rechts, 2x rechts, links-unten, unten, rechts-unten, 2 Zeilen unten), je **1/8** des Fehlers, skaliert mit `dithering_strength * 0.75`.
- Atkinson erzeugt weicheres, gleichmassigeres Dithering -> fuer EPD (wenige Farben) oft visuell besser als Floyd-Steinberg.
- Zurueckgabe: **numpy-Array (H,W,3)** – die Pixel sind **exakt** die 6 Palette-Farben (keine Zwischenstufen); `preview_path` ist ebenfalls No-Op.

> Hinweis: Es existieren zusätzliche `nogil`-Helper `closestColor`/`closestColor_Atkinson` mit **gewichteter Perzeptiv-Distanz** (1063/5000, 447/625, 361/5000) – sie sind **definiert, aber nur in auskommentierten Aufrufen** referenziert. Die aktiven Loops beider Converter verwenden bewusst die einfachere quadrierte Distanz. (Quellen: cpy.pyx L25, L207, L161.)

---

## 6. ESP32-Firmware

Die Firmware ist **klassisches „wakeup -> arbeit -> deep-sleep"-Muster**. Fast alles steckt in `epd7in3e.ino`.

### 6.1 Boot-Sequenz (`setup()`)
1. **Wake-Up-Grund** auslesen: `ESP_SLEEP_WAKEUP_TIMER` / `ESP_SLEEP_WAKEUP_EXT1` (Button/GPIO) / erster Start.
2. **`checkVoltage()`**: LiPo-Spannung messen (s.u.). **< 3050 mV** -> Display weissen, Wi-Fi aus, **Deep-Sleep 24 h** (Kritisch-Entladen-Schutz).
3. **`begin()`** -> liefert `true/false`:
   - `epd.Init()`, `fs_init()` (SPIFFS), `preferences.begin("data")`, `WiFi.mode(STA)`.
   - **Config-Pruefung:** `Button(CONFIG_PIN).result()` -> **Long-Press ~3 s** beim Boot = **Setup-Modus** (`WifiCaptivePortal.startPortal()`).
   - Gespeichert? `isSaved()`:
     - **Ja** -> `autoConnect()` (bestes bekanntes Netz).
     - **Nein** -> `setResetSettingsCallback(resetDeviceCredentials)` + `startPortal()`.
4. `begin()==true` -> **`update()`** (Bild holen + sleep); sonst Display weissen, 30 s warten, `ESP.restart()`.

### 6.2 Batteriespannung (`readBatteryVoltage()`)
- **GPIO34** 50× messen, Werte <= 100 mV verwerfen, **Mittelnehmen × 2** (Spannungsteiler) -> **mV** (≈ 250 ms + ADC-Overhead).
- Nuance: `analogReadResolution(12)` wird **nur in `downloadImage()`** (vor der toten `plusV`-Schleife) gesetzt; die Boot-Messung in `checkVoltage()` läuft mit der **Default-Auflösung (11 bit)** – unkritisch, aber inkonsistent.
- In `downloadImage()` existiert zusätzlich eine **tote Code**-Schleife `plusV` (50 ADC-Reads), deren Ergebnis **nie gesendet** wird – der Header-Value kommt aus `readBatteryVoltage()`. => ~doppelte Messarbeit pro Wakeup.
- Der Wert wird an den Server als Header `batteryCap` geschickt; dort in % umgerechnet (s. 4.6) und in der Web-UI angezeigt.

### 6.3 `downloadImage()`  (Kern-Transaktion)
Leset `SERVER_BASE_URL` aus `preferences["data"]`. Unterscheidet **HTTP vs. HTTPS** (`startsWith("https://")`) -> `WiFiClient` bzw. `WiFiClientSecure::setInsecure()`.

Ablauf (Retry-Logik, `MAX_RETRIES=5`):
1. `http.GET()` zu `<base>/download`.
   - **200** -> `processImageData(&http)` (siehe 6.4).
   - **202** -> `delay(RETRY_DELAY=10 s)` und **weiteres `GET`** (ohne Break; innerhalb desselben Durchgangs bis zu 5 Mal; danach End des Loops). Anmerkung: der Flask-Server selbst sendet **nie** 202 (defensiv/Proxy-Fall, s. §8.1).
   - **500** -> `delay(10 s)`, Break aus dem Inner-Loop, aber `retryOnError=true` **setzt den äußeren While-Loop neu** -> solange der Server 500 liefert (z. B. fehlendes Album/URL), **pollt der Frame endlos im 10-s-Takt** (der Autoren-Kommentar „will retry once“ beschreibt das nicht korrekt; s. §10.1.4).
   - sonst -> Fehler-Log, Break, Ende.
2. **Nach Erfolg:** zweiter Request `GET <base>/sleep` (neuer Client) -> JSON `{sleep_duration}`; `sleep_duration` (ms) -> **Sekunden** (`/=1000`).
3. Clients werden **geloescht** (Memory-Return).
4. `success && sleepDuration>0` -> `hibernate(sleepDuration)`; sonst `hibernate()` (Default **86400 s = 24 h**; s. §10.7.1).
5. Rueckgabe `bool success`.

### 6.4 `processImageData(HTTPClient*)`  (Stream-Parsing)
- Stream-Groesse via `http->getSize()` pruefen; **`BUFFER_SIZE`=96000** allozieren (wiederverwendet, Chunk-Reads).
- `epd.SendCommand(0x10)` -> **RAM-Daten-Modus** der EPD starten.
- Stream byte-for-byte lesen; Bytes akkumulieren, bis ein **Delimiter** (`,`, `\n`, `\r`, `\0`) kommt -> Token via `strtol(...,16)` in ein Byte wandeln -> **`epd.SendData(byteValue)`**.
- Rest-Token nach Loop-Ende ebenfalls absenden; `free(buffer)`.
- Danach **`epd.TurnOnDisplay()`** (Panel-Refresh) und **`epd.Sleep()`** (Panel-Deep-Sleep) -> Display ist aktuell, Chip spart Energie.

> Das bedeutet: der Server liefert ein **kompakt gepacktes 2-Bit-Bitmap** als Text; der ESP32 braucht **keine eigene Bildrechnung**, nur Stream-Parsing + SPI-Write. Genau so bleibt er „dumm" und sparsam.

### 6.5 `hibernate(int sleepDuration=0)`  (Deep-Sleep)
- `sleep_interval = sleepDuration>0 ? sleepDuration : 86400` (d. h. **24 h**). Der `else`-Zweig mit `SLEEP_INTERVAL` (3600 s) ist **unerreichbarer Tot-Code** (s. §10.7.1).
- **WiFi komplett aus** (`WiFi.disconnect(true)`, `WIFI_OFF`), **`fs_deinit()`** (SPIFFS trennen).
- `sleep_time` in **Mikrosekunden** -> `esp_sleep_enable_timer_wakeup(sleep_time)`.
- **RTC-GPIO** `WAKEUP_PIN`(=GPIO2) konfigurieren: Input-Only, Pull-up, `esp_sleep_enable_ext1_wakeup(1<<WAKEUP_PIN, ESP_EXT1_WAKEUP_ALL_LOW)` -> **Taster im Sleep** weckt (LOW).
- `Serial.flush()`, kurze Delays, **`esp_deep_sleep_start()`**.

> Wake-Up-Quellen kombiniert: **Timer** (regelmässiges Wake-up) + **EXT1/GPIO** (Button). Beide zusammen realisieren „alle X Minuten aufwachen ODER per Taster sofort".

### 6.6 `WifiCaptive`-Portal (aus TRMNL abgeleitet)
- **Soft-AP**: SSID `ESP32_ePAPER`, kein Passwort, **IP 4.3.2.1**, zufaelliger Kanal (1-10), max. 1 Client.
- **DNS-Hijack**: `*` auf 4.3.2.1 -> Browser landen zwingend auf der Portal-Seite.
- **Captive-Detection-Endpoints** implementiert (Android `generate_204`, iOS `hotspot-detect.html`, Windows `connecttest.txt`/`ncsi.txt`, Firefox `canonical.html`/`success.txt`, Windows 10 `wpad.dat`, Microsoft `redirect`, ...) -> redirect auf `http://4.3.2.1`.
- **Web-UI** (inlined `INDEX_HTML`):
  - `/` -> HTML (GZip).
  - `/scan` -> JSON-Liste gefundener Netzwerke (inkl. `saved`-Flag).
  - `/connect` -> JSON {ssid,pswd,server}; verbindet; Erfolg -> `saveWifiCredentials()` speichert **SSID/PASS + Server-URL** in Preferences; speichert Index des letzten Netzes.
  - `/soft-reset` -> `resetSettings()` + Callback `resetDeviceCredentials()` (loescht ALLE Credentials + `SERVER_BASE_URL`) -> `ESP.restart()`.
- **`autoConnect()`**: liest Credentials; versucht zuerst **letztes Netz** (3 Attempts); sonst **SCAN** bekannte Netze, nach **Signal (RSSI)** sortieren; jeweils 3 Attempts; bei Erfolg `saveLastUsedWifiIndex()`.
- **Persistence**: **zwei** Preference-Namespaces:
  - `"data"` -> `SERVER_BASE_URL` (Server-URL).
  - `"wificaptive"` -> `wifi_<i>_ssid`, `wifi_<i>_pswd` (i=0..4), `wifi_last_index`.

### 6.7 EPD-Treiber (`epd7in3e.cpp`) & SPI (`epdif.cpp`)
- **`EpdIf` (Basis):** Pins (BUSY=25, RST=4, DC=13, CS=14), `SPI.begin()` + **4 MHz**, `SPI_MODE0`, `MSBFIRST`.
- **`Epd::Init()`** sendet die **Waveshare-Kalibrierung** (Commands 0xAA, 0x01, 0x00, 0x03, 0x05, 0x06, 0x08, 0x30, 0x50, 0x60, 0x61, 0x84, 0xE3, 0xE0, 0x04) + Busy-Wait.
- **`SendCommand(cmd)`**: DC=LOW, CS=LOW, `SpiTransfer`, CS=HIGH.
- **`SendData(data)`**: DC=HIGH, ... (selbes Schema).
- **`TurnOnDisplay()`**: 0x04 (POWER_ON) + Busy, nochmal 0x06, **0x12 (DISPLAY_REFRESH)** + Busy, 0x02 (POWER_OFF) + Busy.
- **`EPD_7IN3E_Display(image)`**: 0x10 -> schickt `height*(width/2)` Bytes, dann Refresh. (Im Ino wird aber **das Stream-Verfahren** aus 6.4 genutzt, nicht diese Methode.)
- **`Clear(color)`**: 0x10 -> `width/2 * height` mal `(color<<4)|color` -> weisses Panel (oder andere Farbe) + Refresh.
- **`Sleep()`**: **0x02 (DEEP_SLEEP)** + Busy, 0x07 (VCOM), RST=0 -> Panel schlaeft (Wakeup nur via Hardware-Reset).

### 6.8 Pin-Layout (FireBeetle ESP32-E <-> 7.3" HAT)
| Driver | ESP32 | Funktion |
|--------|-------|----------|
| BUSY   | **25** | EPD-Busy (Low = beschäftigt) |
| RST    | **4**  | Reset |
| DC     | **13** | Data/Command |
| CS     | **14** | Chip-Select |
| SCLK   | **18** | SPI Clock |
| DIN    | **23** | SPI Data |
| SETTING | **2** (Wiring-Kommentar sagt 27 – Widerspruch; wirksam: `CONFIG_PIN=2`, `WAKEUP_PIN=GPIO_NUM_2`) | Setup/Wakeup-Taster; nur GPIO 0–15 sind RTC-fähig, daher Pflicht-2 (s. §10.7.6) |

> In `config.h`: `CONFIG_PIN=2`, `WAKEUP_PIN=GPIO_NUM_2`, `BUTTON_HOLD_TIME=3000`, `BUTTON_DEBOUNCE=100`, `SLEEP_INTERVAL=3600`, `MIN_SLEEP_TIME=900`, `BUFFER_SIZE=96000` (ESP32-E).

### 6.9 `Button`-Klasse (Setup-Erkennung)
- Polling-Loop bis zu `longPressTime + 500 ms`.
- State-Change -> Debounce-Counter + `hasEvent`.
- **Kein Event innerhalb 1500 ms** -> `false` (kein Setup).
- **Kontinuierlich gedrueckt >= 3 s** -> `longPressDetected` -> **`true`** (Setup).
- -> Damit wird Setup **beim Boot** durch Langdruck ausgelöst; danach ist das Portal bis `CONFIG_TIMEOUT` (5 min) offen.

---

## 7. Ende-zu-Ende-Workflow (Sequenz)

Gesamtzyklus pro Bild-Refresh (typisch 15-30 s):

```
 [Deep-Sleep, ~16 uA]
        |  (RTC-Timer abgelaufen  ODER  Taster -> EXT1 Wakeup)
        v
 [setup()]
  |- Wakeup-Grund loggen
  |- checkVoltage():  mV < 3050 ?  --JA--> weiss, sleep 24h, END
  |                     \--NEIN-->
  |- begin():
  |    epd.Init(); fs_init(); preferences; WiFi=STA
  |    Button Long-Press?  --JA--> Captive-Portal (WLAN+Server), END
  |    else isSaved()?
  |         JA -> autoConnect() (bestes Netz, 3 tries)
  |         NEIN -> Captive-Portal, END
  |    success?
  v
 [update()]  (WiFi.connected?)
  |- downloadImage():
  |    GET /download  (batteryCap: mV)
  |       200 -> processImageData():
  |               SendCommand(0x10)
  |               Stream-Parse -> epd.SendData(byte)  [x ~96000]
  |               TurnOnDisplay(); epd.Sleep()          <-- BILD SICHTBAR
  |       (202 -> 10s warten -> retry; 500 -> solange 500, 10s-Polling-Loop)
  |    GET /sleep  -> JSON {sleep_duration: ms}
  |    hibernate(sleep_s):
  |         WiFi OFF, fs_deinit()
  |         rtc timer = sleep_s ; rtc ext1 (GPIO2, low)
  |         esp_deep_sleep_start()
  v
 [Deep-Sleep]  -----> (naechster Wakeup)
```

**Schluesselpunkte des Workflows:**
- Das **Bild wird voellig serverseitig vorbereitet** (Panel-ready 2-Bit-Bitmap als Text). Der ESP32 macht **null Bildbearbeitung** — nur Stream + SPI.
- **Kein dauerhafter WLAN**: nach jedem Refresh Radio **hart aus**.
- **Sleep-Dauer ist dynamisch** (Server berechnet Intervall + Schlafzeitfenster).
- **Zustand** (welche Assets schon gezeigt, welches Netz, Server-URL) liegt entweder serverseitig (`tracking.txt`) oder in den ESP32-**Preferences** (NVS) — also **persistiert** (überlebt Deep-Sleep/Power-Cycles).

---

## 8. HTTP-Vertrag zwischen ESP32 und Server

### 8.1 `GET /download`
| Richtung | Inhalt |
|-----------|--------|
| Request | `GET {base}/download` · Header **`batteryCap: <mV>`** |
| Response 200 | **Body = Text**: C-artiges Array `XX,XX,...};` (2 hex Ziffern pro gepacktes Pixel-Byte). Dateiname `image_<assetId>.c`, `Content-Type: text/plain`, `Content-Disposition: attachment`. |
| Response 202 | Server verarbeitet noch (ESP wartet 10 s, retry). *Dieser Flask-Server selbst sendet 202 nie – defensiv für Proxy/Queue-Zwischenlagen.* |
| Response 404 | Album/Assets nicht gefunden. |
| Response 500 | `{ "error": "..." }` (z. B. URL/Album leer, Download-Fehler). *Solange 500 persistiert, pollt die Firmware endlos im 10-s-Takt (s. §6.3, §10.1.4).* |

> **Format der Pixel-Bytes:** pro Zeile 16 Bytes, jede Zahl = `hi_nibble|lo_nibble`, wobei jeder 4-Bit-Block ein **2-Bit-Farbindex** ist (2 aufeinanderfolgende Pixel). Das Panel liest 2 Pixel pro Byte.

### 8.2 `GET /sleep`
| Richtung | Inhalt |
|-----------|--------|
| Request | `GET {base}/sleep` |
| Response 200 | JSON: `{ "current_time": "YYYY-MM-DD HH:MM:SS", "next_wakeup": "...", "sleep_duration": <ms> }` |

### 8.3 `GET|POST /setting` (nur Web-Browser, nicht ESP32)
- **GET**: rendert `settings.html` (Config + Batterie %).
- **POST**: Formular -> validiert (Rotation 0/90/180/270) -> **schreibt `config.yaml`** -> `update_app_config()` (Watcher + Globals) -> Redirect.

### 8.4 `GET /`
- Redirect auf `/setting`.


> **Auth:** `x-api-key` wird **nur** fuer die **Immich**-Requests vom *Server* gesetzt. ESP32<->Server-Requests sind **unauthentifiziert** (LAN-Vertrauensmodell, s. §10.4).

### 8.5 Payload-Details

- Bitmap: 800·480 Pixel × 2 Bit = **exakt 96 000 Bytes** (= `BUFFER_SIZE` der Firmware).
- ASCII-Encoding: jede Zahl `00..FF` + `,`, nach je 16 Zahlen ein Newline; Trailer `};` + Newline (wörtlich der letzte Token).
- Gesamtlänge = 96 000×3 + 6 000 Newlines + 3 Trailer = **294 003 Bytes (≈ 287 KiB)** pro Bild – das ist der eigentliche „Verkehr“ pro Refresh (kein GZIP).
- ⚠ Der Trailer `};` erzeugt **ein zusätzliches** `SendData(0x00)` nach den 96 000 gültigen Bytes (s. §10.7.4).

---

## 9. Konfigurationsdatenschema

### 9.1 Struktur von `config.yaml`

```yaml
immich:
  url: "http://192.168.1.10"   # Immich-Basis-URL (nur Leer-Check, kein Format-Check)
  album: "default_album"       # exakte Übereinstimmung mit dem Immich-Albumnamen nötig
  rotation: 270                # 0 | 90 | 180 | 270 (die einzig hart validierte Größe)
  enhanced: 1.3                # Sättigung, PIL ImageEnhance.Color, 0.0..2.0
  contrast: 0.9                # Kontrast, PIL ImageEnhance.Contrast, 0.0..2.0
  strength: 0.8                # Dithering-Intensität 0.0..1.0 (intern weiter ×0.75)
  display_mode: "fill"         # "fit" (Letterbox/weiß) | "fill" (Cover/Crop)
  image_order: "random"        # "random" | "newest"
  sleep_start_hour: 23
  sleep_start_minute: 0
  sleep_end_hour: 6
  sleep_end_minute: 0
  wakeup_interval: 60          # Minuten; UI-Angebote: 30,60,120,180,240,360,480,720,1440
```

### 9.2 Felder, Wirkung & Anker

| Feld | Art | Wo wirkt | Anmerkung |
|------|-----|----------|-----------|
| `url` | string | Immich-API-Base | leer → `500` bei `/download`; TLS-Entscheidung (`https://`-Prefix) erfolgt auf der ESP32-Seite |
| `album` | string | Auswahlquelle **und** `tracking.txt` (1. Zeile) | Albumwechsel ⇒ Tracking-Reset (alle Assets gelten als ungezeigt) |
| `rotation` | int | `load_scaled` | **nach** `ImageOps.exif_transpose` → EXIF- und Config-Rotation addieren sich |
| `enhanced`/`contrast` | float | zwischen `load_scaled` und Dithering | `1.0` = unverändert |
| `strength` | float | Atkinson-Fehlerdiffusion | 0 → reines Nearest-Color (Banding), 1 → volles Dithering |
| `display_mode` | enum | `load_scaled` | `fit` = weißer Rand; `fill` = Zentrier-Crop |
| `image_order` | enum | Asset-Auswahl | `newest` nutzt die Album-Reset-Heuristik (neues Foto ⇒ Tracking leeren + absteigende Wiedergabe) |
| `sleep_start_*`/`sleep_end_*` | int (h/min) | `/sleep`-Berechnung | Fenster darf Mitternacht überspannen (im Code korrigiert); **Zeitzone = Container-Local (Docker-Default = UTC!)** |
| `wakeup_interval` | int (min) | `/sleep`-Grid | `interval·(⌊minuten/interval⌋+n)` mit 1-Tages-Wrap; <10 min ⇒ Sprung auf Slot+2 |

### 9.3 Schreibzugriff & Persistenz

- **Primärer Pfad:** `POST /setting` (Web-UI) → Validierung → `yaml.safe_dump` nach **`/config/config.yaml`** (Container-Pfad; README zeigt **kein** Volume-Mount-Beispiel, ohne das Einstellungen beim Image-Rebuild verloren gehen) → sofort `update_app_config()`.
- **Sekundärer Pfad:** Datei per Hand ändern → **watchdog** `on_modified` → `load_config()` → `update_app_config()`. Beides landet in denselben Globals + `app.config` → **Hot-Reload** ohne Neustart.
- `ConfigFileHandler.ensure_config_exists()` legt bei fehlender Datei `DEFAULT_CONFIG` an – der Server startet **auch ohne Konfiguration** (mit Default-Album `default_album` ⇒ in der Praxis 404/500, bis `album` korrekt gesetzt ist).
- **Zustand pro Instanz:** eine Config = ein Album = ein `tracking.txt` (unter `$IMMICH_PHOTO_DEST`, Default `/photos`). Mehrere Frames gegen denselben Server **teilen** diese Datei (keine Sperre, s. §10.5.2).

---

## 10. Bemerkungen, Schwachstellen & offene Punkte

> Gefunden durch Abgleich README ↔ Code ↔ Firmware. „Kritisch“ = beeinflusst Funktion/Energie; „kosmetisch“ = Doku/Konsistenz.

### 10.1 Fehler- & Robustheitsverhalten (kritisch)

1. **Neustart-Loop bei Wi-Fi-Ausfall:** `begin()==false` (kein bekanntes Netz erreichbar, Portal-Timeout, …) → Display weiß → `delay(30 s)` → `ESP.restart()`. Ohne erreichbares WLAN **bootet der Rahmen ca. alle 35 s neu** (je EPD-Init, Scan, 3 Connect-Attempts, Display weiß). Über Tage der größte Energiefresser – ein exponentieller Retry-Sleep wäre die saubere Lösung.
2. **Portal: nur ein Connect-Versuch.** In `startPortal()` wird nach dem `/connect`-POST **einmal** `connect(ssid,pass)` (15 s Timeout) probiert; bei Fehlschlag `break` aus der Warteschleife, Soft-AP abreißen, `false` zurück. Keine Retry-Logik im Portal (existiert nur in `autoConnect`). Schwache Netzwerke fallen dadurch öfter in den 10.1.1-Loop, als man erwarten würde.
3. **Kritisch-laden-Zweig weckt per Knopf nicht:** Im `< 3050 mV`-Zweig wird **nur** `esp_sleep_enable_timer_wakeup(86400 s)` aktiviert, **kein** EXT1-GPIO – der Weckknopf ist während dieser 24 h wirkungslos (vermutlich unbeabsichtigt; ein LOW-Pegel am Pull-up-Pin zieht praktisch keinen Strom).
4. **Timeout vs. 500 bei `/download` – zwei sehr unterschiedliche Pfade:** (a) **Timeout** (Client-Timeout = 50 s; großer RAW + lange Dithering-Phase auf schwacher NAS-CPU): `GET` liefert Fehler-Code → `break`, kein Retry → **24-h-Schlaf** (Default). (b) **Persistenter 500** (z. B. Album/URL nie konfiguriert): der äußere While-Loop wird bei jedem 500 neu gesetzt → **endlose 10-s-Polls ohne Schlaf** – das ist ein stiller, permanenter Akku-Burner, der nur durch Server-Fix (200) endet. Der Autoren-Kommentar „will retry once“ beschreibt dies nicht korrekt.
5. **Kein Payload-Healthcheck:** Die Firmware parst blind. Ein 200-Response, dessen `Content-Length` 0/negativ ist, macht `processImageData` abbrechen; das Display behält das letzte Bild (akzeptabel), es gibt aber **keinen Rückkanal, ob** ein Refresh erfolgreich war (vgl. §10.9, kein Telemetrie).

### 10.2 README ↔ Code (Fakten & Kosmetik)

| Thema | README/Doku | Code | Fazit |
|-------|------------|------|------|
| Setup-Long-Press | „~5 s“ / „at least 5 second“ | `BUTTON_HOLD_TIME=3000` (3 s) | Text anpassen |
| `config.yaml` | „no longer needed“ + Beispiel mit `enhanced: 1.5` | Defaults `1.3/0.9/0.8`, `rotation 270`, `fill`, `random` | Beispiel ≠ Code-Default |
| Server-URL | drei Varianten: `http://192.168.100.10:15151` (Install), `http://server.ip:15001` (config.h), `http://192.168.1.10` (DEFAULT_CONFIG) | – | Platzhalter vereinheitlichen |
| Docker-Env | `-e IMMICH-API-KEY=` (Strich) | `os.getenv("IMMICH_API_KEY")` (Unterstrich) | funktioniert nur dank Docker-Normalisierung `-`→`_` |
| Button-Pin | Wiring-Kommentar `SETTING <> 27` | `CONFIG_PIN=2`, `WAKEUP_PIN=GPIO_NUM_2` | nur GPIO 0–15 RTC-fähig → **2** korrekt, 27 ist Doku-Fehler |
| Update-Dauer | „within 15 seconds“ **und** „within 30 seconds“ | – | einheitlich formulieren |

### 10.3 Timing & Energie (kritisch/mittel)

1. **RTC-Drift ungekorrigiert:** ESP32-RTC driftet ~±20 ppm (±1,4 min/24 h). `SLEEP_TIME_COMPENSATION=1.009` ist **definiert, aber nirgends angewandt** – der Autor kannte das Problem, hat es nur nicht verkabelt. Da der *Server* die Dauer nach *seiner* Uhr berechnet, addiert sich Server-Drift + RTC-Drift: das Wakeup-Raster weicht über Tage um Minuten vom Raster ab (für Fotos unkritisch, für exakte Zeitfenster relevant).
2. **Zeitzone:** Alle Schlaf-/Wakeup-Berechnungen nutzen die **Container-Systemzeit** (Docker-Default = UTC). Nutzer in UTC+1/+2 müssen `TZ` im Container setzen, sonst schläft das Gerät aus Nutzerperspektive 1 h versetzt. README schweigt dazu.
3. **„NTP-Sync“ ist Kosmetik:** `run_daily_ntp_sync()` *liest* `pool.ntp.org` täglich um 04:11 und *protokolliert* das Ergebnis – **setzt aber keine Systemuhr** (kein `settimeofday`/`clock_settime`). Die Behauptung „korrigiert ungenaue NAS-Uhr“ trifft so **nicht** zu; für korrekte `sleep_duration` muss die *Host*-Uhr stimmen.
4. **Energiebilanz:** ~16 µA Deep-Sleep laut README (Maßwert; plausibel – Datenblatt-Order der ESP32-Deep-Sleep ist ~7–20 µA). **Parametrierter Tagesverbrauch** (Annahmen: 1-Ah-LiPo, 60-min-Raster, Schlaf 23:00–06:00 ⇒ 17 Wake/Tag): **Wake-Zyklus** = WiFi-Connect ~100 mA × 5 s + Download-Phase ~120 mA × 15 s + EPD-Full-Refresh ~250 mA × 3 s ≈ **~1 mAh/Zyklus** → ~17–20 mAh/Tag; **Sleep** = 0,4–8 mAh/Tag je nach Board-Leckage (Debug-/UART-Rails abgeregelt vs. versorgt – bei FireBeetle-Boards der entscheidende Faktor um Faktor 10); **Selbstentladung** ~3 mAh/Tag (10 %/Monat). Summe: **≈ 20–30 mAh/Tag ⇒ 1 Ah LiPo hält ~3–6 Wochen**. Der 500-Poll-Fehlerfall (10.1.4) kostet dagegen ~8 640 GET-Versuche/Tag × ~0,05 mAh ≈ **~0,4–0,5 Ah/Tag ⇒ 1 Ah in ~2–3 Tagen** – die Fehlermoden dominieren die reale Laufzeit bei weitem. Messbar per USB-Netzspannungs-/Strommessgerät am Board; die Annahmen (bes. die Peak-Strome) sind Richtwerte, nicht Hardware-verifiziert.
5. **Batteriemessung:** `readBatteryVoltage()` = 50 ADC-Lesungen à 5 ms (~250 ms + Overhead) **pro Aufruf**; `downloadImage()` fährt **zusätzlich eine komplett tote Schleife** (`plusV` 50×ADC, Ergebnis wird nie gesendet – der Header nutzt den separaten `readBatteryVoltage()`) ⇒ ~2× unnötige ADC-Arbeit pro Wakeup. Zudem setzt die Rechnung einen **exakten 2:1-Spannungsteiler** voraus (anderer Teiler → falsche mV → falsche % im Web-UI); die `BATTERY_LEVELS`-Tabelle ist LiPo-typisch, aber die ADC-Messunsicherheit wird nicht kompensiert.

### 10.4 Sicherheit

1. **`/setting` & `/download` ungeschützt:** kein Auth, kein Rate-Limit, kein CSRF. Jeder LAN-Teilnehmer kann (a) Einstellungen ändern und (b) `/download` in einem Loop feuern → Server-CPU-Last + (indirekt) Akku-Drain durch permanente Wakeups. Im eigenen LAN tolerierbar, für „cloud server“ (README-Formulierung) **nicht**.
2. **WLAN-PSKs & Server-URL unverschlüsselt** in NVS (`wificaptive`, `data`). Wer das Board physisch hat, hat die Heimnetz-Passwords.
3. **`WiFiClientSecure::setInsecure()`** – TLS mit verworfener Zertifikatsprüfung: gegen MitM nur Placebo, schützt aber die PSKs zumindest im Transit.
4. **Docker läuft als root** (kein `USER`), kein `HEALTHCHECK`.

### 10.5 Server-intern (mittel)

1. **Flask-Dev-Server single-threaded** (Werkzeug-Default `threaded=False`, kein Gunicorn/uWSGI). Ein `/download` mit RAW-Dekodierung + Atkinson (30–90 s) **blockiert jede parallele Anfrage** (Web-UI, zweites Gerät). Für ein Frame ok; für mehrere Frames + UI ein Engpass.
2. **Race bei mehreren Frames:** `tracking.txt` hat **keine Datei-Sperre**; zwei gleichzeitige `/download` können dasselbe „unseen“-Set sehen → **doppeltes Foto**. Im sequenziellen Betrieb unkritisch.
3. **RAM-Budget pro `/download`-Request (gerechnet/verifiziert):** (i) Original-Datei komplett im RAM – `response.content` nach `stream=True` ignoriert das Streaming faktisch (24-MP-JPG ~5–15 MB, 24-MP-RAW ~50–80 MB, 100-MP-RAW ~250 MB); (ii) **Vollauflösungs-Dekodierung *vor* dem Downscale**: PIL/rawpy-RGB = 24 MP → 72 MB, 100 MP → 300 MB; (iii) **De-Palettierungs-Peak:** `pixels[:, :, None, :] − palette_array[None, None, :, :]` promoviert `uint8 − int64` zu **int64** (NEP 50 – empirisch unter numpy 2.x bestätigt) → die Ketten (480,800,6,3) kosten je **55,3 MB** (Differenz + Quadrat) bzw. **18,4 MB** (Summe + Sqrt→float64) ⇒ **~150 MB peak pro 800×480-Bild**. Realistischer Gesamt-peak moderner RAW: **~150–450 MB** pro Refresh – der Container (NAS-RAM) ist danach zu dimensionieren; mildert wird es nur durch das Single-Thread-Design (max. ein Peak gleichzeitig).
4. **`/photos`-Volume** hält nur `tracking.txt` (alles andere in-memory) – wer ein Foto-Archiv auf dem Volume erwartet, wird nicht fündig.
5. **`depalette_image`-Index-Shift:** `indices[indices>3] += 1` (Kommentar: „Simulate the code from the C“) lässt **Palette-Slot 4 (= Waveshare `EPD_7IN3E_RED`) nie zu** und verschiebt 4→5, 5→6 (Slot 6 = Binär `110`, im Header gar nicht definiert). Zusammen mit §10.6 ergibt sich die **Farb-Frage**:

6. **Immich-API-Vertrag – fragile Feld-Kopplung (wichtigster Server-Risiko-Knoten):**
   - **`GET /api/albums`** wird per `item['albumName'] == <config>` gefiltert. Der offiziell dokumentierte Immich-`AlbumDto`-Feldname ist jedoch **`name`** – ob die Ziel-Instanz `albumName` liefert, hängt von Immich-Version/Build ab und ist **hier nicht gegen eine Live-Instanz verifiziert**. Fehlt das Feld, wirft der Generator `KeyError` → 500. **Verifikations-Vorschlag:** einmal `curl -H "x-api-key: …" {immich}/api/albums` und die Schlüssel der Elemente inspizieren.
   - **Fehlauswirkung-Kategorien:** (a) *benigne* (→ 24-h-Schlaf): nur die zwei expliziten 404s „Album not found“/„No images found“; (b) **schlecht** (→ **endloser 10-s-Poll-Loop**, s. 10.1.4): *jedes* Nicht-200 aller drei Aufrufe (`/api/albums`, `/api/search/metadata`, `/api/assets/{id}/original` – auch 401/404!), KeyError auf `id`/`albumName`/`originalPath`, jeder rawpy-Dekodier-Fehler. D. h. die Fehlerfläche ist systematisch **gegen den Poll-Pfad verzerrt** – ein kaputter API-Key (→ 401 am ersten Aufruf ⇒ 500) bedeutet permanentes Pollen statt Schlafs.
   - **`exifInfo.dateTimeOriginal`** wird defensiv per `.get()` mit 1970-Fallback gelesen (crash-sicher), aber die Sortierung ist **stringbasiert** ⇒ einheitliches Format erforderlich; gemischte EXIF-/ISO-Formate würden die Reihenfolge *still* fälschen (kein Crash).
   - `originalPath` wird für die RAW/HEIC-Zweige hart benötigt (`.lower().endswith(...)`); fehlt es bei einem Asset ⇒ KeyError ⇒ 500 ⇒ Poll-Loop.

### 10.6 Farb-Pipeline-Inkonsistenzen (offen – visuell verifizieren!)

1. **Zwei Paletten, keine Übereinstimmung:**
   - `cpy.pyx` (Atkinson): reine Farben `(0,0,0) (255,255,255) (255,255,0) (255,0,0) (0,0,255) (0,255,0)` – reines Gelb/Rot/Blau/Grün.
   - `app.py` (modul-global `palette`, De-palettierung): `(0,0,0) (255,255,255) (255,243,56) (191,0,0) (100,64,255) (67,138,28)` – angenäherte Waveshare-E6-Werte.
   - Die Dithering-Fehlerdiffusion referenziert **die reinen** Farben, die finale Slot-Zuordnung hingegen **die annähernden** – zwei logisch verschiedene Paletten in zwei Phasen. Weil die Entfernungen klein sind, deterministisch – aber fragil.
2. **Slot-Mapping vs. Waveshare-Labels (Kernpunkt):** Laut `epd7in3e.h` (Waveshare-Referenz) gilt `2=GREEN, 3=BLUE, 4=RED, 5=YELLOW`. Das Mapping (De-Palette-Index + `indices[indices>3] += 1`) legt die Farben konkret so auf die Panel-Slots:

| Intendierte Farbe (app.py-Palette) | De-Palette-Index | nach +1-Shift | Panel-Slot (Label lt. Header) | Binär |
|---|---|---|---|---|
| Schwarz `(0,0,0)` | 0 | 0 | `000` BLACK | 000 |
| Weiß `(255,255,255)` | 1 | 1 | `001` WHITE | 001 |
| Helles Gelb `(255,243,56)` | 2 | 2 | `010` **GREEN** | 010 |
| Dunkles Rot `(191,0,0)` | 3 | 3 | `011` **BLUE** | 011 |
| Blau-Violett `(100,64,255)` | 4 | 5 | `101` **YELLOW** | 101 |
| Grün `(67,138,28)` | 5 | 6 | `110` **nicht im Header definiert** (111 = „CLEAN/Afterimage“) | 110 |

**Rot-Slot 4 (`100`) wird nie ausgegeben** und die vier chromatischen Farben landen jeweils auf Slots, die laut Waveshare-Header eine **andere** Farbe bezeichnen. Sind die Waveshare-Labels korrekt, zeigt das fertige Bild die **chromatischen Farben systematisch vertauscht** (Gelb erscheint grün usw.). Mögliche Deutungen: (a) Labels im .h stimmen → sichtbarer Farb-Bug; (b) bewusste Vermeidung eines „schlechten“ Slots (der C-Reference-Kommentar deutet darauf); (c) WIP-Fehler. **Empfehlung:** einmal gegen eine Waveshare-Testkarte fotografisch abgleichen; danach das Mapping gezielt entwirren oder explizit dokumentieren.

### 10.7 Firmware-Details & Dead Code

1. **`hibernate()`-Default = 24 h, nicht `SLEEP_INTERVAL`:** `sleep_interval = sleepDuration>0 ? sleepDuration : 86400;` → der `else`-Zweig mit `SLEEP_INTERVAL` ist **unerreichbar**; „Wi-Fi fehlgeschlagen ⇒ 24 h schlafen“ (u. a. nach 10.1.1 und nach jeder fehlgeschlagenen Refresh). Tot-Code-Konstanten: `SLEEP_INTERVAL`, `MIN_SLEEP_TIME`, `PREFERENCES_SLEEP_TIME_KEY`, `PREFERENCES_LAST_SLEEP_TIME`, `PREFERENCES_CONNECT_API_RETRY_COUNT`, `WIFI_CHANNEL` (es wird ein zufälliger Kanal 1–10 gezogen).
2. **`autoConnect`-Skip-Logik:** Im Fallback-Loop wird Slot 0 explizit übersprungen (`ssid == _savedWifis[0].ssid && pswd == _savedWifis[0].pswd`), obwohl Slot 0 nach `saveWifiCredentials()` gerade das **zuletzt verwendete** Netz ist ⇒ das frische Netz wird im Scan-Pfad **ausgelistet** (kommt erst beim nächsten Boot über `last_used_index` wieder vor). Subtiler Bug, praktisch selten relevant.
3. **ArduinoJson `StaticJsonDocument<200>`** = API v6; das README verlangt nur „ArduinoJson“ (v7 würde nicht kompilieren). Version-Pinning fehlt.
4. **Trailer `};` → Extra-`SendData(0x00)`:** Nach den 96 000 Bitmap-Bytes parst die Firmware das Token `"};"` (`,`/`\n` sind Delimiter, `}`/`;` nicht) → `strtol("};",16)=0` → **ein zusätzliches 0x00** (Pixel-Paar = Slot 0 = schwarz) nach dem Bild. Entweder ignoriert der Controller den Überlauf, oder das erste Pixel-Paar wird überschrieben – **visuell wahrscheinlich irrelevant, gehört aber entfernt** (z. B. Server-End `";\n"` statt `"};\n"`, oder Parser-Abbruch bei `}`).
5. **`processImageData`-Speicher:** die dominante Allokation ist das **einmalige `malloc(96 000)`**, das über das gesamte Bild gehalten und erst am Ende `free()`-t wird. Der Parser-`String hexBuffer` ist dagegen winzig und wiederverwendet (max. ~2–3 Zeichen, da der Server nach je 2 Hex-Ziffern ein `,` schickt) → **kein** relevantes Fragmentierungsrisiko. **Budget-Perspektive (Klassik-ESP32 mit 520 kB SRAM):** nach WLAN-STA-Boot (FreeRTOS + `esp_wifi` + `lwIP` + Arduino-Core, keine Heap-Tuning-Optionen im Code) sind typischerweise **~250–350 kB** Heap frei → der 94-kB-Block ist damit **~⅓ des freien Heaps** (einmalig, nur in der Download-Phase; deep sleep resettet den Heap pro Wake-Zyklus). Kontiguität ist in der 512-kB-Region realistisch, da innerhalb eines Zyklus kaum größere Blöcke konkurrieren. **Fehlschlag ist „harmlos-lautlos“:** `malloc==NULL` → `false` → `success=false` → `hibernate()` = **24-h-Schlaf ohne Bild-Update und ohne sichtbares Feedback** (Display zeigt das letzte Bild); erst bei wiederholtem Scheitern wäre das Muster erkennbar. Mess-Empfehlung: `ESP.getFreeHeap()` bzw. `heap_caps_get_largest_block(MALLOC_CAP_INTERNAL)` nach `WiFi`-Connect ausgeben, um die reale Reserve auf der Ziel-Hardware zu bestätigen.
6. **PIN 27 vs. 2:** Nur RTC-fähige GPIOs (0–15) können `esp_sleep_enable_ext1_wakeup` bedienen; der Taster **muss** an GPIO2 (Pull-up, LOW = Pressed) – die Wiring-Tabelle `SETTING <> 27` ist irreführend. Konsequenz: es gibt **keinen zweiten freien Weck-Pin**; jeder Zusatz-Input braucht Multiplexing.
7. **SPIFFS:** `fs_init()`/`fs_deinit()` rahmen den kompletten Boot-/Sleep-Zyklus (Fehler bei `SPIFFS.begin(true)` ⇒ `ESP.restart()`-Loop!), aber **niemand liest/schreibt Dateien** (der ganze `fs_*`-Körper außer init/deinit ist kommentiert). Die SPIFFS-Partition (je nach 4-MB-Scheme typ. ~1,5 MB) ist damit **dauerhaft reserviert, aber ohne jeden Write** → null Flash-Wear; das einzige echte Risiko ist ein **korrupter** SPIFFS (z. B. nach Stromloss im Update), der den oben genannten Neustart-Loop bei jedem Boot auslösen kann – ein klassisches Feld-Fehlverhalten, das leicht übersehen wird (Heilung: Neuflash/Formattierung der Partition). Unnötiger Boot-Overhead; kann komplett raus.
8. **Duplizierte EPD-Logik:** `EPD_7IN3E_Display()`/`_part()` existieren, werden aber nie aufgerufen (das Ino streamt selbst per `SendCommand(0x10)`+`SendData`) – zwei Quellen der Wahrheit, unnötige Wartungskosten.
9. **`update()` endet immer mit `hibernate()`**, auch nach erfolgreicher `downloadImage()` – faktisch tot, da `downloadImage()` intern bereits `esp_deep_sleep_start()` erreicht (die nie zurückkehrt). Defensive Redundanz, ok.

### 10.8 Build / Repo-Hygiene (kosmetisch, aber wartungsrelevant)

1. **`cpy.so` (1,9 MB) + `cpy.c` (1,4 MB) sind im Repo committet.** Das Dockerfile baut das Modul **nicht** neu (`COPY .` + nur `pip install`); das Image setzt den **vorkompilierten** `.so` voraus ⇒ nur auf der Quellplattform lauffähig. **Direkt aus dem ELF-Header bewiesen:** `e_machine=0x3E (x86_64)`, 64-bit little-endian, `ET_DYN`, gelinkt gegen `libc.so.6` (glibc, kein libpython – normales C-Extension-Modul, `PyInit_cpy`), Build-Relikte `python3.9` / `x86_64-linux-gnu` / `/workspaces/EPF/.venv` → **CPython-3.9-venv auf 64-bit Debian/Ubuntu** (CI-artiges Build-Environment). Konsequenzen: (a) **ARM-NAS** (Synology-ARM, Raspberry Pi) → Import schlägt **still** fehl (ImportError ⇒ Server-Crash); (b) `setup.py` + `Cython` in requirements würden einen Rebuild erlauben, der Dockerfile nutzt sie aber nicht. → Empfehlung: Build-Stage im Dockerfile (`pip wheel .`) + Multiarch.
2. **`python:3.9-slim` ist EOL** (April 2025), während `numpy 2.0.2`/`pillow 11` modern sind – Versions-Scherbe, die nur wegen des externen `.so` funktioniert.
3. **Kein `.gitignore`** (darum landen `cpy.c`/`cpy.so` im Tree), keine Tests, kein CI, kein `HEALTHCHECK`; das README-Docker-Beispiel zeigt **kein** `v /host/config:/config`-Volume.
4. **Provenienz:** Captive-Portal „mostly modified from TRMNL“ (eigener Lizenzkontext – klären), Waveshare-Header (MIT), FireBeetle-BD; bei einem öffentlichen Repo sauber in `THIRD_PARTY.md`/Attribution dokumentieren.

### 10.9 Feature-Lücken / WIP-Kanten

- **Datums-Overlay komplett auskommentiert** (der Code – `draw_text_with_background`, rotierter Text, DejaVu-Schrift – ist vorhanden) – README nennt das Feature nicht mehr, der Code wartet.
- **Kein Partial-Refresh** (`EPD_7IN3E_Display_part` ungenutzt) – immer Vollbild. EPD-Wear-Leveling: bei 1-h-Refresh = 24 Full-Refresh/Tag; bei E6-Zyklen-Lifetime unkritisch, bei kürzeren Intervallen relevant.
- **Kein Push-Modell:** Server → Frame nur via Polling; kein Trigger-Kanal („neues Foto im Album ⇒ sofort wecken“). Frames mit 30-min-Interval zeigen neue Fotos im schlechtesten Fall 30 min später.
- **Ein Album, eine Instanz, ein `tracking.txt`** – keine Multi-Frame-Orchestrierung (10.5.2), keine per-Frame-Profile.
- **Kein OTA/Update-Mechanismus** für die Firmware; kein A/B-Partitioning.
- **Keine Telemetrie nach außen** (nur Batteriespannung); fehlgeschlagene Refreshs sind vom Gerät nur per Serial sichtbar.
- **`EPD_7IN3E_CLEAN` (0x7)** ungenutzt – Waveshare markiert die Farbe als Afterimage-Betroffen; das aktuelle „weiß machen“ nutzt korrekt `EPD_7IN3E_WHITE`.

---

## 11. Fazit

**EPF** ist ein durchdachtes und für das Ziel (E-Paper-Rahmen, minimaler Stromverbrauch) **im Kern stimmiges** System: Die Architektur-Entscheidung „Server macht 100 % der Bilddatenarbeit, ESP32 ist ein 96-kB-Byte-Streamer“ ist elegant, reduziert die Gerätekomplexität auf eine **≈ 2 200 Zeilen** große Firmware (davon ~550 der Waveshare-EPD-Treiber/SPI-Layer; ~1 670 eigenes Projekt-Code inkl. Captive-Portal), die gut wartbar bleibt, und hält die Refresh-Zeiten im Sekundenbereich. Captive-Portal (TRMNL-geleitet), Cython-Pipeline (Atkinson), Deep-Sleep-Diskipline (Radio hart aus, RTC-Double-Wecker) und das Tracking-Modell sind **alle funktional vorhanden** und gut gegeneinander abgedichtet.

Die Schwächen konzentrieren sich fast vollständig auf die **Fehler- und Randlagen**: Neustart-Loops statt Retry-Sleep, ein faktisch wirkungsloses „NTP-Sync“, unkorrigierter RTC-Drift, ungesicherte HTTP-Endpunkte, lockfreies `tracking.txt` – und vor allem **die offene Farb-Slot-Frage** (§10.6), die den visuellen Kern berührt und die einzige ist, die das „Es funktioniert“ grundsätzlich in Frage stellt. Dazu kommen typische WIP-Artefakte: Dead Code, README-/Code-Divergenzen, committete Binaries ohne Multiarch-Build.

**Empfohlene nächste Schritte (nach Priorität):**

1. §10.6 verifizieren: Farb-Slots gegen Waveshare-Testkarte fotografisch prüfen → Mapping entwirren oder dokumentieren (entscheidet, wie der Rahmen *aussieht*).
2. Robustheit: exponentielle Retry-Sleeps statt Neustart-Loop; EXT1-Wecker auch im Low-Voltage-Zweig; Portal-Retrys.
3. Minimal-Security: Token/Rate-Limit auf `/download`+`/setting`, `threaded=True`/Gunicorn; `TZ` + `/config`-Volume in README/Dockerfile.
4. Build: Dockerfile mit Cython-Build-Stage (Multiarch), Python-Basis aktualisieren, `.gitignore` + binäre Artefakte aus dem Repo nehmen.
5. Aufräumen: Dead Code (Konstanten, `plusV`-Loop, SPIFFS, `};`-Trailer), README-Tabellen (Knopf/Pins/Env-Namen) mit Code abgleichen, Provenienz dokumentieren.
6. (Optional) WIP-Features abschließen: Datums-Overlay, Push-Trigger, Partial-Refresh, Telemetrie/OTA.

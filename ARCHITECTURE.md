# Architecture & Interfaces

How the three parties talk to each other: **Immich** (where the photos live),
**EPF** (the Dockerised glue/proxy that does all the work), and the **ESP32**
(the physical frame). Read this alongside the README (product view) and
`TESTSPEC.md` (verification view).

```
                 x-api-key                     LAN  HTTP(S)
                ┌──────────────┐             ┌─────────────────────────┐
  photos        │   IMMICHE    │              │  EPF  (Flask, Docker)   │
 ─────────────►│  v3 REST API  │◄────────────│  GET /download          │
                └──────────────┘  /download, │  GET /sleep             │
                            ▲              │  POST /setting (Web-UI)    │
                            │              ▼                             │
                       (auth)   ┌─────────────────────┐     hibernate   │
                                 │   ESP32 FRAME       │─────────────────┘
                                 │  WROOM + 7.3" E-Ink │  (wake on RTC /
                                 │  + battery + button │   button)
                                 └─────────────────────┘
```

## Roles

- **Immich** — the photo store. EPF is its *client*. Only the original image
  bytes plus the album metadata come out of here.
- **EPF** — the brain. A small Flask app (runs in Docker) that, on each device
  request: resolves the album, pages through the Immich v3 search API, downloads
  one original, resizes/crops/rotates it, dithers it to the 16-level E-Ink
  palette (Atkinson), and returns it as a C array. It is also the source of
  truth for the sleep window and reports battery health via the response. **All
  processing happens here**, on the cheap x86 server — the frame stays dumb.
- **ESP32** — a thin HTTP client + display driver. It wakes (RTC alarm or
  button), fetches one frame + a sleep duration over LAN, drives the panel, and
  hibernates. It stores the server address (`SERVER_BASE_URL`) and Wi-Fi
  profiles in NVS (written by the captive portal). It performs **no**
  image processing itself.

---

## Interface A — EPF ⇄ Immich  (v3 REST API)

Transport: plain HTTP(S) to the Immich box; **auth via the `x-api-key` header**
(key needs the `asset` / `asset.download` permissions). Base URL =
`IMMICH_URL`; target album = `IMMICH_ALBUM` (tests) / `config['album']` (runtime).

| Step | Call | Purpose / key fields |
|------|------|-----------------------|
| 1 | `GET /api/albums` | Album catalogue. EPF matches one by **name** to resolve the album `id`. |
| 2 | `POST /api/search/metadata` | Body: `{ "albumIds": [id], "size": <chunk>, "page": <n>, "withExif": true }`. Response: `{ total, items:[ { id, originalFileName, exifInfo: { dateTimeOriginal } }, … ], hasNextPage, nextPage }`. EPF **loops while `hasNextPage`** — this is the v3 pagination contract that replaces the retired `GET /api/albums/{id}`. |
| 3 | `GET /api/assets/{id}/original` | The raw image bytes for the chosen asset. |

Result: an ordered list of `{id, captureTime, name}` (sorted newest / oldest /
random per `image_order`), one is picked, then processed locally. No photo data
ever touches the ESP32 directly.

---

## Interface B — ESP32 ⇄ EPF  (device ↔ Flask, over the LAN)

The device keeps a base URL in NVS (e.g. `http://192.168.x.y:15151`, written by
the captive portal) and speaks ordinary HTTP(S) to it. Two endpoints matter:

### `GET {base}/download`  — fetch one ready-to-show frame

- **Request header the device sends:** `batteryCap: <millivolts>` — the voltage
  it just measured on the battery (GPIO34, 12-bit ADC, ×2 divider). The server
  folds this into its battery reporting.
- **Response `200`:** body = the dithered frame as a **flat, comma-separated
  stream of 16-level palette indices** (one per pixel, `text/plain`,
  Content-Length = the byte count). The firmware streams the body straight into
  the EPD controller, turns the display on, and sleeps the panel.
- **Response `202`** (server busy/rendering): device waits `RETRY_DELAY` and
  retries. **Response `500`**: device retries once, then gives up.
- **Response header `X-Photo-Url`:** the Immich original URL of the photo just
  shown (used for the planned NFC-tag feature — the firmware does not read it
  yet).

### `GET {base}/sleep`  — how long to sleep before the next wake

- **Request header:** `Accept: application/json`.
- **Response `200` (JSON):**
  ```json
  { "current_time": "12:30", "next_wakeup": "06:00", "sleep_duration": 203400000 }
  ```
  `sleep_duration` is in **milliseconds**; the device divides by 1000 to get
  seconds and calls `hibernate(seconds)`. Inside the night sleep window this is
  the time to the next allowed wake; outside it it is ~0 (short sleep → next
  `wakeup_interval` tick).

### Wi-Fi provisioning (captive portal)

First boot: the ESP32 runs a SoftAP. The browser lands on a captive page where
you enter the **server address** and **Wi-Fi profiles** (SSID + password, a few
slots). These are persisted to NVS as `WIFI_SSID_KEY(n)` / password pairs plus
`SERVER_BASE_URL`. Re-entering the settings page = hold the button ≥ 5 s at
reboot. (See README → ESP32.)

---

## One display cycle (end to end)

1. RTC alarm fires (every `wakeup_interval` minutes) **or** the button wakes the
   frame.
2. If the current time falls inside the **sleep window** → compute
   `sleep_duration` to the next permitted wake time and `hibernate` (panel off,
   near-zero draw). No network traffic.
3. Otherwise read the **battery voltage**.
4. `GET /download` (header `batteryCap: <mV>`) → stream the 16-level frame into
   the 7.3″ E-Ink panel (800 × 480), turn it on, let the panel sleep.
5. `GET /sleep` → `hibernate(sleep_duration)`. Repeat at step 1.

---

## Data formats (the two wire contracts)

- **Frame (EPF → ESP32):** `800 × 480 = 384 000` samples; each sample is a
  **16-level (4-bit) palette index** (0 = darkest … 15 = lightest) in the E-Ink
  palette. Serialized comma-separated, one value per pixel. Produced by
  `cpy.pyx` (`load_scaled` for rotate/crop/scale → Atkinson dither → `closestColor`
  onto the 16-colour `epd_colors` palette).
- **Sleep (EPF → ESP32):** the small JSON above.
- **Settings (browser → EPF):** `POST /setting`, a form-encoded `update()` call
  carrying the visible UI fields (url, album, rotation, enhanced, contrast,
  strength, display_mode, image_order, sleep hours/minutes, wakeup interval) plus
  the hidden `batteryCap`. Persisted to `config.yaml`.

---

## Invariants to keep in sync (the coupling points)

These are the seams where a change on one side silently breaks the other —
treat them as contracts:

1. **Frame serialization.** What `cpy.pyx` emits must match how
   `Arduino/epd7in3e.ino :: processImageData()` parses it. Both assume the same
   **800 × 480 geometry** and the **16-level palette**. Changing colour depth,
   dithering, or the token/byte packing on either side must be re-validated
   end-to-end (the firmware feeds the EPD byte-wise via `SendData`).
2. **Sleep semantics.** The server decides *when* to sleep (single source of
   truth: the sleep window in `config.yaml`); the device only *obeys*
   `sleep_duration`. Don't let the device grow its own schedule.
3. **Album identity.** EPF resolves the album by **name**; Immich must expose
   that album and the `x-api-key` must carry read/download rights.
4. **Auth boundary.** The Immich `x-api-key` is only ever used **server-side**
   (EPF → Immich). The ESP32 authenticates with nothing — so the EPF service
   must stay reachable only on the trusted LAN/VLAN.

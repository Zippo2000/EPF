# Architecture & Interfaces

How the three parties talk to each other: **Immich** (where the photos live),
**EPF** (the Dockerised glue/proxy that does all the work), and the **ESP32**
(the physical frame). Read this alongside the README (product view) and
`TESTSPEC.md` (verification view).

```
                x-api-key                      LAN  HTTP(S)
               ┌──────────────┐              ┌──────────────────────────┐
 photos       │    IMMICHE    │              │  EPF  (Flask, Docker)   │
─────────────►│  v3 REST API  │◄─────────────│  GET /download           │
               └──────────────┘   /download, │  GET /sleep              │
                          ▲              │  POST /setting (Web-UI)      │
                          │              ▼                               │
                     (auth)   ┌─────────────────────┐   hibernate       │
                              │    ESP32 FRAME       │───────────────────┘
                              │  WROOM + E630S panel │  (wake: RTC / button)
                              │  + battery + button  │
                              └─────────────────────┘
```

## Roles

- **Immich** — the photo store. EPF is its *client*; only original image bytes and
  album metadata come out of here.
- **EPF** — the brain. A small Flask app in Docker that, on each device request:
  resolves the album, pages through the Immich **v3** search API, downloads one
  original, resizes/crops/rotates it, **dithers it toward the 6-colour Spectra-6
  palette (Atkinson)**, and serves it as a packed hex byte-stream. It is the single
  source of truth for the sleep window and reports battery health. **All processing
  happens here, on the cheap x86 server — the frame stays dumb.**
- **ESP32** — a thin HTTP client + display driver: a FireBeetle ESP32-E driving a
  **7.3″ Waveshare E630S “Spectra 6” 6-colour e-paper (800 × 480)**, plus a battery
  and a button. It wakes (RTC alarm or button), fetches one frame and a sleep
  duration over LAN, drives the panel, and hibernates. It stores the server address
  (`SERVER_BASE_URL`) and Wi-Fi profiles in NVS (written by the captive portal) and
  does **no** image processing itself.

---

## Interface A — EPF ⇄ Immich  (v3 REST API)

Transport: HTTP(S) to the Immich box; **auth via the `x-api-key` header** (needs
read + `asset.download`). Base = `IMMICH_URL`; target album = `IMMICH_ALBUM` (tests)
/ `config['album']` (runtime).

| Step | Call | Purpose / key fields |
|------|------|-----------------------|
| 1 | `GET /api/albums` | Album catalogue. EPF matches one by **name** to get the album `id`. |
| 2 | `POST /api/search/metadata` | Body: `{ "albumIds": [id], "size": <chunk>, "page": <n>, "withExif": true }` → `{ total, items:[{ id, originalFileName, originalPath, exifInfo:{ dateTimeOriginal } }], hasNextPage, nextPage }`. EPF **loops while `hasNextPage`**. This replaces the retired, non-versioned `GET /api/albums/{id}` — the v3 pagination contract. |
| 3 | `GET /api/assets/{id}/original` | Raw image bytes of the chosen asset (RAW/HEIC are decoded server-side). |

Result: an ordered list of `{id, captureTime, name}` (sorted newest / oldest / random
by `image_order`), one picked, processed locally. **No photo data ever reaches the
ESP32 directly.**

---

## Interface B — ESP32 ⇄ EPF  (device ↔ Flask, over the LAN)

The device keeps a base URL in NVS (e.g. `http://192.168.x.y:15151`, written by the
captive portal) and speaks ordinary HTTP/S to it. Two endpoints matter.

### `GET {base}/download`  — fetch one ready-to-show frame

- **Request header from the device:** `batteryCap: <millivolts>` — the battery
  voltage just measured (GPIO34, 12-bit ADC × voltage-divider). The server folds it
  into its battery report.
- **Response `200`:** the dithered frame as `text/plain`, `Content-Disposition:
  attachment; filename="image_<asset_id>.c"`. See the frame format below. The
  firmware streams the body into the panel, turns the display on, and puts the panel
  to sleep. Response header **`X-Photo-Url`** carries an Immich photo deep-link for
  the just-shown photo (planned NFC-tag feature; the firmware does not read it yet).
- **Error statuses the server can return:** `404` (album not found / no images) and
  `500` (config, fetch or download failure).
- **Firmware retry policy:** on `500` it waits and retries **once**; it also tolerates
  a `202` (accept) by waiting and retrying, though the current EPF server does not
  emit one — so that branch is defensive. Any other code aborts.

### `GET {base}/sleep`  — how long to hibernate before the next wake

- **Request header from the device:** `Accept: application/json`.
- **Response `200` (JSON)** — the only field the firmware actually consumes is
  `sleep_duration`; the other two are informational:
  ```json
  {
    "current_time": "2025-05-14 12:30:00",
    "next_wakeup":  "2025-05-15 06:00:00",
    "sleep_duration": 203400000
  }
  ```
  Timestamps use `%Y-%m-%d %H:%M:%S`; `sleep_duration` is in **milliseconds**. The
  device divides by 1000 to get seconds and calls `hibernate(seconds)`. Inside the
  night sleep window this is the time to the next allowed wake; outside it it is ~0
  (short sleep → next `wakeup_interval` tick).

### Wi-Fi provisioning (captive portal)

First boot: the ESP32 runs a SoftAP; the browser lands on a captive page where you
enter the **server address** and **Wi-Fi profiles** (SSID + password, a few slots),
persisted to NVS as `WIFI_SSID_KEY(n)`/password pairs plus `SERVER_BASE_URL`.
Re-open the settings page by holding the button ≥ 5 s at reboot. (See README → ESP32.)

---

## One display cycle (end to end)

1. An RTC alarm (every `wakeup_interval` minutes) or the **button** wakes the frame.
2. If the current time is inside the **sleep window** → compute `sleep_duration` to
   the next permitted wake time and `hibernate` (panel off, near-zero draw). No
   network traffic.
3. Otherwise read the **battery voltage**.
4. `GET /download` (header `batteryCap: <mV>`) → stream the frame bytes into the
   800 × 480 panel, turn it on, let the panel sleep.
5. `GET /sleep` → `hibernate(sleep_duration)`. Repeat at step 1.

---

## Data formats (the two wire contracts)

- **Frame (EPF → ESP32).** The E630S is a **6-colour “Spectra 6”** panel. Each pixel
  is quantized to one of the six palette colours
  (black / white / yellow / red / blue / green) as a **4-bit index**. **Two pixel
  nibbles are packed into one byte** (even column in the high nibble, odd in the low),
  so `800 × 480 = 384 000` pixels become **192 000 bytes**. Those bytes are serialized
  as **two-digit uppercase-hex tokens** (`00`–`FF`), comma-separated, with a newline
  every 16 tokens and a closing `};` (≈ 588 KB). Built by `app.py:
  convert_to_c_code_in_memory()` (→ `depalette_image()` for the 6-colour mapping) on
  the Atkinson-dithered image from `cpy.pyx`. The firmware parses each token with
  `strtol(…, 16)` and feeds the panel **one byte at a time** — a direct match.
- **Sleep (EPF → ESP32):** the small JSON above (consumed field: `sleep_duration`).
- **Settings (browser → EPF):** `POST /setting`, a form-encoded `update()` call with
  the visible UI fields (url, album, rotation, enhanced, contrast, strength,
  display_mode, image_order, sleep hours/minutes, wakeup interval) plus a hidden
  `batteryCap`. Persisted to `config.yaml`.

---

## Invariants to keep in sync (the coupling points)

The seams where a change on one side silently breaks the other — treat as contracts:

1. **Frame serialization is a tight, bidirectional contract.** `cpy.pyx` /
   `app.py::convert_to_c_code_in_memory` **emit** two 4-bit pixel indices per byte as
   hex; `Arduino/epd7in3e.ino :: processImageData()` **parses** each hex token and
   writes one byte per `SendData`. Both sides must agree on the **800 × 480 geometry**,
   the **4-bit (6-colour) index width**, and the **packing/byte order**. Change colour
   depth, packing, or dithering on one side and re-flash/re-deploy the other together.
2. **Sleep ownership.** The server decides *when* to sleep (single source of truth:
   the window in `config.yaml`); the device only *obeys* `sleep_duration`. Don’t let
   the device grow its own schedule.
3. **Album identity.** EPF resolves the album by **name**; the Immich key must have
   read/download rights and that album must exist.
4. **Auth boundary.** The Immich `x-api-key` is used **only server-side** (EPF →
   Immich). The ESP32 presents **no credentials** — so keep the EPF service reachable
   only from a trusted LAN/VLAN.

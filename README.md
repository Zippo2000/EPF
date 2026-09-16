# E-paper ESP32 Frame - ESP32-E

- **This project is currently a Work in Progress (WIP)!**

This project leverages **Immich** as a service for organizing albums and photos. Photos intended for display are grouped into specific albums, and a FLASK server hosted on a NAS or cloud server handles image cropping and editing before sending them to the ESP32. Since the ESP32 remains in deep sleep most of the time, and all image processing is handled by the server, the EPD updates photos very quickly, typically within 15 seconds. This significantly reduces power consumption.

## Features

- **Captive portal**: By long-pressing setup button on the ESP32 when boot up, the device enters setup mode, allowing the Wi-Fi setup page to store up to five SSIDs. This enhances mobility and makes it easier to switch between networks.
Mostly modifieded from TRMNL WiFiCaptive[https://github.com/usetrmnl/firmware/tree/main/lib/wificaptive]
- **Fully Automated Photo Management**: Manage photos through Immich without additional manual processes; photos will automatically sync to the frame.
- **Implementation of Atkinson Dithering**
- **Ultra-low Power Consumption**: As all image processing and quantization are handled by the server, the device only consumes ~16µA during deep sleep, with photo updates completed within 30 seconds.
- **Customizable Display**: Configure photo orientation, basic color adjustments, album name, and more through the server webpage.
- **Cython impelementation**: Use Cython to significantly accelerate photo processing, achieving up to a 5x speed boost.
- **HTTPS supported**: ESP32 now can connect to secured server.
- **Sleep time impelementation**: ESP32 will enter deep sleep during the specified sleep period.
- **One button**: When the ESP32 is in deep sleep, a short press of the setting button will wake it up and restart the process, while a long press(~5s) of the setting button during boot will enter setting mode.

## Table of Contents

- [Components](#components)
- [Installation](#installation)
- [Testing](#testing)
- [Architecture & Interfaces](ARCHITECTURE.md)
- [Security: secret scanning (pre-commit)](#security-secret-scanning-pre-commit)
- [License](#license)

## Components

> How the three parts (Immich · EPF · ESP32) exchange data is documented in
> **[ARCHITECTURE.md](ARCHITECTURE.md)** — hardware roles, the two wire contracts, and the
> invariants to keep in sync.

- [FireBeetle 2 ESP32-E](https://wiki.dfrobot.com/FireBeetle_Board_ESP32_E_SKU_DFR0654)
- [7.3-inch E Ink Spectra 6 (E6) Full Color E-Paper Display Module + HAT](https://www.waveshare.com/7.3inch-e-paper-hat-e.htm)
- Picture frame: A standard picture frame that accommodates the e-paper frame.
- Li-Po battery with PH2.0 header
- Simple button for wake and setting or use integrated button on ESP32-E

## Installation

### Clone the Repository

```bash
$ git clone https://github.com/Zippo2000/EPF.git
```

### Manually Build Docker Image

```bash
$ git clone https://github.com/Zippo2000/EPF.git
$ docker build -t Zippo2000/epf .
```

### Run the Container

**Option A – docker-compose (recommended):**

```
cp .env.example .env   # then set IMMICH_API_KEY in .env
docker compose up -d
```

**Option B – docker run:**

Note: the environment variable uses **underscores** (`IMMICH_API_KEY`), not hyphens.

```bash
docker run --name epf \
  -e IMMICH_API_KEY='<your-immich-api-key>' \
  -e TZ=Europe/Berlin \
  -v $(pwd)/config:/config \
  -v $(pwd)/photos:/photos \
  -p <replace-port>:5000 \
  -d Zippo2000/epf
```

### Configure `config.yaml` (no longer needed, configure the settings directly from webpage)
<details>
Below is an example of a configured `config.yaml` file:

```yaml
immich:
  # Album name, must match the album name created in Immich
  album: testAlbme
  # Photo rotation angle, accepts only (0, 90, 180, 270)
  rotation: 270
  # Immich server URL
  url: http://192.168.100.36:2283
  # Color(Saturation) enhancement level using PIL's ImageEnhance.Color (1.0 = original level)
  enhanced: 1.5
  # Contrast level using PIL's ImageEnhance.Contrast (1.0 = original level)
  contrast: 1.2
```
</details>

### ESP32

Connect the EPD, ESP32, Li-Po battery, and setting button according to the correct wiring configuration. 
To run the code follow the following steps:

1. Install and set up Arduino IDE
2. Connect your ESP32
3. Rename the Arduino folder from the repo to `epd7in3e`
4. Open the `epd7in3e.ino` file
5. Install following libraries from Arduino library manager:
  5-1. Arduinojson
  5-2. Async TCP
  5-3. ESP Async Web Server
6. Click 'Upload'
7. Connect to the Wifi AP created by the ESP32, named `ESP32_ePAPER`
8. A captive portal shows up allowing to enter your WiFi details and details of the Docker container (e.g. http://192.168.100.10:15151)

You can re-enter the configuration page later by short-circuiting the setting button at least 5 second while rebooting.

## Testing

Two suites live in `tests/` (spec in [`TESTSPEC.md`](TESTSPEC.md)). Because the server
imports the Cython-built `cpy.so` (a Linux binary), both run inside a Docker container —
the scripts below build and start it for you.

**Offline (no network, no Immich) — 29 tests:**
```bash
sh scripts/run-tests.sh
```
Covers battery-voltage→percent mapping, config handling (`load_config` never returns `None`,
deep-copy isolation), settings-page rendering, the `/sleep` contract, the **newest/random ordering** guard (all-already-shown → reset), and the **6-colour payload/dither** contract.

**Live (against a real Immich v3 server) — 4 tests:**
```bash
sh scripts/run-live-tests.sh
```
Reads `IMMICH_URL`, `IMMICH_ALBUM` and `IMMICH_API_KEY` from your local, git-ignored
`.env` (see [`.env.example`](.env.example)). Pass a server/album to override the `.env`
values: `sh scripts/run-live-tests.sh http://<host>:2283 <album>`. These verify the v3 API
contract (album lookup, paginated `search/metadata` fetch, original download) and the full
`/download` pipeline end-to-end. Live tests skip cleanly when no `.env`/server is available.

## Security: secret scanning (pre-commit)

A `gitleaks` **pre-commit hook** scans every staged change and **blocks the commit** if it looks like it contains a secret (API keys, tokens, passwords, …). This is what guards against leaking things like an Immich API key or a LAN address into the repo. It uses gitleaks' built-in ruleset, runs in a pinned Docker image, and needs no per-machine binary install.

**Install (once, after cloning):**

```bash
sh scripts/install-hooks.sh
```

This points Git's `core.hooksPath` at the committed `.githooks/` directory, so every `git commit` runs the scan. Requirements: Docker (with Docker Desktop running on Windows) and the `zricethezav/gitleaks:v8.18.4` image (pulled automatically on first use).

**Behaviour:**
- clean changes → commit proceeds silently;
- a potential secret is found → the commit is **blocked** with a `file:line [rule]` pointer;
- no Docker available → the scan is **skipped with a warning** (commit allowed). Set `GL_STRICT=1` to make that a hard failure instead.

**Bypass a single, confirmed false positive:**

```bash
git commit --no-verify
```

**Note for contributors:** never paste real credentials (e.g. the Immich API key) into tracked files. Put them in the local, git-ignored `.env` file (see `.env.example`) instead. The scanner is a backstop, not a substitute for that habit.

## License

This project is licensed under the MIT License.


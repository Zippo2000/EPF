# AGENTS.md

Guidance for **AI coding agents** (and human newcomers) working in this repo.
Humans: start with [`README.md`](README.md). This file answers "how do I build,
test, and not break this project?" It follows the open [AGENTS.md](https://agents.md)
format — any agent that auto-loads `AGENTS.md` (Codex, Copilot, Aider, Cursor,
Gemini CLI, …) can use it as-is. Where it conflicts with an explicit instruction in
chat, the chat wins; the nearest `AGENTS.md` in the directory tree wins over this one.

---

## What this project is

**EPF ("ePaper Photo Frame")** is a two-piece system:

- **The server** — a small **Flask app (`app.py`)** packaged with Docker. It is the
  *brain*: on each device request it pulls a photo from a self-hosted **Immich**
  media server, scales/crops/rotates it, **dithers it (Atkinson) toward a 6-colour
  E-Ink palette** (in Cython, [`cpy.pyx`](cpy.pyx)), and serves the frame to the
  device over HTTP. It also answers a "how long should I sleep?" call and hosts a
  web page for configuring the *running* server.
- **The frame** — **ESP32** firmware in [`Arduino/`](Arduino/) (DFRobot
  FireBeetle ESP32-E + Waveshare 7.3″ "E630S Spectra 6" e-paper, 800×480, 6-colour).
  It is a thin client: wakes on RTC/button, `GET`s a frame and a sleep duration,
  drives the panel, hibernates. **It does no image processing.**

The exact wire contracts between all three (Immich ↔ EPF ↔ ESP32) are documented in
[`ARCHITECTURE.md`](ARCHITECTURE.md). **Read that before touching `app.py`, `cpy.pyx`,
or the firmware — several "obvious" changes break the other side.**

## Repository layout

```
app.py                  # the Flask server (all server-side logic; procedural, module-level)
cpy.pyx / cpy.c / cpy.so  # Cython image dithering. .so is a PREBUILT Linux binary (see below)
requirements.txt        # Python deps (Flask, requests, numpy, Pillow, pillow-heif, rawpy, Cython, ...)
Dockerfile              # builds the server image (python:3.9-slim). Does NOT compile cpy.so.
docker-compose.yml      # deploy: bind-mounts ./config + ./photos, requires IMMICH_API_KEY
pytest.ini              # pytest: testpaths=tests, addopts=-ra
templates/  static/     # Flask UI (settings page) and CSS

Arduino/                # ESP32 firmware (.ino + E-Paper driver + Wi-Fi captive portal)
scripts/                # run-tests.sh (offline), run-live-tests.sh (live), install-hooks.sh
tests/                  # pytest suite: test_battery/config/settings/sleep/ordering/payload/battery_display/geometry (offline) + test_live
.githooks/pre-commit    # gitleaks secret scanner (runs in a pinned Docker image)

README.md               # product docs + install + user guide (English)
ARCHITECTURE.md         # the three-way interface contract (English)
TESTSPEC.md             # test specification & coverage matrix (German)
ANALYSE.md              # deeper technical analysis (German)
.env.example            # committed config template (placeholders only)  -> .env is git-ignored
```

---

## Prerequisites

- **Docker** (Docker Desktop on Windows is fine; native Linux/macOS also work). Nearly
  every command below is a `docker` invocation — the app itself is **Linux-only** (see
  the `cpy.so` note under *Gotchas*).
- For the **live** tests only: a reachable **Immich v3** server with a working API key.

## Commands

> All commands are run from the **repository root**.

**Build the server image**
```bash
docker build -t epf:latest .          # uses Dockerfile (python:3.9-slim)
```

**Configure & run the server**
```bash
cp .env.example .env                   # then fill in the values
#   required: IMMICH_API_KEY
#   useful:   TZ=Europe/Berlin, EPF_PORT=15001
#   (test-only, not used by the running frame): IMMICH_URL, IMMICH_ALBUM
docker compose up -d                    # compose REFUSES to start if IMMICH_API_KEY is unset
```
The settings page (configure the *running* server) is at `http://<host>:<EPF_PORT>/setting`.

**Run the offline test suite** (no network, no Immich needed)
```bash
sh scripts/run-tests.sh
```

**Run the live integration suite** (needs `.env` with `IMMICH_URL`/`IMMICH_ALBUM`/`IMMICH_API_KEY`)
```bash
sh scripts/run-live-tests.sh                       # reads .env
sh scripts/run-live-tests.sh http://host:2283 eink  # ...or override via CLI args
```
See [Testing](#testing) for how to target a single test.

**(Re)build `cpy.so` from `cpy.pyx`** — *only if you changed the Cython code*. This is
**not** part of the Docker build; it is a manual, Linux-host step:
```bash
cython -3 -X bind=cpp cpy.pyx -o cpy.c
cc -shared -fPIC -O3 -I"$PYINC" -I"$NPINC" cpy.c -o cpy.so   # $PYINC/$NPINC = python & numpy include dirs
```
It **must** be built for the same CPython as the image (**3.9**) and on **Linux**, and
you must keep the committed `cpy.pyx` / `cpy.c` / `cpy.so` trio in sync. (The exact flags
depend on your toolchain — the repo does not script this build.)

**Flash the firmware** — no build in CI; open [`Arduino/epd7in3e.ino`](Arduino/epd7in3e.ino)
in the Arduino IDE (or PlatformIO), select board *FireBeetle 2 ESP32-E* (ESP32) and
upload. First power-on launches the Wi-Fi captive portal where the user enters the
server address and Wi-Fi profiles (stored in ESP32 NVS).

**Enable / bypass the secret scanner** (see [Security](#security--secrets))
```bash
sh scripts/install-hooks.sh        # one-time: points core.hooksPath at .githooks/
git commit --no-verify             # bypass a single commit if it's a confirmed false positive
```

---

## Configuration (two channels — do not confuse them)

This is the single biggest footgun in the project:

- **The running server reads ONLY its `config.yaml`** — which is (re)written by the web
  page `GET/POST /setting`. **It does not read `IMMICH_URL` / the album from the
  environment at runtime.** Do not "refactor" `app.py` to pull those from env vars; that
  is intentional (the device is configured through the UI, not the environment).
- **Environment variables are for *deployment* and *tests* only:**
  - `docker-compose.yml` consumes `IMMICH_API_KEY` (required), `TZ`, `EPF_PORT`.
  - `scripts/run-live-tests.sh` consumes `IMMICH_URL`, `IMMICH_ALBUM`, `IMMICH_API_KEY`.
- **`.env` is git-ignored.** Only the placeholder template [`.env.example`](.env.example)
  is committed. Never commit a real Immich key or a real LAN address (the pre-commit hook
  will block it — see next section).

---

## Testing

The suite is plain **pytest** scoped by [`pytest.ini`](pytest.ini) (`testpaths=tests`,
`addopts=-ra`). It always runs **inside a Docker container** — because `app.py` imports
the Linux-only `cpy.so` — and pytest is `pip install`ed at container start.

| Tier | File(s) | Covers | How to run | Needs Immich? |
|------|---------|--------|------------|--------------|
| **Offline** | `tests/test_battery.py`, `test_config.py`, `test_settings.py`, `test_sleep.py`, `test_ordering.py`, `test_payload.py`, `test_battery_display.py`, `test_geometry.py` | pure units: voltage→%, `load_config` never `None`, settings-page 500-safety, `/sleep` contract, battery display, `fit`/`fill` geometry | `sh scripts/run-tests.sh` | No |
| **Live** | `tests/test_live.py` | Immich **v3** contract (album-by-name, paginated `search/metadata`, original download) + the full `/download` pipeline + `X-Photo-Url` | `sh scripts/run-live-tests.sh` | Yes |

Key mechanics:

- The live tests **gate on `EPF_LIVE_TESTS=1`** and **degrade to `pytest.skip`** (not
  fail) when that flag is unset, when the connection details are missing, or when the
  server/album is unreachable. So a plain offline run is always green-or-skipping, never
  red — ideal for "is the build broken?" checks.
- **Run a single test** (offline example) by replicating the runner with a `-k` filter:
  ```bash
  docker build -q -t epf-tests:local .
  docker run --rm -e IMMICH_PHOTO_DEST=/tmp/x epf-tests:local \
       sh -c 'pip install -q pytest; cd /app && python -m pytest -k test_u02'
  ```
- `tests/conftest.py` holds the shared fixtures; `test_live.py` defines the `immich`
  fixture (auth + album-id resolution + pagination) that the four live tests consume.
- The full case catalogue, priorities, and coverage matrix live in
  [`TESTSPEC.md`](TESTSPEC.md). **When you change behaviour, add/update the matching test**
  even if not asked — that is the expected norm here.

---

## Code style & conventions

- **Python 3.9** (matches the Docker image). The server is **procedural**: module-level
  functions in `app.py`, not a big class. Keep it that way. English for comments/docstrings.
- **Per-pixel image work belongs in Cython** ([`cpy.pyx`](cpy.pyx)): scaling/rotation is
  Pillow, the dither is a hot loop, and the colour mapping is vectorised numpy. **Do not
  move the per-pixel loops into pure-Python in `app.py`** — that defeats the whole reason
  `cpy.so` exists.
- Config is a **Python dict (`DEFAULT_CONFIG`)** serialized to YAML; new settings are added
  there and mirrored in the `/setting` form + `templates/settings.html`.
- **Firmware is Arduino C++** (ESP32). Persistent state lives in NVS/Preferences under
  keys `SERVER_BASE_URL` and `WIFI_SSID_KEY(n)` — add new keys deliberately; the captive
  portal and the reader both enumerate them.
- **Docs language split:** code-facing docs are English (`README.md`, `ARCHITECTURE.md`);
  the analytical docs are German (`TESTSPEC.md`, `ANALYSE.md`). Match the file's existing
  language.

---

## Security & secrets

- **Pre-commit hook** [`.githooks/pre-commit`](.githooks/pre-commit) scans every *staged*
  change with **gitleaks' built-in ruleset**, running it in a **pinned Docker image
  (`zricethezav/gitleaks:v8.18.4`)** — no per-machine binary. It **blocks the commit** on a
  finding and prints `[rule] file:line`. Activate once with `sh scripts/install-hooks.sh`.
  Behaviour worth knowing: if Docker is unavailable it **fails soft (skips)** unless you set
  `GL_STRICT=1`; on Windows it resolves the repo root with `cygpath -w`.
- **Never commit** a real Immich API key, token, or a real LAN IP. Those belong only in the
  git-ignored `.env`. If you must bypass the hook once, use `git commit --no-verify`.
- **Trust boundary:** the Immich `x-api-key` is used *only server-side* (EPF→Immich). The
  ESP32 presents **no credentials**, so keep the EPF service reachable only from your trusted
  LAN/VLAN.
- **Line endings:** [`.gitattributes`](.gitattributes) forces `eol=lf` on `*.sh` and
  `.githooks/*` — a CRLF in the hook's shebang breaks it on Windows. Don't "fix" this back.
- **Exec bits on Windows:** Git here has `core.filemode=false`, so `chmod +x` doesn't reach
  the index. Executability of `scripts/*.sh` and `.githooks/pre-commit` is recorded via
  `git update-index --chmod=+x`.

---

## Gotchas (the non-obvious stuff)

- **`cpy.so` is a committed, prebuilt Linux ELF** (Cython-compiled from `cpy.pyx`). The
  `Dockerfile` does **not** rebuild it — it's just carried in by `COPY . /app/`. Consequences:
  the server **cannot run on a Windows/macOS host directly**, and a changed `cpy.pyx` requires
  a manual Linux rebuild (above) with `cpy.c`/`cpy.so` re-committed in lockstep.
- **There is no `Dockerfile.test`.** The test runners build the **normal** app image
  (`epf-tests:local` / `epf-tests:live`) and `pip install` pytest at container start.
- **The Immich client already speaks the v3 API**: album discovery is `GET /api/albums`
  (match by **name**), and asset listing is the **paginated `POST /api/search/metadata`**
  (loop on `hasNextPage`/`nextPage`). Do **not** reintroduce the retired, non-versioned
  `GET /api/albums/{id}`.
- **Sleep timing is server-authoritative** and driven by `datetime.now()` in the container's
  `TZ` (default `Europe/Berlin`). The base image has no timezone data by default; without the
  `TZ` env var the sleep/wake windows land in UTC and at the wrong hour.
- **Persisted state lives in the bind mounts** `./config` (settings + `tracking.txt`) and
  `./photos`. Recreating the container without these mounts silently resets settings to
  defaults and loses the shown-photo history.
- **`/download`** returns the frame as `text/plain` (2 pixel-nibbles/byte, 2-digit hex,
  newline every 16, closing `};`) and sets an **`X-Photo-Url`** response header; the firmware
  reads it for a planned NFC feature but doesn't act on it yet. Details in
  [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Further reading

- [`README.md`](README.md) — what it is, install, hardware assembly, user guide (English)
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the three-party interface contract + coupling invariants
- [`TESTSPEC.md`](TESTSPEC.md) — test cases, priorities, coverage (German)
- [`ANALYSE.md`](ANALYSE.md) — deeper technical analysis (German)

> **Monorepo note:** if the firmware grows its own conventions, drop an `AGENTS.md` inside
> [`Arduino/`](Arduino/). Agents read the *nearest* file, so it would override this one for
> firmware edits while leaving the server docs above intact.

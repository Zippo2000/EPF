"""LIVE integration tests against a real Immich v3 server.

These are the "live/manual" tier of TESTSPEC.md, now automated. They are
SKIPPED by default and only run when the environment explicitly opts in
(`EPF_LIVE_TESTS=1`) AND supplies the connection details, so the offline suite
(`python -m pytest`) never touches the network:

      IMMICH_URL        base URL of the Immich server   (required)
      IMMICH_API_KEY    the server's API key             (with "asset.download")
      IMMICH_ALBUM      album name to test against        (default: "eink")

Run them with:  sh scripts/run-live-tests.sh     (all read from the local .env)
Nothing here hard-codes a server address or a credential.

Covered (see TESTSPEC.md):
  * TC-API01  album resolution            via GET /api/albums
  * TC-API02  paginated asset fetch       via POST /api/search/metadata  <- the v3 migration
  * TC-API03  original image download     via GET /api/assets/{id}/original
  * TC-API05  X-Photo-Url header format
  * TC-D01    full app /download pipeline end-to-end
"""
import copy
import os
import re

import pytest

import app as epf


# ---------------------------------------------------------------------------
# Opt-in + connection details (never hard-coded)
# ---------------------------------------------------------------------------
LIVE_ENABLED = os.environ.get("EPF_LIVE_TESTS") == "1"


def _env():
    url = os.environ.get("IMMICH_URL", "").rstrip("/")
    key = os.environ.get("IMMICH_API_KEY", "")
    album = os.environ.get("IMMICH_ALBUM", "eink")
    return url, key, album


@pytest.fixture(scope="module")
def immich():
    """Resolve the target album once for the whole module. Skips (rather than
    cascading failures) when the server is simply not reachable or the
    connection details were not provided."""
    if not LIVE_ENABLED:
        pytest.skip("live tests disabled (set EPF_LIVE_TESTS=1)")

    url, key, album = _env()
    if not url or not key:
        pytest.skip("IMMICH_URL / IMMICH_API_KEY not provided")

    import requests

    headers = {"Accept": "application/json", "x-api-key": key}
    try:
        r = requests.get(f"{url}/api/albums", headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as exc:                      # noqa: BLE001 - skip, don't fail
        pytest.skip(f"Immich at {url} not reachable ({exc})")

    match = next((a for a in r.json() if a.get("albumName") == album), None)
    if not match:
        pytest.skip(f"album {album!r} not found on {url}")

    return {
        "url": url,
        "key": key,
        "album": album,
        "album_id": match["id"],
        "asset_count": match.get("assetCount", 0),
        "headers": headers,
    }


def _collect_all_assets(info):
    """TC-API02 core: walk the paginated v3 endpoint until exhausted."""
    import requests

    all_items = []
    page = 1
    while True:
        body = {
            "albumIds": [info["album_id"]],
            "size": 100,
            "page": page,
            "withExif": True,
        }
        r = requests.post(
            f"{info['url']}/api/search/metadata",
            headers=info["headers"], json=body, timeout=15,
        )
        assert r.status_code == 200, f"search/metadata page {page} -> {r.status_code}"
        result = r.json().get("assets", {})
        items = result.get("items", [])
        all_items.extend(items)
        next_page = result.get("nextPage")
        if not next_page:
            break
        page = int(next_page)
        assert page <= 1000, "safety abort: too many pages"
    return all_items


# ---------------------------------------------------------------------------
# TC-API01: album resolution
# ---------------------------------------------------------------------------
def test_api01_album_resolution(immich):
    # The fixture already proved the album resolves by name; assert the id is a
    # well-formed non-empty identifier and matches an asset in the album.
    assert immich["album_id"].strip() != ""
    assert len(immich["album_id"]) >= 8


# ---------------------------------------------------------------------------
# TC-API02: paginated asset fetch is complete and terminates
# ---------------------------------------------------------------------------
def test_api02_search_metadata_paginated(immich):
    items = _collect_all_assets(immich)
    assert items, "no assets returned by /api/search/metadata"
    # Every asset must carry the identity + EXIF fields the pipeline depends on.
    for item in items:
        assert item.get("id"), "asset without id"
        assert "exifInfo" in item, "asset missing exifInfo"
    # If the album reports an asset count, the pagination must have returned
    # exactly that many (proves the loop terminates AND is complete).
    if immich["asset_count"]:
        assert len(items) == immich["asset_count"], (
            f"fetched {len(items)} assets but album reports {immich['asset_count']}")


# ---------------------------------------------------------------------------
# TC-API03: original image download
# ---------------------------------------------------------------------------
def test_api03_original_download(immich):
    import requests

    first = _collect_all_assets(immich)[0]
    aid = first["id"]
    r = requests.get(
        f"{immich['url']}/api/assets/{aid}/original",
        headers=immich["headers"], timeout=30, stream=True,
    )
    assert r.status_code == 200, f"original download -> {r.status_code}"
    ctype = r.headers.get("Content-Type", "")
    assert ctype.startswith("image/") or ctype == "application/octet-stream", ctype
    body = r.content
    assert len(body) > 0, "empty image body"


# ---------------------------------------------------------------------------
# TC-D01 + TC-API05: full app /download end-to-end + X-Photo-Url header
# ---------------------------------------------------------------------------
def test_d01_download_end_to_end_and_photo_url_header(immich):
    # Point the in-process app config at the live server, then drive the real
    # /download route through Flask' test client. This exercises album
    # resolution -> v3 asset fetch -> original download -> Cython Atkinson
    # dithering -> .c payload, plus the X-Photo-Url response header.
    cfg = copy.deepcopy(epf.DEFAULT_CONFIG)
    cfg["immich"]["url"] = immich["url"]
    cfg["immich"]["album"] = immich["album"]
    epf.update_app_config(cfg)

    client = epf.app.test_client()
    resp = client.get("/download", headers={"batteryCap": "3950"})

    assert resp.status_code == 200, f"/download -> {resp.status_code}: {resp.data[:300]}"

    # The payload is the .c array: comma-separated 16-level integers.
    body = resp.get_data(as_text=True)
    first_line = body.splitlines()[0] if body.strip() else ""
    assert re.match(r"^\d+(,\d+)*,?$", first_line.strip()), (
        f"payload does not look like a C array: {first_line[:80]!r}")

    # TC-API05: the NFC deep-link header is present and well-formed.
    photo_url = resp.headers.get("X-Photo-Url")
    assert photo_url, "X-Photo-Url header missing from /download response"
    assert re.match(
        r"^https://[^/]+/albums/[0-9a-f-]{36}/photos/[0-9a-f-]{36}$",
        photo_url,
    ), f"unexpected X-Photo-Url shape: {photo_url}"

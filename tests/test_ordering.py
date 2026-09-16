"""TC-U03 / TC-U04: the "already-shown" ordering recovery (the P0 guard).

When every photo in the album has already been shown (or a newer photo arrives), the
tracker must be reset and the album restarted instead of raising IndexError. This
previously lived inline in the /download route (hard to test); it is now the pure
function app.choose_next_image, so it is testable fully offline (no Immich, no network).

Two layers:
  * unit        : drive choose_next_image() directly with hand-built asset lists;
  * integration  : drive the real Flask /download route with a stubbed `requests` module
                   so the album fetch short-circuits, while the ordering/reset/save
                   decisions (the P0 behaviour) are asserted.
"""
import app as epf

ASSETS = [
    {"id": "a1", "originalPath": "/a1.jpg", "exifInfo": {"dateTimeOriginal": "2020-01-03T00:00:00"}},
    {"id": "a2", "originalPath": "/a2.jpg", "exifInfo": {"dateTimeOriginal": "2020-01-02T00:00:00"}},
    {"id": "a3", "originalPath": "/a3.jpg", "exifInfo": {"dateTimeOriginal": "2020-01-01T00:00:00"}},
]


def _call(assets, downloaded, order):
    selected, need_reset = epf.choose_next_image(assets, set(downloaded), order)
    return selected, need_reset


# ---------------------------------------------------------------------------
# TC-U03 -- 'newest' order
# ---------------------------------------------------------------------------
def test_newest_all_already_shown_resets_and_picks_newest():
    sel, need_reset = _call(ASSETS, {"a1", "a2", "a3"}, "newest")
    assert need_reset is True, "tracker must reset when the album is fully shown"
    assert sel["id"] == "a1", "restart must resume at the newest photo"


def test_newest_no_history_picks_newest_and_resets():
    sel, need_reset = _call(ASSETS, set(), "newest")
    assert need_reset is True
    assert sel["id"] == "a1"


def test_newest_some_remaining_picks_newest_undownloaded_without_reset():
    sel, need_reset = _call(ASSETS, {"a1"}, "newest")      # a1 (newest) already shown
    assert sel["id"] == "a2"
    assert need_reset is False, "no reset while undownloaded photos remain"


# ---------------------------------------------------------------------------
# TC-U04 -- 'random' order (the guard must not crash / IndexError either)
# ---------------------------------------------------------------------------
def test_random_all_already_shown_resets():
    sel, need_reset = _call(ASSETS, {"a1", "a2", "a3"}, "random")
    assert need_reset is True, "fully-shown random album must reset and restart"
    assert sel["id"] in {"a1", "a2", "a3"}


def test_random_some_remaining_no_reset(monkeypatch):
    picked = {}

    def _fixed(seq):
        picked["ids"] = [a["id"] for a in seq]          # which pool was we offered?
        return max(seq, key=lambda a: a["id"])

    monkeypatch.setattr(epf.random, "choice", _fixed)
    sel, need_reset = _call(ASSETS, {"a1"}, "random")
    assert need_reset is False
    assert set(picked["ids"]) == {"a2", "a3"}, "random must pick only from undownloaded photos"
    assert sel["id"] in {"a2", "a3"}


# ---------------------------------------------------------------------------
# Integration: the real /download route, network stubbed, ordering asserted
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json = json_body

    def json(self):
        return self._json


class _FakeRequests:
    def __init__(self, album_assets, downloaded_ids):
        self._album_assets = album_assets
        self._downloaded_ids = downloaded_ids
        self.reset_calls = 0
        self.saved = []

    def get(self, url, headers=None, stream=False, **_):
        if url.endswith("/api/albums"):
            return _Resp(200, [{"id": "ALBUM_ID", "albumName": "eink"},
                                {"id": "OTHER", "albumName": "other"}])
        return _Resp(500)                             # any other GET (e.g. /original) -> fail fast

    def post(self, url, headers=None, json=None, **_):
        if url.endswith("/api/search/metadata"):
            return _Resp(200, {"assets": {"items": self._album_assets, "nextPage": None}})
        return _Resp(500)


class _Monkey:
    """Patch the app module (requests + tracker fns + config) for the duration of one
    /download call, then restore everything. `order` is the image_order the route reads."""
    def __init__(self, fake, order):
        self._fake = fake
        self._attrs = [
            ("requests", fake),
            ("load_downloaded_images", lambda: set(fake._downloaded_ids)),
            ("reset_tracking_file", lambda: fake.__setattr__("reset_calls", fake.reset_calls + 1)),
            ("save_downloaded_image", lambda a: fake.saved.append(a)),
        ]
        self._saved = {}
        for name, val in self._attrs:
            self._saved[name] = getattr(epf, name)
            setattr(epf, name, val)
        self._albumname_saved = epf.albumname
        self._cfg_saved = epf.current_config["immich"]["image_order"]
        epf.albumname = "eink"                          # matches the stubbed album name
        epf.current_config["immich"]["image_order"] = order

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        epf.albumname = self._albumname_saved
        epf.current_config["immich"]["image_order"] = self._cfg_saved
        for name, _ in self._attrs:
            setattr(epf, name, self._saved[name])


def _run_route(order, downloaded_ids):
    fake = _FakeRequests(ASSETS, downloaded_ids)
    m = _Monkey(fake, order)
    m.__enter__()
    try:
        resp = epf.app.test_client().get("/download", headers={"batteryCap": "3950"})
    finally:
        m.__exit__(None, None, None)
    return resp, fake


def test_route_all_shown_resets_then_saves_newest():
    _, fake = _run_route("newest", {"a1", "a2", "a3"})
    assert fake.reset_calls == 1, "fully-shown album must trigger exactly one tracking reset"
    assert fake.saved == ["a1"], "after the reset the newest photo must be recorded as shown"


def test_route_not_all_shown_newest_no_reset():
    _, fake = _run_route("newest", {"a1"})
    assert fake.reset_calls == 0, "no reset while the newest photo has already been shown"
    assert fake.saved == ["a2"]


def test_route_random_all_shown_resets():
    _, fake = _run_route("random", {"a1", "a2", "a3"})
    assert fake.reset_calls == 1
    assert fake.saved and fake.saved[0] in {"a1", "a2", "a3"}

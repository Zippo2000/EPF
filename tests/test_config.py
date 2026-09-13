"""TC-U01 / TC-U02: configuration handling.

* TC-U02 - `ConfigFileHandler.load_config()` must never return `None` (the
  previous bug: yaml.safe_load() returns None for an empty / comment-only file
  WITHOUT raising, which then blew up the caller with a TypeError).
* TC-U01 - `current_config` must be an independent deep copy of
  `DEFAULT_CONFIG`; mutating the live config must not corrupt the defaults that
  back the "Reset to Default" behaviour.
"""
import os
import tempfile

import app as epf


def _load_config_for(content):
    """Build a ConfigFileHandler without running __init__ side-effects, then
    point its config_path at a temp file with the given content."""
    handler = epf.ConfigFileHandler.__new__(epf.ConfigFileHandler)
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        handler.config_path = path
        return handler.load_config()
    finally:
        os.unlink(path)


# ---- TC-U02: load_config never returns None --------------------------------
def test_load_config_empty_file():
    assert _load_config_for("") == epf.DEFAULT_CONFIG


def test_load_config_comment_only():
    assert _load_config_for("# just a comment\n# another\n") == epf.DEFAULT_CONFIG


def test_load_config_missing_file():
    handler = epf.ConfigFileHandler.__new__(epf.ConfigFileHandler)
    handler.config_path = os.path.join(tempfile.gettempdir(), "definitely_not_here_xyz.yaml")
    assert handler.load_config() == epf.DEFAULT_CONFIG


def test_load_config_valid_returns_dict():
    content = 'immich:\n  url: "http://example.invalid:2283"\n  album: "x"\n'
    result = _load_config_for(content)
    assert isinstance(result, dict)
    assert result["immich"]["url"] == "http://example.invalid:2283"


# ---- TC-U01: deepcopy isolation --------------------------------------------
def test_current_config_is_an_independent_copy():
    assert epf.current_config is not epf.DEFAULT_CONFIG
    # The inner dict must be a distinct object too (this is the actual bug that
    # a shallow copy() would leave shared).
    assert epf.current_config["immich"] is not epf.DEFAULT_CONFIG["immich"]


def test_mutating_live_config_does_not_touch_defaults():
    original = epf.DEFAULT_CONFIG["immich"]["url"]
    try:
        epf.current_config["immich"]["url"] = "http://mutated.invalid"
        assert epf.DEFAULT_CONFIG["immich"]["url"] == original, (
            "DEFAULT_CONFIG was mutated through current_config - copy is not deep")
    finally:
        epf.current_config["immich"]["url"] = original  # restore

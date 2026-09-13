"""
Shared pytest setup.

`import app` has module-level side effects (it creates the photo directory and
the tracking file, and builds the Flask app). We make that safe and
deterministic before any test module imports `app`:

  * point IMMICH_PHOTO_DEST at a throw-away temp dir (so the module-level
    os.makedirs / tracking-file creation never touches the real tree),
  * put the project root on sys.path so `import app` and `import cpy` resolve
    regardless of how pytest is invoked.

This conftest is imported by pytest before the test modules, which is what we
rely on for the environment preparation to happen first.
"""
import os
import sys
import tempfile

# --- Environment (must run before `import app`) -----------------------------
os.environ.setdefault("IMMICH_API_KEY", "offline-test-key")
os.environ.setdefault("IMMICH_PHOTO_DEST", tempfile.mkdtemp(prefix="epf_test_photos_"))

# --- Make the project root importable (app.py / cpy.so both live here) ------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))          # .../tests
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)                     # project root
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

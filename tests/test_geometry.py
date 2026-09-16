"""TC-D04: display-mode geometry (fit vs fill).

Verifies that load_scaled produces the expected geometric output:
  fit  → letterboxed on a white background
  fill → fills the entire 800×480 frame (no letterbox)

A solid-colour source keeps its pixel values through LANCZOS resampling,
so the assertions are deterministic without needing a specific photo.
"""
import numpy as np
from PIL import Image

import app as epf


def _wide():
    """1200×400 solid grey (128) — aspect 3.0 ≠ target 800/480 ≈ 1.667."""
    return Image.fromarray(np.full((400, 1200, 3), 128, np.uint8), "RGB")


def test_fit_letterboxes():
    """fit: wide image is scaled to fit width, letterboxed top/bottom on white."""
    out = np.array(epf.load_scaled(_wide(), 0, "fit"))
    assert out.shape[:2] == (480, 800)
    assert (out[0, 0] == 255).all()             # corner = white letterbox
    assert not (out[240, 400] == 255).all()     # centre = content (grey 128)


def test_fill_has_no_letterbox():
    """fill: wide image is scaled to cover height, then center-cropped to 800 px — no bars."""
    out = np.array(epf.load_scaled(_wide(), 0, "fill"))
    assert out.shape[:2] == (480, 800)
    assert not (out[0, 0] == 255).all()        # corner = content, not letterbox
    assert not (out[0, -1] == 255).all()        # right edge = content too

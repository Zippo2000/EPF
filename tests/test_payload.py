"""TC-D01 (structure) / TC-D02: the .c payload produced by the real pipeline.

Offline (no network, no Immich). `depalette_image` + `convert_image_atkinson`
(Cython) + `convert_to_c_code_in_memory` are pure, so we feed a synthetic 800x480
image and pin the EXACT on-the-wire frame contract:

  * format   : uppercase-hex "XX" tokens, 16 per line, 192 000 data tokens, closing '};'
  * palette  : 6-colour Spectra-6  -> every 4-bit nibble lies in {0,1,2,3,4,5,6}
               (index 4 is remapped to 5, so a '4' nibble never actually appears)

This also records the historical trap: a 16-level (0..15) palette -- as an older
revision of TESTSPEC.md described -- would emit A-F nibbles and break any
"decimal-digit only" assertion. The frame is 6-colour, not 16-level.
"""
import re

import numpy as np
from PIL import Image

import app as epf

EXPECTED_TOKENS = 800 * 480 // 2        # 192 000 bytes (two 4-bit pixel indices per byte)
NIBBLE_CHARS = set("0123456")           # 6-colour Spectra-6 palette, indices 0..6


def _frame_payload_lines(text):
    """Parse a .c frame body into its list of per-line hex-token lists (dropping '};').

    Each data line is exactly 16 uppercase-hex tokens followed by a trailing comma; the
    final line is the closing ';}'. Returns the token lists so callers can validate them.
    """
    body = text.rstrip()
    assert body.endswith("};"), "payload must end with the C-array closing marker ';}'"
    data = [l for l in body.splitlines() if l.strip() and l.strip() != "};"]
    lines = [[t for t in l.split(",") if t] for l in data]       # drop the trailing-comma empty
    total = sum(len(x) for x in lines)
    assert total == EXPECTED_TOKENS, f"expected {EXPECTED_TOKENS} tokens, got {total}"
    return lines


def _palette_pixels_image(seed=1):
    """An 800x480 image whose pixels are drawn from the 6-colour palette (deterministic)."""
    pal = np.array(epf.palette, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    return Image.fromarray(pal[rng.integers(0, len(pal), size=(480, 800))], "RGB")


def _fixed_image():
    """A deterministic 800x480 RGB gradient used to drive the dithering determinism check."""
    yy, xx = np.mgrid[0:480, 0:800]
    return Image.fromarray(np.stack([xx, yy, (xx + yy) // 2], -1).astype(np.uint8), "RGB")


def test_frame_payload_format_offline():
    """TC-D01 (structure): a real 6-colour frame is well-formed to the byte."""
    data = epf.convert_to_c_code_in_memory(_palette_pixels_image()).getvalue().decode("utf-8")
    lines = _frame_payload_lines(data)
    for toks in lines:
        assert len(toks) == 16, toks
        for tok in toks:
            assert re.fullmatch(r"[0-9A-F]{2}", tok), f"bad token {tok!r}"
            assert set(tok) <= NIBBLE_CHARS, f"nibble outside the 6-colour palette: {tok!r}"


def test_dither_determinism_offline():
    """TC-D02: the Atkinson dither -> pack pipeline is bit-stable for identical inputs."""
    img = _fixed_image()

    def once():
        atk = epf.convert_image_atkinson(img, dithering_strength=epf.strength)
        return epf.convert_to_c_code_in_memory(Image.fromarray(atk)).getvalue()

    a, b = once(), once()
    assert a == b, "dithering pipeline is not deterministic across identical inputs"


def test_16level_palette_would_emit_letters():
    """Record the historical trap: a 16-level (0..15) palette is NOT decimal-digit-shaped --
    it packs to A-F hex and would break a '16-level / decimal only' assumption."""
    def pack(indices):
        h, w = indices.shape
        out = []
        for y in range(h):
            for x in range(0, w, 2):
                lo = indices[y, x + 1] if x + 1 < w else indices[y, x]
                out.append((indices[y, x] << 4) | lo)
        return [f"{v:02X}" for v in out]

    toks = pack(np.arange(16, dtype=np.uint8).reshape(1, 16))
    joined = ",".join(toks)
    assert not re.match(r"^\d+(,\d+)*,?$", joined), \
        "a 16-level payload must break a decimal-digit-only assertion"
    assert any(set(t) - NIBBLE_CHARS for t in toks), "expected A-F nibbles for a 16-level palette"

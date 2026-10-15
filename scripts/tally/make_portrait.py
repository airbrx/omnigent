#!/usr/bin/env python3
"""Draw Tally's placeholder avatar, so the asset in the tree can be regenerated.

    uv run python scripts/tally/make_portrait.py

This is the Airbrx logomark, an orange "A" on the same ``#101419`` ground as
Eva's mark, so the three agents sit together in the drawer. It is deliberately
not a likeness: nobody should invent a face for Tally. Abram will supply a
real portrait, and when he does it replaces
``omnigent/airbrx/tally/assets/tally-portrait.png`` and this script goes.

Modeled on ``scripts/eva/make_portrait.py``: ``#FD6C1D`` as the only accent,
``#FF8C42`` as its light end, the 1254px square.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

SIZE = 1254
GROUND = (16, 20, 25, 255)  # the ground Eva's mark samples from Iris's portrait
ACCENT = (253, 108, 29)
ACCENT_LIGHT = (255, 140, 66)

OUT = (
    pathlib.Path(__file__).resolve().parents[2] / "omnigent/airbrx/tally/assets/tally-portrait.png"
)


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _accent() -> Image.Image:
    """The brand gradient, top-left to bottom-right, as a fill source."""
    grad = Image.new("RGB", (SIZE, SIZE))
    px = grad.load()
    assert px is not None
    for y in range(SIZE):
        for x in range(SIZE):
            px[x, y] = _lerp(ACCENT, ACCENT_LIGHT, (x / SIZE + y / SIZE) / 2)
    return grad


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), GROUND)

    # The A, built from two legs and a crossbar rather than set in a typeface,
    # for the reason Eva's E is: at drawer size a shape stays legible.
    unit = SIZE / 24
    height = unit * 12
    width = unit * 11
    leg = unit * 2.4  # horizontal thickness of each leg
    bar = unit * 1.8
    cx = SIZE / 2
    top = (SIZE - height) / 2
    bottom = top + height

    mask = Image.new("L", (SIZE, SIZE), 0)
    pen = ImageDraw.Draw(mask)
    left_base = cx - width / 2
    right_base = cx + width / 2
    pen.polygon(
        [(cx - leg / 2, top), (cx + leg / 2, top), (left_base + leg, bottom), (left_base, bottom)],
        fill=255,
    )
    pen.polygon(
        [
            (cx - leg / 2, top),
            (cx + leg / 2, top),
            (right_base, bottom),
            (right_base - leg, bottom),
        ],
        fill=255,
    )
    bar_top = top + height * 0.6
    # The bar spans the inside of the legs at its height, and a little into them.
    half = (bar_top - top) / height * (width / 2)
    pen.rectangle((cx - half, bar_top, cx + half, bar_top + bar), fill=255)

    img.paste(_accent(), (0, 0), mask)
    return img


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(OUT, format="PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {img.size[0]}x{img.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

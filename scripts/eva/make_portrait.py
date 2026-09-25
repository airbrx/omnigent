#!/usr/bin/env python3
"""Draw Eva's avatar, so the asset in the tree can be regenerated rather than
trusted.

    python3 scripts/eva/make_portrait.py

Iris's avatar is a photorealistic portrait of a person. Eva's is a mark, and
that is a decision rather than a shortfall: an agent that drafts outreach a rep
sends under their own name should not also wear an invented human face. The two
still sit together in the drawer because they share a ground (sampled from
Iris's own, ``#101419``) and the one accent the brand allows.

Everything here comes from the airbrx design system: ``#FD6C1D`` as the only
accent, ``#FF8C42`` as its light end, the 1254px square Iris uses, and the
geometric letterform language of the wordmark. No gradient on a surface, one
accent, nothing decorative that does not carry meaning.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

SIZE = 1254
GROUND = (16, 20, 25, 255)  # sampled from iris-portrait.png so they sit together
ACCENT = (253, 108, 29)
ACCENT_LIGHT = (255, 140, 66)

OUT = pathlib.Path(__file__).resolve().parents[2] / "omnigent/airbrx/eva/assets/eva-portrait.png"


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _vertical_accent(box: tuple[int, int, int, int]) -> Image.Image:
    """The brand gradient, top-left to bottom-right, as a fill source."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    grad = Image.new("RGB", (w, h))
    px = grad.load()
    assert px is not None
    for y in range(h):
        for x in range(w):
            px[x, y] = _lerp(ACCENT, ACCENT_LIGHT, (x / w + y / h) / 2)
    return grad


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), GROUND)

    # The E, built from three bars rather than set in a typeface: at 32px in an
    # agent drawer a letterform reads as a shape, and a shape drawn to the grid
    # stays legible where rendered type goes muddy.
    unit = SIZE // 24
    arm = unit * 9  # the widest arm, and therefore the mark's width
    height = unit * 12
    bar_h = unit * 2
    stem_w = unit * 2
    radius = unit // 2  # the 12/16/20 radius language, scaled
    # Centred on both axes against the mark's own bounding box rather than the
    # stem, or it reads as leaning left at drawer size.
    left = (SIZE - arm) // 2
    top = (SIZE - height) // 2

    mask = Image.new("L", (SIZE, SIZE), 0)
    pen = ImageDraw.Draw(mask)

    # Vertical stem.
    pen.rounded_rectangle((left, top, left + stem_w, top + height), radius=radius, fill=255)
    # Three arms. The middle one is shorter, which is what makes an E an E
    # rather than a bracket, and it is the only asymmetry in the mark.
    for y, length in (
        (top, arm),
        (top + (height - bar_h) // 2, unit * 6),
        (top + height - bar_h, arm),
    ):
        pen.rounded_rectangle((left, y, left + length, y + bar_h), radius=radius, fill=255)

    accent = _vertical_accent((0, 0, SIZE, SIZE))
    img.paste(accent, (0, 0), mask)
    return img


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(OUT, format="PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {img.size[0]}x{img.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

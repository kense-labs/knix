#!/usr/bin/env python3
"""Derive the whole repos/artwork/logos set from the official Kense marks.

Brand contract (../kense/kstudio/AGENTS.md, "Brand icons (don't improvise)"):
  scripts/make-brand-icons.py in kstudio is the single source of truth.
  TARGET_RATIO = 0.604, BADGE = 824 in a 1024 canvas must never change.
  Never hand-edit the shipped PNG / icns / ico / icon.svg assets.

This script does NOT edit those assets. It reads the byte-identical copies in
docs/brand/ and derives the size variants required by the third-party
artwork Makefile, using the same Lanczos resampling the official generator
uses for icon-512 / icon-128. No ratio, badge, colour or motif is altered.

  python3 scripts/make-knix-logos.py --check   # read-only drift check
  python3 scripts/make-knix-logos.py --apply   # write the derived assets
"""
from __future__ import annotations

import argparse
import io
import pathlib
import sys
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "brand"
LOGOS = ROOT / "repos" / "artwork" / "logos"

# Published constants from ../kense/kstudio/scripts/make-brand-icons.py.
# Documented here for traceability; this script never changes them.
TARGET_RATIO = 0.604
BADGE = 824

VARIANTS = [
    "Knix_logo",
    "Knix_logo_darkbg",
    "Knix_logo_horizontal",
    "Knix_logo_horizontal_darkbg",
    "Knix_logo_horizontal_mono",
    "Knix_logo_mono",
    "Knix_logomark",
    "Knix_logomark_mono",
]
PNG_DIRS = {
    "png_16": 16, "png_32": 32, "png_48": 48, "png_64": 64,
    "png_128": 128, "png_256": 256, "png_512": 512,
}
ICNS_VARIANTS = [
    "Knix_logo", "Knix_logo_darkbg",
    "Knix_logo_mono", "Knix_logomark", "Knix_logomark_mono",
]


def master_for(variant: str) -> pathlib.Path:
    """Light-background marks use the dark K; darkbg variants use the light K."""
    return BRAND / ("mark-dark.png" if "darkbg" in variant else "mark-light.png")


def target_size(variant: str, dirname: str):
    horizontal = "horizontal" in variant
    if dirname in PNG_DIRS:
        n = PNG_DIRS[dirname]
        return (n, round(n * 0.28)) if horizontal else (n, n)
    k = 1 if dirname == "png_1x" else 2
    if horizontal:
        return (2500 * k, 700 * k)
    if "logomark" in variant:
        return (575 * k, 575 * k)
    return (700 * k, 700 * k)


def render_png(variant: str, size) -> bytes:
    w, h = size
    src = Image.open(master_for(variant)).convert("RGBA")
    side = min(w, h)
    mark = src.resize((side, side), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(mark, ((w - side) // 2, (h - side) // 2), mark)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def render_ppm(w: int = 80, h: int = 80) -> bytes:
    """Plain P3 PPM, matching the boot_logo.ppm target of the artwork Makefile."""
    im = Image.open(BRAND / "icon-1024.png").convert("RGB")
    im = im.resize((w, h), Image.LANCZOS)
    lines = ["P3", "%d %d" % (w, h), "255"]
    raw = im.tobytes()
    for y in range(h):
        lines.append(" ".join(str(b) for b in raw[y * w * 3:(y + 1) * w * 3]))
    return ("\n".join(lines) + "\n").encode("ascii")


def plan():
    """Yield (relative path, expected bytes, kind) for every derived asset."""
    svg_bytes = (BRAND / "icon.svg").read_bytes()
    icns_bytes = (BRAND / "kstudio.icns").read_bytes()

    for variant in VARIANTS:
        yield pathlib.Path("src") / (variant + ".svg"), svg_bytes, "svg"
        yield pathlib.Path("svg") / (variant + ".svg"), svg_bytes, "svg"
        for dirname in list(PNG_DIRS) + ["png_1x", "png_2x"]:
            size = target_size(variant, dirname)
            yield (pathlib.Path(dirname) / (variant + ".png"),
                   render_png(variant, size), "png")
    for variant in ICNS_VARIANTS:
        yield pathlib.Path("icns") / (variant + ".icns"), icns_bytes, "icns"

    yield pathlib.Path("boot_logo.ppm"), render_ppm(), "ppm"
    yield pathlib.Path("Knix_logo_reference.svg"), svg_bytes, "svg"
    yield (pathlib.Path("Knix_logo_reference.png"),
           render_png("Knix_logo", (1600, 686)), "png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.apply):
        ap.error("choose --check or --apply")

    for needed in ("mark-dark.png", "mark-light.png", "icon.svg",
                   "icon-1024.png", "kstudio.icns"):
        if not (BRAND / needed).exists():
            print("MISSING official asset: docs/brand/%s" % needed, file=sys.stderr)
            return 2

    counts = {}
    drift = []
    missing = []
    for rel, expected, kind in plan():
        counts[kind] = counts.get(kind, 0) + 1
        p = LOGOS / rel
        if not p.exists():
            missing.append(str(rel))
            continue
        if p.read_bytes() != expected:
            drift.append(str(rel))
            if args.apply:
                p.write_bytes(expected)

    print("derived assets: %d  %s" % (
        sum(counts.values()),
        ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))
    print("missing: %d  drift: %d" % (len(missing), len(drift)))
    for m in missing[:5]:
        print("  MISSING", m)
    for d in drift[:5]:
        print("  DRIFT  ", d)

    if args.check:
        if missing or drift:
            print("CHECK FAILED: logos are not in sync with the official marks.",
                  file=sys.stderr)
            return 1
        print("CHECK OK: every logo is derived byte-for-byte from docs/brand/.")
        return 0
    print("APPLIED: wrote %d assets from the official Kense marks." % len(drift))
    return 0


if __name__ == "__main__":
    sys.exit(main())

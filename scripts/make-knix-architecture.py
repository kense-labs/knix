#!/usr/bin/env python3
"""Generate docs/knix-architecture.svg in the Kense OS paper style.

Single source of truth for the architecture diagram. The two official Kense
marks are read from docs/brand/ and embedded as base64 PNG; their bytes are
never edited here (see ../kense/kstudio/AGENTS.md, "Brand icons").

Style contract: paper #F8F6F0, ink #141413, blue #4E60FF, white #FDFCF9,
note grey #7A756C, pale blue #E9ECFF, Menlo, rx 6/8/14, open-V arrow
`M2 1L8 5L2 9`, orthogonal connectors, viewBox 680x470, min font 10.5px.

Usage:
    python3 scripts/make-knix-architecture.py
    python3 scripts/make-knix-architecture.py --render-png
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import subprocess
import sys
from PIL import ImageFont

ap = argparse.ArgumentParser()
ap.add_argument("--render-png", action="store_true",
                help="render the PNG preview with sips (macOS)")
args = ap.parse_args()

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "brand"
SVG_OUT = ROOT / "docs" / "knix-architecture.svg"
PNG_OUT = ROOT / "docs" / "knix-architecture.png"

PAPER = "#F8F6F0"
INK = "#141413"
BLUE = "#4E60FF"
WHITE = "#FDFCF9"
GREY = "#7A756C"
PALE = "#E9ECFF"

MARK_HASHES = {
    "mark-dark.png": "092e950523d7997fb686d559fb28ba0f32fe98490cb901686ccdf5e6ab6dcc3d",
    "mark-light.png": "28a962a6144b4f2c10b42238ae29d8f85a3d358c7fe10eadcc05f86aa436aacc",
}

dark_bytes = (BRAND / "mark-dark.png").read_bytes()
light_bytes = (BRAND / "mark-light.png").read_bytes()
assert hashlib.sha256(dark_bytes).hexdigest() == MARK_HASHES["mark-dark.png"]
assert hashlib.sha256(light_bytes).hexdigest() == MARK_HASHES["mark-light.png"]
dark_b64 = base64.b64encode(dark_bytes).decode()
light_b64 = base64.b64encode(light_bytes).decode()

_fonts: dict[int, ImageFont.FreeTypeFont] = {}
def text_width(s: str, size: float) -> float:
    key = int(round(size))
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", key)
    return _fonts[key].getlength(s)

L: list[str] = []
overflow: list[tuple] = []

def add(line: str) -> None:
    L.append(line)

def text(x, y, s, size=11, fill=INK, anchor="start", maxw=None) -> None:
    assert size >= 10.5, "font below style minimum: %s" % size
    if maxw is not None:
        w = text_width(s.replace("&amp;", "&"), size)
        if w > maxw:
            overflow.append((s, size, round(w, 1), maxw))
    add('<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s">%s</text>'
        % (x, y, size, fill, anchor, s))

def rect(x, y, w, h, fill, rx=8, stroke=None, sw=1.2, dash=None) -> None:
    parts = ['<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s"'
             % (x, y, w, h, rx, fill)]
    if stroke:
        parts.append(' stroke="%s" stroke-width="%s"' % (stroke, sw))
    if dash:
        parts.append(' stroke-dasharray="%s"' % dash)
    add("".join(parts) + '/>')

def block(x, y, w, h, line1, line2, kind="plain") -> None:
    if kind == "ink":
        rect(x, y, w, h, INK, rx=8)
        c1, c2 = WHITE, WHITE
    elif kind == "blue":
        rect(x, y, w, h, WHITE, rx=6, stroke=BLUE, sw=1.2)
        c1, c2 = INK, BLUE
    elif kind == "emphasis":
        rect(x, y, w, h, BLUE, rx=6)
        c1, c2 = WHITE, PALE
    else:
        rect(x, y, w, h, WHITE, rx=6, stroke=INK, sw=0.9)
        c1, c2 = INK, GREY
    cx = x + w / 2
    text(cx, y + 18, line1, 12, c1, "middle", w - 16)
    text(cx, y + 34, line2, 10.5, c2, "middle", w - 16)

def arrow(d, color=INK) -> None:
    add('<path d="%s" fill="none" stroke="%s" stroke-width="1.2" '
        'marker-end="url(#knix-architecture-aInk)"/>' % (d, color))

def guide(d, color=GREY) -> None:
    add('<path d="%s" fill="none" stroke="%s" stroke-width="0.9" '
        'stroke-dasharray="3 4"/>' % (d, color))

totals = json.loads((ROOT / "repos.json").read_text(encoding="utf-8"))["totals"]
footer = "43 dirs · %s files · %.2f GB · 411 MiB pack" % (
    format(totals["snapshot_files"], ","),
    totals["snapshot_bytes"] / 1073741824)

add('<?xml version="1.0" encoding="UTF-8"?>')
add('<svg viewBox="0 0 680 470" width="680" height="470" role="img" xmlns="http://www.w3.org/2000/svg">')
add('<title>Knix 独立发行版的产品边界与运行后端</title>')
add('<desc>README.md 是入口契约，platform/CONTRACT.md 拥有产品边界。边界内左侧是归档基线：'
    'repos.json 机器真源、collect-knix.py 唯一采集机制、repos/ 下 43 份浅克隆源码快照；'
    '右侧是产品层：platform/identity 与 image 定义品牌、内核基线与 knix.img，'
    'native-apple 与 vm-virt 两个启动后端只决定启动路径，不改变身份。'
    '边界外的 platform/host-adapters/ 为非规范、可选、默认关闭的资产，不进入镜像。'
    'I1–I14 由 verify-knix.py 执行，边界判据是移除宿主后仍可构建、启动、自检、更新与发布。'
    '全部汇入单一 knix.img 与 origin/main 发布面。</desc>')
add('<defs>')
add('<marker id="knix-architecture-aInk" viewBox="0 0 10 10" refX="8" refY="5" '
    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M2 1L8 5L2 9" fill="none" stroke="#141413" stroke-width="1.5" '
    'stroke-linecap="round" stroke-linejoin="round"/></marker>')
add('</defs>')
rect(0, 0, 680, 470, PAPER, rx=14)
add('<g font-family="Menlo, SFMono-Regular, Consolas, monospace">')

# ---- title bar -----------------------------------------------------------
rect(24, 16, 632, 32, INK, rx=8)
add('<image x="34" y="20" width="24" height="24" href="data:image/png;base64,%s"/>' % dark_b64)
text(340, 38, "KNIX · PRODUCT BOUNDARY", 14, WHITE, "middle", 572)
text(340, 62, "one identity · one image · two launch backends · one published branch",
     11, GREY, "middle", 632)
add('<image x="596" y="76" width="44" height="44" href="data:image/png;base64,%s"/>' % light_b64)

# ---- entry contracts -----------------------------------------------------
block(30, 76, 270, 44, "README.md · ENTRY CONTRACT",
      "truth hierarchy · I1–I14", "ink")
block(310, 76, 270, 44, "platform/CONTRACT.md",
      "product boundary · contract.json", "ink")
guide("M300 98 L310 98", BLUE)
arrow("M165 120 L165 130")
arrow("M445 120 L445 130")

# ---- inside the product boundary ----------------------------------------
rect(24, 132, 632, 198, PALE, rx=14, stroke=INK, sw=0.9)
text(34, 150, "PRODUCT BOUNDARY", 11, INK, "start", 260)
text(646, 150, "I1–I14 · verify-knix.py", 11, GREY, "end", 260)

text(34, 168, "ARCHIVE BASELINE", 10.5, GREY, "start", 260)
block(34, 174, 292, 42, "repos.json · MACHINE TRUTH",
      "43 entries · SHA pin · byte counts", "ink")
block(34, 226, 292, 42, "collect-knix.py · ONLY MECHANISM",
      "--list · --only · --jobs · depth 1")
block(34, 278, 292, 42, "repos/ · 43 snapshots",
      "1.94 GB · 147,032 files · no .git")
arrow("M180 216 L180 226")
arrow("M180 268 L180 278")

text(346, 168, "PRODUCT LAYER · RUN BACKENDS", 10.5, GREY, "start", 280)
text(646, 168, "choose one", 10.5, GREY, "end", 100)
block(346, 174, 300, 42, "platform/identity + image",
      "brand · kernel baseline · knix.img", "ink")
block(346, 236, 146, 42, "native-apple", "knix.config · m1n1")
block(500, 236, 146, 42, "vm-virt", "virt.config · virtio", "blue")
arrow("M496 216 L496 228 L418 228 L418 236")
arrow("M496 228 L574 228 L574 236")
guide("M418 278 L418 284 L496 284 L496 288")
guide("M574 278 L574 284")
rect(346, 288, 300, 32, "none", rx=6, stroke=INK, sw=0.9, dash="4 4")
text(496, 308, "one knix.img · one origin/main", 10.5, GREY, "middle", 280)

# ---- outside the product boundary ---------------------------------------
guide("M174 330 L174 338")
guide("M518 330 L518 338")
rect(24, 338, 276, 44, "none", rx=8, stroke=GREY, sw=0.9, dash="4 4")
text(34, 356, "host-adapters/ · NON-NORMATIVE", 11, INK, "start", 256)
text(34, 372, "optional · off · never in knix.img", 10.5, GREY, "start", 256)
rect(380, 338, 276, 44, BLUE, rx=6)
text(390, 356, "I14 · NON-NEGOTIABLE", 11, WHITE, "start", 256)
text(390, 372, "no host → still builds, boots, checks", 10.5, PALE, "start", 256)

# ---- single published surface -------------------------------------------
arrow("M340 330 L340 390")
block(130, 392, 420, 42, "knix.img · origin/main · kense-labs/knix",
      footer, "ink")
text(340, 452, "I14 · run backends are launch paths, not capability sources",
     10.5, GREY, "middle", 632)
add('</g>')
add('</svg>')

if overflow:
    for s, size, w, maxw in overflow:
        print("OVERFLOW: %r size=%s width=%s max=%s" % (s, size, w, maxw),
              file=sys.stderr)
    raise SystemExit(1)

SVG_OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print("wrote", SVG_OUT.relative_to(ROOT), SVG_OUT.stat().st_size, "bytes")
print("text nodes checked:", sum(1 for l in L if l.startswith("<text")),
      "overflow: 0")
print("embedded marks: 2 (sha256 verified against the official brand assets)")

if args.render_png:
    r = subprocess.run(["sips", "-s", "format", "png", str(SVG_OUT),
                        "--out", str(PNG_OUT)], capture_output=True, text=True)
    if r.returncode != 0:
        print("sips failed:", r.stderr.strip(), file=sys.stderr)
        raise SystemExit(1)
    print("wrote", PNG_OUT.relative_to(ROOT), PNG_OUT.stat().st_size, "bytes")

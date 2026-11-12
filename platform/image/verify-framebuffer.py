#!/usr/bin/env python3
"""Prove that the booted knix desktop actually painted pixels.

This runs on the build HOST, after `verify-graphics.sh` has streamed into the
guest and finished. The guest picks a 24-bit colour from its own /dev/urandom,
sway paints it as a solid-colour background, and the guest prints the colour.
This script only READS that colour from the guest log and READS the standard
virtio-gpu scanout through QEMU's QMP `screendump` in P6 (PPM) format. It never
draws, injects or configures guest content: the desktop stays guest-owned.

    verify-framebuffer.py --qmp <sock> --out <file.ppm> \
        --log <guest-graphics.log> --summary <framebuffer-verify.json>
    verify-framebuffer.py --self-test     # no QEMU, no seed, no VM (CI)

The build refuses to write a receipt unless every sampled pixel equals the
guest-reported colour and the top of the scanout shows the guest swaybar.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import socket
import sys
import tempfile
import time

WIDTH, HEIGHT = 1280, 800
HEADER_BYTES = 16  # b"P6\n1280 800\n255\n"
SAMPLE_XS = (160, 640, 1120)
SAMPLE_YS = (200, 400, 600)
BAR_ROWS = range(0, 25, 2)
BAR_STEP = 8
MIN_BAR_PIXELS = 100
PATTERN_RE = re.compile(r"^FRAMEBUFFER PATTERN: ([0-9a-f]{6})$", re.MULTILINE)
BAR_COLOUR = (0x32, 0x32, 0x32)


def extract_pattern(text: str) -> str:
    matches = PATTERN_RE.findall(text)
    if len(matches) != 1:
        raise ValueError("expected exactly one guest-chosen FRAMEBUFFER PATTERN, got %d"
                         % len(matches))
    return matches[0]


def parse_ppm(raw: bytes):
    if not raw.startswith(b"P6"):
        raise ValueError("QEMU screendump is not a P6 PPM")
    parts = raw.split(b"\n", 3)
    if len(parts) != 4:
        raise ValueError("truncated PPM header")
    dims = parts[1].split()
    if len(dims) != 2:
        raise ValueError("malformed PPM dimensions")
    try:
        w, h, maxv = int(dims[0]), int(dims[1]), int(parts[2].strip())
    except ValueError as exc:
        raise ValueError("malformed PPM header: %s" % exc) from exc
    return w, h, maxv, parts[3]


def check_pixels(pattern_hex: str, w: int, h: int, maxv: int, data: bytes):
    if (w, h) != (WIDTH, HEIGHT):
        raise ValueError("scanout is %dx%d, expected %dx%d" % (w, h, WIDTH, HEIGHT))
    if maxv != 255:
        raise ValueError("PPM maxval is %d, expected 255" % maxv)
    if len(data) != w * h * 3:
        raise ValueError("PPM carries %d pixel bytes, expected %d"
                         % (len(data), w * h * 3))
    want = tuple(bytes.fromhex(pattern_hex))
    # The guest lifts every channel into 0x40..0xff. A black or near-black
    # "pattern" would also match an uninitialised framebuffer, so refuse it.
    if any(channel < 0x40 for channel in want):
        raise ValueError("guest pattern #%s is too dark to prove a repaint"
                         % pattern_hex)

    def px(x, y):
        i = (y * w + x) * 3
        return tuple(data[i:i + 3])

    total = len(SAMPLE_XS) * len(SAMPLE_YS)
    matching = sum(px(x, y) == want for y in SAMPLE_YS for x in SAMPLE_XS)
    if matching != total:
        raise ValueError("guest pattern #%s appears in only %d/%d scanout samples"
                         % (pattern_hex, matching, total))
    bar = sum(px(x, y) != want
              for y in BAR_ROWS for x in range(0, w, BAR_STEP))
    if bar < MIN_BAR_PIXELS:
        raise ValueError("only %d non-pattern pixels in the top bar; the guest "
                         "swaybar did not paint" % bar)
    return {
        "width": w,
        "height": h,
        "pattern": pattern_hex,
        "pattern_sha256": hashlib.sha256(pattern_hex.encode("ascii")).hexdigest(),
        "samples": total,
        "matching_samples": matching,
        "bar_pixels": bar,
        "verified": True,
        "owner": "guest",
        "capture": "QMP screendump (P6) of the standard virtio-gpu scanout",
    }


def qmp_screendump(sock_path: pathlib.Path, out_path: pathlib.Path) -> None:
    if not sock_path.exists():
        raise ValueError("QMP socket is missing: %s" % sock_path)
    if out_path.exists():
        out_path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    sock.connect(str(sock_path))
    stream = sock.makefile("rwb", buffering=0)

    def recv():
        line = stream.readline()
        if not line:
            raise ValueError("QMP connection closed")
        return json.loads(line)

    def command(name, args=None):
        payload = {"execute": name}
        if args:
            payload["arguments"] = args
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        while True:
            reply = recv()
            if "error" in reply:
                raise ValueError("QMP %s failed: %s" % (name, reply["error"]))
            if "return" in reply:
                return reply["return"]
            # asynchronous QMP event: keep reading

    try:
        recv()  # greeting
        command("qmp_capabilities")
        command("screendump", {"filename": str(out_path), "format": "ppm"})
    finally:
        sock.close()
    expected = HEADER_BYTES + WIDTH * HEIGHT * 3
    for _ in range(50):
        if out_path.is_file() and out_path.stat().st_size >= expected:
            return
        time.sleep(0.1)
    raise ValueError("QEMU never wrote a complete P6 screendump to %s" % out_path)


def make_ppm(pattern: bytes, bar: bool = True, w: int = WIDTH,
             h: int = HEIGHT, maxv: int = 255, truncate: int = 0) -> bytes:
    row = pattern * w
    bar_row = bytes(BAR_COLOUR) * w
    data = bytearray(b"".join(
        (bar_row if bar and y < 25 else row) for y in range(h)))
    if truncate:
        data = data[:-truncate]
    return b"P6\n%d %d\n%d\n" % (w, h, maxv) + bytes(data)


def self_test() -> int:
    cases = []

    def expect(name, fn, ok):
        try:
            fn()
            got = True
        except (ValueError, OSError) as exc:
            got = False
            detail = str(exc)
        else:
            detail = "accepted"
        if got != ok:
            raise AssertionError("case %s: expected %s, got %s (%s)"
                                 % (name, "pass" if ok else "refusal",
                                    "pass" if got else "refusal", detail))
        cases.append("%s=%s" % (name, "OK" if ok else "REFUSED"))

    with tempfile.TemporaryDirectory(prefix="knix-framebuffer-") as tmp:
        base = pathlib.Path(tmp)
        good = make_ppm(bytes.fromhex("deacdb"))
        (base / "good.ppm").write_bytes(good)
        expect("control", lambda: check_pixels("deacdb", *parse_ppm(good)), True)
        expect("extract_pattern", lambda: extract_pattern(
            "noise\nFRAMEBUFFER PATTERN: deacdb\nrest"), True)
        expect("missing_pattern_line", lambda: extract_pattern("no pattern here"),
               False)
        expect("duplicate_pattern_line", lambda: extract_pattern(
            "FRAMEBUFFER PATTERN: aabbcc\nFRAMEBUFFER PATTERN: ddeeff"), False)
        expect("uppercase_pattern_line", lambda: extract_pattern(
            "FRAMEBUFFER PATTERN: DEACDB"), False)
        expect("wrong_colour", lambda: check_pixels(
            "aabbcc", *parse_ppm(good)), False)
        expect("black_scanout", lambda: check_pixels(
            "000000", *parse_ppm(make_ppm(b"\x00\x00\x00"))), False)
        expect("no_swaybar", lambda: check_pixels(
            "deacdb", *parse_ppm(make_ppm(bytes.fromhex("deacdb"), bar=False))),
            False)
        expect("wrong_resolution", lambda: check_pixels(
            "deacdb", *parse_ppm(make_ppm(bytes.fromhex("deacdb"), w=1024,
                                          h=768))), False)
        expect("wrong_maxval", lambda: check_pixels(
            "deacdb", *parse_ppm(make_ppm(bytes.fromhex("deacdb"), maxv=15))),
            False)
        expect("truncated_pixels", lambda: check_pixels(
            "deacdb", *parse_ppm(make_ppm(bytes.fromhex("deacdb"),
                                          truncate=9))), False)
        expect("not_a_ppm", lambda: check_pixels(
            "deacdb", *parse_ppm(b"GIF89a\n")), False)
    print("FRAMEBUFFER SELF-TEST OK: %d cases (%s)"
          % (len(cases), ", ".join(cases)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qmp", type=pathlib.Path, help="QEMU QMP unix socket")
    ap.add_argument("--out", type=pathlib.Path, help="P6 screendump output")
    ap.add_argument("--log", type=pathlib.Path,
                    help="guest-graphics.log carrying the guest-chosen pattern")
    ap.add_argument("--summary", type=pathlib.Path,
                    help="JSON verification summary for the receipt")
    ap.add_argument("--self-test", action="store_true",
                    help="exercise the pixel contract without QEMU")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    missing = [n for n in ("qmp", "out", "log", "summary") if not getattr(args, n)]
    if missing:
        ap.error("missing --%s" % ", --".join(missing))
    try:
        pattern = extract_pattern(args.log.read_text(encoding="utf-8"))
        qmp_screendump(args.qmp, args.out)
        result = check_pixels(pattern, *parse_ppm(args.out.read_bytes()))
        args.summary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    except (OSError, ValueError) as exc:
        print("FRAMEBUFFER VERIFY FAILED: %s" % exc, file=sys.stderr)
        return 1
    print("FRAMEBUFFER VERIFY OK: %dx%d, guest pattern #%s at %d/%d samples, "
          "%d swaybar pixels" % (result["width"], result["height"],
                                 result["pattern"], result["matching_samples"],
                                 result["samples"], result["bar_pixels"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

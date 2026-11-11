#!/usr/bin/env python3
"""Verify the knix collaboration contract.

The README owns the contract; this script makes it executable. Run it before
any commit that touches repos/, repos.json, README.md, docs/ or scripts/.

Exit status is 0 only when every invariant holds. Nothing is written.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPOS = ROOT / "repos"
README = ROOT / "README.md"
SVG = ROOT / "docs" / "knix-architecture.svg"
PNG = ROOT / "docs" / "knix-architecture.png"
BRAND = ROOT / "docs" / "brand"
KENSE_BRAND = ROOT.parent / "kense" / "kstudio" / "assets" / "brand"
SKIP_DIRS = {".git", "__pycache__", ".workbuddy"}

# Official Kense marks. These hashes are the published brand contract -- never
# edit the PNG bytes by hand; regenerate them with ../kense/kstudio/scripts/.
MARK_HASHES = {
    "mark-dark.png": "092e950523d7997fb686d559fb28ba0f32fe98490cb901686ccdf5e6ab6dcc3d",
    "mark-light.png": "28a962a6144b4f2c10b42238ae29d8f85a3d358c7fe10eadcc05f86aa436aacc",
}

# Built at runtime so this verifier never becomes a residual brand hit itself.
def brand_pattern() -> re.Pattern:
    A, L = "Asahi", "Linux"
    a, l = A.lower(), L.lower()
    return re.compile("|".join([
        (A + L).upper(), A + L, A + " " + L, a + " " + l,
        A + "-" + L, a + "-" + l, (A + "-" + L).upper(),
        A + "_" + L, a + "_" + l, a + l, A + L.upper(),
    ]))


def stats(path: pathlib.Path):
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            files += 1
            if not os.path.islink(p):
                total += st.st_size
    return files, total


errors: list[str] = []
notes: list[str] = []
def require(ok: bool, good: str, bad: str):
    if ok:
        notes.append("OK: " + good)
    else:
        errors.append("FAIL: " + bad)


def main() -> int:
    m = json.loads((ROOT / "repos.json").read_text(encoding="utf-8"))
    entries = m["repositories"]

    # I1 -- 43 directories, names exactly match the manifest
    names = sorted(e["name"] for e in entries)
    dirs = sorted(d for d in os.listdir(REPOS) if (REPOS / d).is_dir())
    require(m["count"] == len(entries) == 43 and names == dirs,
            "43 dirs match repos.json",
            "manifest/directory mismatch: count=%d entries=%d dirs=%d"
            % (m["count"], len(entries), len(dirs)))

    # I2 -- SHA pin, local_path, no nested git metadata
    bad_sha, bad_path, nested_git = [], [], []
    for e in entries:
        lp = ROOT / e["local_path"]
        if not re.fullmatch(r"[0-9a-f]{40}", e["snapshot_sha"] or ""):
            bad_sha.append(e["name"])
        if not lp.is_dir():
            bad_path.append(e["local_path"])
        if (lp / ".git").exists():
            nested_git.append(e["name"])
    require(not bad_sha, "43 pinned 40-char SHAs", "bad SHA: %s" % bad_sha)
    require(not bad_path, "43 local_path entries resolve", "missing: %s" % bad_path)
    require(not nested_git, "no nested .git in snapshots", "nested .git: %s" % nested_git)

    # I3 -- single-file gate, and disk statistics match the machine truth
    big = []
    for dirpath, _dirnames, filenames in os.walk(REPOS):
        for name in filenames:
            p = pathlib.Path(dirpath) / name
            if p.is_symlink():
                continue
            try:
                if p.stat().st_size > 95 * 1024 * 1024:
                    big.append(str(p.relative_to(ROOT)))
            except OSError:
                pass
    require(not big, "no file exceeds 95 MB", "oversized: %s" % big[:5])

    drift = []
    total_files = total_bytes = 0
    for e in entries:
        f, b = stats(ROOT / e["local_path"])
        total_files += f
        total_bytes += b
        if (f, b) != (e["snapshot_files"], e["snapshot_bytes"]):
            drift.append((e["name"], (e["snapshot_files"], e["snapshot_bytes"]), (f, b)))
    require(not drift, "per-repo file/byte counts match disk",
            "stale statistics: %s" % drift[:3])
    require(m["totals"]["snapshot_files"] == total_files
            and m["totals"]["snapshot_bytes"] == total_bytes,
            "totals match disk (%s files / %s bytes)" % (total_files, total_bytes),
            "stale totals: manifest=%s/%s disk=%s/%s"
            % (m["totals"]["snapshot_files"], m["totals"]["snapshot_bytes"],
               total_files, total_bytes))

    # I4 -- README links resolve (the 43-row index is machine-checkable)
    readme = README.read_text(encoding="utf-8")
    links = re.findall(r"\[`[^`]+`\]\(([^)]+)\)", readme)
    broken = [l for l in links if not (ROOT / l).exists()]
    require(len(links) == 43 and not broken,
            "README 43 index links resolve",
            "links=%d broken=%s" % (len(links), broken[:5]))

    # I5 -- no upstream brand literals in content, path names or symlink targets
    pat = brand_pattern()
    r = subprocess.run(["git", "grep", "--no-index", "-l", "-E", pat.pattern, "--", "."],
                       cwd=ROOT, capture_output=True, text=True)
    content_hits = [l for l in r.stdout.splitlines() if not l.startswith(".workbuddy/")]
    path_hits, link_hits = [], []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = pathlib.Path(dirpath).relative_to(ROOT)
        for name in dirnames + filenames:
            p = pathlib.Path(dirpath) / name
            if pat.search(name):
                path_hits.append(str(p.relative_to(ROOT)))
            if p.is_symlink() and pat.search(os.readlink(p)):
                link_hits.append(str(p.relative_to(ROOT)))
    require(not (content_hits or path_hits or link_hits),
            "0 upstream brand literals (content / paths / symlink targets)",
            "residual content=%s paths=%s links=%s"
            % (content_hits[:5], path_hits[:5], link_hits[:5]))

    # I6 -- official Kense marks, both on disk and embedded in the SVG
    ok_marks = True
    for n, h in MARK_HASHES.items():
        p = BRAND / n
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != h:
            ok_marks = False
    if KENSE_BRAND.is_dir():
        for n, h in MARK_HASHES.items():
            p = KENSE_BRAND / n
            if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != h:
                ok_marks = False
        notes.append("OK: ../kense official mark sources checked")
    else:
        notes.append("SKIP: ../kense brand sources not present locally")
    require(ok_marks, "2 official mark PNGs match the brand contract",
            "mark PNG bytes differ from the official contract")

    svg = SVG.read_text(encoding="utf-8")
    embedded = [base64.b64decode(b) for b in
                re.findall(r'href="data:image/png;base64,([^"]+)"', svg)]
    embedded_hashes = {hashlib.sha256(d).hexdigest() for d in embedded}
    require(len(embedded) == 2 and embedded_hashes == set(MARK_HASHES.values())
            and SVG.exists() and PNG.exists() and PNG.stat().st_size > 0,
            "SVG embeds both marks; PNG preview present",
            "architecture diagram assets are incomplete")
    require("<title>" in svg and "<desc>" in svg
            and 'viewBox="0 0 680 470"' in svg
            and len(re.findall(r"<marker ", svg)) >= 1,
            "architecture SVG keeps the Kense style contract",
            "architecture SVG lost its title/desc/viewBox/marker")

    # I7 -- the only collection mechanism still parses
    try:
        ast.parse((ROOT / "scripts" / "collect-knix.py").read_text(encoding="utf-8"))
        ok_script = True
    except (OSError, SyntaxError):
        ok_script = False
    require(ok_script, "scripts/collect-knix.py parses",
            "collect-knix.py is missing or has a syntax error")

    for line in notes:
        print(line)
    for line in errors:
        print(line, file=sys.stderr)
    if errors:
        print("\n%d invariant(s) violated. Fix them before committing." % len(errors),
              file=sys.stderr)
        return 1
    print("\nAll invariants hold. Safe to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

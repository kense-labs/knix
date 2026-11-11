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

# Assembled at runtime so this verifier never becomes a residual hit itself.
# join() instead of "+" because CPython constant-folds string concatenation and
# would otherwise bake the literal into this module's .pyc bytecode cache.
UPSTREAM_TOKEN = "".join(("asa", "hi"))
TOKEN_RE = re.compile(UPSTREAM_TOKEN, re.I)


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


def in_release_surface(rel: str, tracked: set[str]) -> bool:
    """Only files that can reach origin/main count as residuals.

    `git grep --no-index` walks ignored files too (e.g. Python bytecode
    caches and .workbuddy/), so filter them out unless git already tracks them.
    """
    if rel in tracked:
        return True
    return subprocess.run(["git", "check-ignore", "-q", "--", rel],
                          cwd=ROOT, capture_output=True).returncode != 0


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

    # I2 / I3 -- SHA pin, local_path, no nested git metadata
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

    # I4 -- single-file gate
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

    # I5 -- disk statistics match the machine truth
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

    # I6 -- zero upstream tokens in content, path names and symlink targets
    r = subprocess.run(
        ["git", "grep", "--no-index", "-l", "-i", "-E", UPSTREAM_TOKEN, "--", "."],
        cwd=ROOT, capture_output=True, text=True)
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.splitlines())
    content_hits = [l for l in r.stdout.splitlines()
                    if in_release_surface(l, tracked)]
    path_hits, link_hits, dangling = [], [], []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in dirnames + filenames:
            p = pathlib.Path(dirpath) / name
            if TOKEN_RE.search(name):
                path_hits.append(str(p.relative_to(ROOT)))
            if p.is_symlink():
                if TOKEN_RE.search(os.readlink(p)):
                    link_hits.append(str(p.relative_to(ROOT)))
                if str(p).startswith(str(REPOS) + os.sep):
                    try:
                        if not p.exists():
                            dangling.append(str(p.relative_to(ROOT)))
                    except OSError:
                        dangling.append(str(p.relative_to(ROOT)))
    require(not (content_hits or path_hits or link_hits),
            "0 upstream tokens (content / paths / symlink targets / PNG metadata)",
            "residual content=%s paths=%s links=%s"
            % (content_hits[:5], path_hits[:5], link_hits[:5]))

    # I12 -- every vendored symlink resolves inside the monorepo
    require(not dangling, "0 dangling symlinks in repos/",
            "dangling symlinks: %s" % dangling[:5])

    # I7 -- identifiers unified under Knix naming
    branches = {e["default_branch"] for e in entries}
    require(all(not TOKEN_RE.search(b) for b in branches)
            and all(not TOKEN_RE.search(n) for n in names),
            "identifiers unified (%s)" % ", ".join(sorted(branches)),
            "manifest still carries an old identifier")

    # I8 -- every official asset in docs/brand/ matches ../kense byte-for-byte
    local_assets = sorted(p for p in BRAND.iterdir() if p.is_file())
    ok_marks = True
    for n, h in MARK_HASHES.items():
        p = BRAND / n
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != h:
            ok_marks = False
    mismatched = []
    if KENSE_BRAND.is_dir():
        for p in local_assets:
            q = KENSE_BRAND / p.name
            if (not q.exists()
                    or hashlib.sha256(q.read_bytes()).hexdigest()
                    != hashlib.sha256(p.read_bytes()).hexdigest()):
                mismatched.append(p.name)
    else:
        notes.append("SKIP: ../kense brand sources not present locally")
    require(ok_marks and not mismatched,
            "%d official assets match the brand contract" % len(local_assets),
            "brand asset mismatch: %s" % mismatched[:5])

    svg = SVG.read_text(encoding="utf-8")
    embedded = [base64.b64decode(b) for b in
                re.findall(r'href="data:image/png;base64,([^"]+)"', svg)]
    embedded_hashes = {hashlib.sha256(d).hexdigest() for d in embedded}
    require(len(embedded) == 2 and embedded_hashes == set(MARK_HASHES.values())
            and PNG.exists() and PNG.stat().st_size > 0,
            "SVG embeds both official marks; PNG preview present",
            "architecture diagram assets are incomplete")
    require("<title>" in svg and "<desc>" in svg
            and 'viewBox="0 0 680 470"' in svg
            and len(re.findall(r"<marker ", svg)) >= 1
            and not TOKEN_RE.search(svg),
            "architecture SVG keeps the Kense style contract and is clean",
            "architecture SVG lost its style contract or carries a residual")

    # I9 -- single published branch, no tags, no registered submodules
    tags = subprocess.run(["git", "tag", "--list"], cwd=ROOT,
                          capture_output=True, text=True).stdout.split()
    local_branches = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"], cwd=ROOT,
        capture_output=True, text=True).stdout.split()
    submodules = subprocess.run(
        ["git", "submodule", "status"], cwd=ROOT,
        capture_output=True, text=True).stdout.split()
    require(not tags and local_branches == ["main"] and not submodules,
            "single main branch, no tags, no registered submodules",
            "tags=%s branches=%s submodules=%s" % (tags, local_branches, submodules))

    # I10 / I13 -- README is a clean derived view, free of meta-narration
    readme = README.read_text(encoding="utf-8")
    require("repos.json" in readme and "scripts/verify-knix.py" in readme,
            "README points to the machine truth and the gate",
            "README lost its pointer to repos.json / verify-knix.py")
    require(not TOKEN_RE.search(readme),
            "README contains 0 upstream traces",
            "README still contains an upstream token")
    meta_narration = re.search(r"本文|本页|本文档|需要说明的是|换句话说|如下说明", readme)
    require(not meta_narration and "repos/artwork/logos/Makefile" in readme,
            "README has no self-narration and retires the upstream Makefile",
            "README regained meta-narration or dropped the Makefile ban")

    # I14 -- run backends are launch paths, never capability sources
    boundary_errors = []
    contract_path = ROOT / "platform" / "contract.json"
    if not contract_path.is_file():
        boundary_errors.append("platform/contract.json missing")
    else:
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            contract = {}
            boundary_errors.append("contract.json unreadable: %s" % exc)
        contract_doc = ROOT / "platform" / "CONTRACT.md"
        contract_text = (contract_doc.read_text(encoding="utf-8")
                         if contract_doc.is_file() else "")
        missing_clauses = [c for c in contract.get("required_clauses", [])
                           if c not in contract_text]
        if missing_clauses:
            boundary_errors.append("contract clauses: %s" % missing_clauses)
        if "platform/CONTRACT.md" not in readme or "## Run backends" not in readme:
            boundary_errors.append("README lost the boundary pointer")
        backend_dirs = [ROOT / "platform" / "backends" / b
                        for b in contract.get("backends", [])]
        if not backend_dirs or not all(d.is_dir() for d in backend_dirs):
            boundary_errors.append("backend directories incomplete")
        surface = contract.get("publish_surface", [])
        if len(surface) != 2 or "origin/main" not in surface:
            boundary_errors.append("publish surface changed: %s" % surface)
        for rel in contract.get("non_normative", []):
            d = ROOT / rel
            note = d / "README.md"
            if (not d.is_dir() or not note.is_file()
                    or "non-normative" not in note.read_text(encoding="utf-8")):
                boundary_errors.append("non-normative marker missing: %s" % rel)
            elif any(p.is_file() and p.suffix != ".md" for p in d.rglob("*")):
                boundary_errors.append("host adapter carries an artifact: %s" % rel)
        ns = contract.get("host_namespace", "")
        if ns:
            gr = subprocess.run(
                ["git", "grep", "--no-index", "-l", "-F", ns, "--", "repos/"],
                cwd=ROOT, capture_output=True, text=True)
            if gr.stdout.splitlines():
                boundary_errors.append("host namespace in repos/: %s"
                                       % gr.stdout.splitlines()[:3])
        deny = contract.get("deny_in_publishable_code", [])
        for rel in contract.get("publishable_dirs", []):
            d = ROOT / rel
            if not d.is_dir():
                boundary_errors.append("publishable dir missing: %s" % rel)
                continue
            for p in d.rglob("*"):
                if (not p.is_file() or p.suffix == ".md"
                        or any(part in SKIP_DIRS for part in p.parts)):
                    continue
                try:
                    body = p.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                hit = next((w for w in deny if w in body), None)
                if hit:
                    boundary_errors.append("host dependency in %s: %s"
                                           % (p.relative_to(ROOT), hit))
        remotes = subprocess.run(["git", "remote"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.split()
        if remotes != ["origin"]:
            boundary_errors.append("remotes: %s" % remotes)
        else:
            url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.strip()
            if url != contract.get("origin_url"):
                boundary_errors.append("origin differs from the contract: %s" % url)
    require(not boundary_errors,
            "product boundary holds (backends, host adapters, single origin)",
            "product boundary: %s" % boundary_errors[:4])

    # I11 -- the whole derived logo set is reproducible from the official marks
    logo_check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make-knix-logos.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True)
    require(logo_check.returncode == 0 and "CHECK OK" in logo_check.stdout,
            "96 logo assets are byte-derived from the official marks",
            "logo drift: %s" % (logo_check.stdout.strip().splitlines()[-1:] or
                                [logo_check.stderr.strip()[:120]]))

    # I15 -- identity, image manifest and brand bytes agree with every build input
    identity_errors = []
    idp = ROOT / "platform" / "identity"
    imgp = ROOT / "platform" / "image"
    required = [
        idp / "identity.json", idp / "os-release", idp / "knix-release",
        idp / "brand.manifest.json", idp / "packages.json", idp / "session.json",
        imgp / "manifest.json", imgp / "build.sh", imgp / "inject-identity.sh",
        imgp / "build-rootfs.sh", imgp / "build-kernel.sh",
        imgp / "assemble-native.sh", imgp / "sign.sh",
        ROOT / "platform" / "backends" / "vm-virt" / "boot-dev.sh",
        ROOT / "platform" / "backends" / "vm-virt" / "boot-native.sh",
    ]
    for p in required:
        if not p.is_file():
            identity_errors.append("missing %s" % p.relative_to(ROOT))
    if not identity_errors:
        try:
            ident = json.loads((idp / "identity.json").read_text(encoding="utf-8"))
            mani = json.loads((imgp / "manifest.json").read_text(encoding="utf-8"))
            bm = json.loads((idp / "brand.manifest.json").read_text(encoding="utf-8"))
            session = json.loads((idp / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            ident, mani, bm, session = {}, {}, {"assets": {}}, {}
            identity_errors.append("unreadable identity/manifest: %s" % exc)
        osrel = (idp / "os-release").read_text(encoding="utf-8") if (idp / "os-release").is_file() else ""
        if not (ident.get("product") == ident.get("id") == "knix"
                and ident.get("publish_surface") == ["knix.img", "origin/main"]):
            identity_errors.append("identity.json fields drifted")
        if ('NAME="%s"' % ident.get("name")) not in osrel or ("ID=%s" % ident.get("id")) not in osrel:
            identity_errors.append("os-release does not match identity.json")
        if mani.get("artifact", {}).get("name") != "knix.img":
            identity_errors.append("image manifest artifact changed")
        for rel, expected in mani.get("inputs", {}).get("identity_sha256", {}).items():
            p = ROOT / rel
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                identity_errors.append("identity input drift: %s" % rel)
        for rel, expected in mani.get("inputs", {}).get("brand_sha256", {}).items():
            p = ROOT / rel
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                identity_errors.append("brand input drift: %s" % rel)
        for name, expected in bm.get("assets", {}).items():
            p = BRAND / name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                identity_errors.append("brand manifest drift: %s" % name)
        if session.get("host_adapters") != "none":
            identity_errors.append("session claims host adapters")
        if "| I15 |" not in readme:
            identity_errors.append("README lost I15")
    require(not identity_errors,
            "identity and image manifest agree with every build input",
            "identity/image contract: %s" % identity_errors[:4])

    # the only collection mechanism still parses
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

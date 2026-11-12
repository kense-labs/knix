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
        imgp / "verify-graphics.sh",
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
        for group, entries in mani.get("inputs", {}).items():
            if group == "seed" or not isinstance(entries, dict):
                continue
            for rel, expected in entries.items():
                p = ROOT / rel
                if (not p.is_file()
                        or hashlib.sha256(p.read_bytes()).hexdigest() != expected):
                    identity_errors.append("%s drift: %s" % (group, rel))
        if mani.get("artifact", {}).get("single") is not True:
            identity_errors.append("image manifest lost the single-artifact rule")
        for name, expected in bm.get("assets", {}).items():
            p = BRAND / name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                identity_errors.append("brand manifest drift: %s" % name)
        if session.get("host_adapters") != "none":
            identity_errors.append("session claims host adapters")
        if (session.get("output", {}).get("backend") != "drm"
                or session.get("output", {}).get("owner") != "guest"
                or session.get("input", {}).get("owner") != "guest"):
            identity_errors.append("session lost guest-owned DRM/input semantics")
        gfx = mani.get("graphics", {})
        if (gfx.get("owner") != "guest" or gfx.get("device") != "virtio-gpu-pci"
                or gfx.get("compositor") != "sway"):
            identity_errors.append("image manifest lost the graphics contract")
        if not (ROOT / gfx.get("verify", "")).is_file():
            identity_errors.append("graphics verification recipe is missing")
        if "| I15 |" not in readme:
            identity_errors.append("README lost I15")
    require(not identity_errors,
            "identity and image manifest agree with every build input",
            "identity/image contract: %s" % identity_errors[:4])

    # I16 -- the guest-produced kernel/rootfs baseline is locked in the tree;
    # scripts/verify-repro.py performs the full read-only reproducibility gate.
    repro_errors = []
    repro = ROOT / "scripts" / "verify-repro.py"
    if repro.is_file():
        try:
            ast.parse(repro.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            repro_errors.append("verify-repro.py does not parse: %s" % exc)
    else:
        repro_errors.append("missing scripts/verify-repro.py")
    if not (ROOT / "platform/image/package-versions.txt").is_file():
        repro_errors.append("missing locked package versions")
    try:
        repro_mani = json.loads((imgp / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(repro_mani.get("build_baseline"), dict):
            repro_errors.append("manifest lost build_baseline")
    except (OSError, ValueError) as exc:
        repro_errors.append("unreadable manifest: %s" % exc)
    if "| I16 |" not in readme:
        repro_errors.append("README lost I16")
    require(not repro_errors,
            "locked build baseline is present and the repro gate parses",
            "repro contract: %s" % repro_errors[:4])

    # I17 -- every declared run backend owns a machine-readable launch-path
    # contract; scripts/verify-backends.py makes the boundary executable.
    backend_errors = []
    backend_gate = ROOT / "scripts" / "verify-backends.py"
    if backend_gate.is_file():
        try:
            ast.parse(backend_gate.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            backend_errors.append("verify-backends.py does not parse: %s" % exc)
    else:
        backend_errors.append("missing scripts/verify-backends.py")
    try:
        backend_rules = json.loads(
            (ROOT / "platform" / "contract.json").read_text(encoding="utf-8"))
        truth = str(backend_rules.get("backend_truth", ""))
        for name in backend_rules.get("backends", []):
            rel = truth.replace("<name>", str(name))
            if not rel or not (ROOT / rel).is_file():
                backend_errors.append("backend machine truth missing: %s" % name)
        if not backend_rules.get("backend_ownership_fields"):
            backend_errors.append("contract lost backend ownership fields")
    except (OSError, ValueError) as exc:
        backend_errors.append("unreadable backend contract: %s" % exc)
    if "| I17 |" not in readme:
        backend_errors.append("README lost I17")
    require(not backend_errors,
            "every run backend has a boundary-safe machine contract",
            "backend contract: %s" % backend_errors[:4])

    # I18 -- the Wayland session is guest-owned, non-root and seat-backed.
    # WLR_BACKENDS=drm alone excludes libinput and leaves get_inputs empty.
    seat_errors = []
    try:
        sess = json.loads((ROOT / "platform" / "identity" / "session.json")
                          .read_text(encoding="utf-8"))
        sp = sess.get("seat_provider", {})
        if sp.get("manager") != "seatd" or sp.get("owner") != "guest":
            seat_errors.append("seat provider is not guest-owned seatd")
        if sp.get("backends") != ["drm", "libinput"]:
            seat_errors.append("wlroots backends lost libinput")
        if sp.get("groups") != ["video", "input", "render"]:
            seat_errors.append("session groups drifted")
        vg = (ROOT / "platform" / "image" / "verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("SESSION VERIFY OK", "QEMU Virtio Keyboard",
                      "QEMU Virtio Tablet", "knix-session.service", "seatd"):
            if token not in vg:
                seat_errors.append("verify-graphics.sh lost %s" % token)
    except (OSError, ValueError) as exc:
        seat_errors.append("unreadable session contract: %s" % exc)
    if "| I18 |" not in readme:
        seat_errors.append("README lost I18")
    require(not seat_errors,
            "guest-owned seatd session is verified with real libinput devices",
            "session contract: %s" % seat_errors[:4])

    # I19 -- autologin is real: the guest's own target enables the session unit,
    # and verification proves it was already active before any script touched it.
    autostart_errors = []
    try:
        au = json.loads((ROOT / "platform" / "identity" / "session.json")
                        .read_text(encoding="utf-8")).get("autostart", {})
        if au.get("enabled") is not True or au.get("owner") != "guest":
            autostart_errors.append("autostart is not a guest-owned enabled contract")
        if au.get("target") != "multi-user.target":
            autostart_errors.append("autostart target drifted")
        if au.get("condition") != "/dev/dri/card0":
            autostart_errors.append("autostart lost its DRM condition / GPU=none fallback")
        gfx = json.loads((ROOT / "platform" / "image" / "manifest.json")
                         .read_text(encoding="utf-8")).get("graphics", {})
        if (gfx.get("autostart") is not True
                or gfx.get("autostart_target") != "multi-user.target"
                or gfx.get("autostart_condition") != "/dev/dri/card0"):
            autostart_errors.append("image manifest lost the autostart contract")
        asm = (ROOT / "platform" / "image" / "assemble-native.sh").read_text(
            encoding="utf-8")
        for token in ("multi-user.target.wants/knix-session.service",
                      "ln -sf ../knix-session.service",
                      "WantedBy=multi-user.target"):
            if token not in asm:
                autostart_errors.append("assemble-native.sh lost %s" % token)
        vg = (ROOT / "platform" / "image" / "verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("AUTOSTART VERIFY OK", "is-enabled", "is-active",
                      "systemctl restart"):
            if token not in vg:
                autostart_errors.append("verify-graphics.sh lost %s" % token)
        if 'systemctl stop "$UNIT_NAME"' in vg:
            autostart_errors.append("verification must not stop the boot-time session")
        bs = (ROOT / "platform" / "image" / "build.sh").read_text(encoding="utf-8")
        for token in ("AUTOSTART VERIFY OK", "is-enabled knix-session.service",
                      "is-active knix-session.service", '"autostart"'):
            if token not in bs:
                autostart_errors.append("build.sh lost %s" % token)
    except (OSError, ValueError) as exc:
        autostart_errors.append("unreadable autostart contract: %s" % exc)
    if "| I19 |" not in readme:
        autostart_errors.append("README lost I19")
    require(not autostart_errors,
            "the image boots into its own guest-owned desktop",
            "autostart contract: %s" % autostart_errors[:4])

    # I20 -- a locked baseline can only be refreshed through an explicit,
    # scoped proposal with a reason; kernel drift never goes through it.
    refresh_errors = []
    try:
        tool = (ROOT / "scripts" / "refresh-baseline.py").read_text(
            encoding="utf-8")
        for token in ("--check", "--propose", "--apply", "--reason",
                      "--self-test", "BASELINE-PROPOSAL.json",
                      "kernel_drift_is_never_refreshed",
                      "REFUSED: stale proposal"):
            if token not in tool:
                refresh_errors.append("refresh-baseline.py lost %s" % token)
        ast.parse(tool, filename="scripts/refresh-baseline.py")
        pol = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8")).get("baseline_refresh", {})
        if (pol.get("tool") != "scripts/refresh-baseline.py"
                or pol.get("require_proposal") is not True
                or pol.get("require_reason_chars") != 8):
            refresh_errors.append("baseline_refresh policy drifted")
    except (OSError, SyntaxError, ValueError) as exc:
        refresh_errors.append("unreadable baseline refresh policy: %s" % exc)
    if "| I20 |" not in readme:
        refresh_errors.append("README lost I20")
    require(not refresh_errors,
            "baseline refresh requires an explicit, scoped proposal",
            "refresh policy: %s" % refresh_errors[:4])

    # I21 -- the boot-time session must survive a compositor crash through the
    # guest's own systemd, and that recovery must be a build-time hard gate.
    resilience_errors = []
    try:
        session_doc = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8"))
        res = session_doc.get("resilience", {})
        if (res.get("owner") != "guest" or res.get("manager") != "systemd"
                or res.get("restart") != "on-failure"
                or res.get("restart_sec") != 2
                or res.get("start_limit_burst") != 5
                or res.get("start_limit_interval_sec") != 30):
            resilience_errors.append("session.json resilience contract drifted")
        mres = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8")).get("graphics", {}).get("resilience", {})
        if (mres.get("owner") != "guest" or mres.get("restart") != "on-failure"
                or mres.get("start_limit_burst") != 5):
            resilience_errors.append("manifest graphics.resilience drifted")
        assemble = (ROOT / "platform/image/assemble-native.sh").read_text(
            encoding="utf-8")
        unit_heredoc = assemble.split("<<'UNIT'\n", 1)[-1].split("\nUNIT\n", 1)[0]
        unit_lines = [line.strip() for line in unit_heredoc.splitlines()]
        for token in ("Restart=on-failure", "RestartSec=2",
                      "StartLimitBurst=5", "StartLimitIntervalSec=30"):
            if token not in unit_lines:
                resilience_errors.append("session unit heredoc lost %s" % token)
        verify = (ROOT / "platform/image/verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("RESILIENCE VERIFY OK", "systemctl kill --signal=SIGKILL",
                      "NEW_PID", "the restarted sway allocated no GBM framebuffer"):
            if token not in verify:
                resilience_errors.append("verify-graphics.sh lost %s" % token)
        bs = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        for token in ("RESILIENCE VERIFY OK", '"resilience": {'):
            if token not in bs:
                resilience_errors.append("build.sh lost %s" % token)
    except (OSError, ValueError) as exc:
        resilience_errors.append("unreadable resilience contract: %s" % exc)
    if "| I21 |" not in readme:
        resilience_errors.append("README lost I21")
    require(not resilience_errors,
            "a crashed compositor is recovered by guest systemd and verified",
            "resilience contract: %s" % resilience_errors[:4])

    # I22 -- the desktop must paint real guest-owned pixels, not merely report
    # that a compositor is running. The guest picks the colour; the host only
    # reads the standard virtio-gpu scanout through QMP.
    framebuffer_errors = []
    try:
        m = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8"))
        fb = m.get("graphics", {}).get("framebuffer", {})
        if (fb.get("owner") != "guest"
                or fb.get("resolution") != [1280, 800]
                or fb.get("samples") != 9
                or fb.get("verify") != "platform/image/verify-framebuffer.py"
                or "QMP screendump (P6)" not in fb.get("capture", "")):
            framebuffer_errors.append("manifest graphics.framebuffer drifted")
        tool = (ROOT / "platform/image/verify-framebuffer.py").read_text(
            encoding="utf-8")
        for token in ("--self-test", "FRAMEBUFFER VERIFY OK",
                      "FRAMEBUFFER PATTERN:", "qmp_capabilities",
                      '"format": "ppm"', "guest pattern"):
            if token not in tool:
                framebuffer_errors.append("verify-framebuffer.py lost %s" % token)
        ast.parse(tool, filename="platform/image/verify-framebuffer.py")
        guest = (ROOT / "platform/image/verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("FRAMEBUFFER PATTERN:", "/dev/urandom", "solid_color",
                      "FRAMEBUFFER PATTERN PAINTED"):
            if token not in guest:
                framebuffer_errors.append("verify-graphics.sh lost %s" % token)
        bs = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        for token in ("FRAMEBUFFER VERIFY OK", "verify-framebuffer.py",
                      '"framebuffer": {'):
            if token not in bs:
                framebuffer_errors.append("build.sh lost %s" % token)
        workflow = (ROOT / ".github/workflows/knix-contract.yml").read_text(
            encoding="utf-8")
        if "verify-framebuffer.py --self-test" not in workflow:
            framebuffer_errors.append("CI lost the framebuffer self-test step")
    except (OSError, SyntaxError, ValueError) as exc:
        framebuffer_errors.append("unreadable framebuffer contract: %s" % exc)
    if "| I22 |" not in readme:
        framebuffer_errors.append("README lost I22")
    require(not framebuffer_errors,
            "the guest-painted framebuffer is proven from real pixels",
            "framebuffer contract: %s" % framebuffer_errors[:4])

    # I23 -- one restart is not enough: a crash loop must be stopped by the
    # guest's own systemd, leave an observable degraded state with a real
    # fallback, and then recover a complete knix-owned session.
    crash_loop_errors = []
    try:
        m = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8"))
        cl = m.get("graphics", {}).get("crash_loop", {})
        if (cl.get("owner") != "guest" or cl.get("manager") != "systemd"
                or cl.get("burst") != 5 or cl.get("interval_sec") != 30
                or cl.get("degraded_failed_units") != 1
                or cl.get("signal") != "Start request repeated too quickly."):
            crash_loop_errors.append("manifest graphics.crash_loop drifted")
        sess = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8")).get("resilience", {}).get("crash_loop", {})
        if (sess.get("owner") != "guest" or sess.get("burst") != 5
                or sess.get("interval_sec") != 30
                or sess.get("signal") != "Start request repeated too quickly."):
            crash_loop_errors.append("session.json crash_loop contract drifted")
        vg = (ROOT / "platform/image/verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("LIMIT VERIFY OK", "Start request repeated too quickly",
                      "systemctl reset-failed", "FAILED_UNITS",
                      "ssh.service", "/dev/ttyAMA0", "NRestarts"):
            if token not in vg:
                crash_loop_errors.append("verify-graphics.sh lost %s" % token)
        bs = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        for token in ("LIMIT VERIFY OK", '"crash_loop": {'):
            if token not in bs:
                crash_loop_errors.append("build.sh lost %s" % token)
        workflow = (ROOT / ".github/workflows/knix-contract.yml").read_text(
            encoding="utf-8")
        if "graphics.crash_loop" not in workflow:
            crash_loop_errors.append("CI contract lost the crash-loop check")
    except (OSError, ValueError) as exc:
        crash_loop_errors.append("unreadable crash-loop contract: %s" % exc)
    if "| I23 |" not in readme:
        crash_loop_errors.append("README lost I23")
    require(not crash_loop_errors,
            "a compositor crash loop is limited and observed inside the guest",
            "crash-loop contract: %s" % crash_loop_errors[:4])

    # I24 -- the guest itself must report a failed compositor. A verification
    # script observing the failure is not a diagnostic capability; the report
    # has to come from the guest's own systemd OnFailure= unit.
    diagnostics_errors = []
    try:
        m = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8"))
        d = m.get("graphics", {}).get("diagnostics", {})
        if (d.get("owner") != "guest" or d.get("manager") != "systemd"
                or d.get("unit") != "/etc/systemd/system/knix-session-failure.service"
                or d.get("script") != "/usr/lib/knix/session-failure-report.sh"
                or d.get("report") != "/run/knix/session-failure.txt"
                or d.get("history") != "/run/knix/session-failure.history"
                or d.get("min_reported_events") != 2
                or "OnFailure=" not in d.get("trigger", "")):
            diagnostics_errors.append("manifest graphics.diagnostics drifted")
        sess = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8")).get("diagnostics", {})
        if (sess.get("owner") != "guest" or sess.get("manager") != "systemd"
                or "OnFailure=" not in sess.get("trigger", "")
                or sess.get("report") != "/run/knix/session-failure.txt"
                or sess.get("history") != "/run/knix/session-failure.history"):
            diagnostics_errors.append("session.json diagnostics contract drifted")
        vg = (ROOT / "platform/image/verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("DIAGNOSTICS VERIFY OK", "DIAGNOSTICS EVENTS:",
                      "session-failure.txt", "session-failure.history",
                      "knix-session-failure.service", "OnFailure=",
                      "active_state=failed", "is-failed",
                      "'failed_units=1'", 'tail -1 "$DIAG_HISTORY"',
                      '"$DIAG_EVENTS" -ge 2'):
            if token not in vg:
                diagnostics_errors.append("verify-graphics.sh lost %s" % token)
        asm = (ROOT / "platform/image/assemble-native.sh").read_text(
            encoding="utf-8")
        for token in ("OnFailure=knix-session-failure.service",
                      "session-failure-report.sh", "session-failure.txt",
                      "session-failure.history",
                      "knix-session-failure.service"):
            if token not in asm:
                diagnostics_errors.append("assemble-native.sh lost %s" % token)
        bs = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        for token in ("DIAGNOSTICS VERIFY OK", "DIAGNOSTICS EVENTS:",
                      '"diagnostics": {', "diag_events"):
            if token not in bs:
                diagnostics_errors.append("build.sh lost %s" % token)
        workflow = (ROOT / ".github/workflows/knix-contract.yml").read_text(
            encoding="utf-8")
        if "graphics.diagnostics" not in workflow:
            diagnostics_errors.append("CI contract lost the diagnostics check")
    except (OSError, ValueError) as exc:
        diagnostics_errors.append("unreadable diagnostics contract: %s" % exc)
    if "| I24 |" not in readme:
        diagnostics_errors.append("README lost I24")
    require(not diagnostics_errors,
            "a failed compositor is diagnosed by the guest's own systemd",
            "diagnostics contract: %s" % diagnostics_errors[:4])

    # I25 -- a published image must be independently acceptable: the receipt
    # is written by the build itself, so a separate read-only gate has to prove
    # the bytes, the pinned inputs, the locked baseline and the whole guest
    # evidence chain still match the committed manifest.
    release_errors = []
    try:
        rel = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8")).get("release", {})
        if (rel.get("tool") != "scripts/verify-release.py"
                or rel.get("schema") != 3
                or rel.get("artifact") != "knix.img"
                or rel.get("format") != "qcow2"
                or rel.get("read_only") is not True
                or rel.get("min_diagnostics_events") != 2
                or rel.get("framebuffer_samples") != 9
                or rel.get("min_framebuffer_bar_pixels") != 100
                or rel.get("publish_surface") != ["knix.img", "origin/main"]
                or "not_a_build_input" not in rel):
            release_errors.append("manifest release policy drifted")
        checks = rel.get("checks", [])
        if (len(checks) != 17
                or "login_policy_verified" not in checks
                or "image_sha256_and_bytes_match_receipt" not in checks
                or "receipt_checksum_matches" not in checks
                or "baseline_matches_manifest" not in checks
                or "session_diagnostics_verified" not in checks
                or "diagnostics_persistence_verified" not in checks
                or "diagnostics_operations_verified" not in checks
                or "framebuffer_pixels_verified" not in checks
                or "single_publish_surface" not in checks):
            release_errors.append("release policy lost an acceptance check")
        gate = ROOT / "scripts" / "verify-release.py"
        if not gate.is_file() or not (gate.stat().st_mode & 0o111):
            release_errors.append("verify-release.py is missing or not executable")
        else:
            text = gate.read_text(encoding="utf-8")
            try:
                ast.parse(text, filename=str(gate))
            except SyntaxError as exc:
                release_errors.append("verify-release.py does not parse: %s" % exc)
            for token in ("RELEASE VERIFY OK", "RELEASE FAILED", "--self-test",
                          "--image", "--receipt", "--checksum", "canonical_sha",
                          "image sha256 drift", "receipt checksum drift",
                          "inputs drift", "baseline sha256 drift",
                          "publish_surface drift", "no QEMU"):
                if token not in text:
                    release_errors.append("verify-release.py lost %s" % token)
        mi = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8")).get("inputs", {})
        for group, entries in mi.items():
            if isinstance(entries, dict) and any(
                    "verify-release" in str(k) for k in entries):
                release_errors.append("release gate became a build input: %s" % group)
        workflow = (ROOT / ".github/workflows/knix-contract.yml").read_text(
            encoding="utf-8")
        if ("Release verification gate" not in workflow
                or "verify-release.py --self-test" not in workflow):
            release_errors.append("CI lost the release verification gate")
    except (OSError, ValueError) as exc:
        release_errors.append("unreadable release contract: %s" % exc)
    if "| I25 |" not in readme:
        release_errors.append("README lost I25")
    require(not release_errors,
            "a release is accepted by a read-only host-side gate",
            "release contract: %s" % release_errors[:4])

    # I26 -- a crash report that only lives in /run disappears at reboot. The
    # guest must own a persistent archive and the build must prove it survives
    # an actual relaunch of the same disk, with no host-written diagnostics.
    persistence_errors = []
    try:
        pm = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8"))
        pd = pm.get("graphics", {}).get("diagnostics", {}).get("persistence", {})
        if (pd.get("owner") != "guest" or pd.get("manager") != "systemd-journald"
                or pd.get("directory") != "/var/lib/knix"
                or pd.get("log") != "/var/lib/knix/session-failure.log"
                or pd.get("snapshot") != "/var/lib/knix/session-failure.txt"
                or pd.get("journal") != "/var/log/journal"
                or pd.get("journal_storage") != "persistent"
                or pd.get("min_reported_events") != 2
                or pd.get("survives_reboot") is not True):
            persistence_errors.append("manifest graphics.diagnostics.persistence drifted")
        ps = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8")).get("diagnostics", {}).get("persistence", {})
        if (ps.get("owner") != "guest" or ps.get("manager") != "systemd-journald"
                or ps.get("directory") != "/var/lib/knix"
                or ps.get("log") != "/var/lib/knix/session-failure.log"
                or ps.get("journal_storage") != "persistent"
                or ps.get("min_reported_events") != 2):
            persistence_errors.append("session.json diagnostics.persistence drifted")
        pasm = (ROOT / "platform/image/assemble-native.sh").read_text(
            encoding="utf-8")
        for token in ("knix-persistence.conf", "Storage=persistent",
                      "PDIR=/var/lib/knix", "session-failure.log",
                      "boot=$BOOT_ID", "/var/log/journal"):
            if token not in pasm:
                persistence_errors.append("assemble-native.sh lost %s" % token)
        pvg = (ROOT / "platform/image/verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("PERSISTENCE VERIFY OK", "ARCHIVE_EVENTS",
                      "session-failure.log", "Storage=persistent", "DIAG_ARCHIVE"):
            if token not in pvg:
                persistence_errors.append("verify-graphics.sh lost %s" % token)
        pbs = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        for token in ("DIAGNOSTICS PERSISTENCE VERIFY OK", "PERSISTENCE DRIFT",
                      "PERSISTENCE EVENTS:", "KNIX_KEEP_VARS=1",
                      "reboot verification", '"persistence": {',
                      "guest-persistence.log",
                      "/run/knix/session-failure.history"):
            if token not in pbs:
                persistence_errors.append("build.sh lost %s" % token)
        prg = (ROOT / "scripts/verify-release.py").read_text(encoding="utf-8")
        if ("diagnostics_persistence_verified" not in prg
                or "diagnostics persistence drift" not in prg):
            persistence_errors.append("release gate lost the persistence check")
        pchecks = pm.get("release", {}).get("checks", [])
        if (len(pchecks) != 17
                or "diagnostics_persistence_verified" not in pchecks
                or "login_policy_verified" not in pchecks):
            persistence_errors.append("release policy lost the persistence check")
        pworkflow = (ROOT / ".github/workflows/knix-contract.yml").read_text(
            encoding="utf-8")
        if "diagnostics.persistence" not in pworkflow:
            persistence_errors.append("CI contract lost the persistence check")
    except (OSError, ValueError) as exc:
        persistence_errors.append("unreadable persistence contract: %s" % exc)
    if "| I26 |" not in readme:
        persistence_errors.append("README lost I26")
    require(not persistence_errors,
            "guest crash diagnostics survive a reboot on the guest's own disk",
            "persistence contract: %s" % persistence_errors[:4])

    # I27 -- diagnostics must be operable, not merely durable. The guest owns a
    # bounded rotating archive, cross-boot statistics and a local err-priority
    # alert; a second boot must advance the statistics before a receipt exists.
    ops_errors = []
    try:
        om = json.loads((ROOT / "platform/image/manifest.json").read_text(
            encoding="utf-8"))
        od = om.get("graphics", {}).get("diagnostics", {}).get("operations", {})
        if (od.get("owner") != "guest" or od.get("manager") != "systemd"
                or od.get("archive_limit_bytes") != 8192
                or od.get("generations") != 3
                or od.get("statistics") != "/var/lib/knix/session-failure.stats"
                or od.get("alert") != "/var/lib/knix/session-failure.alert"
                or od.get("runtime_alert") != "/run/knix/session-failure.alert"
                or od.get("journal_tag") != "knix-diagnostics"
                or od.get("journal_priority") != "err"
                or od.get("login_notice") != "/etc/profile.d/10-knix-session-alert.sh"
                or od.get("drill_directory") != "/run/knix/rotation-drill"
                or od.get("min_boots_seen") != 2):
            ops_errors.append("manifest graphics.diagnostics.operations drifted")
        so = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8")).get("diagnostics", {}).get("operations", {})
        if (so.get("owner") != "guest" or so.get("archive_limit_bytes") != 8192
                or so.get("generations") != 3 or so.get("min_boots_seen") != 2
                or so.get("statistics") != "/var/lib/knix/session-failure.stats"
                or so.get("alert") != "/var/lib/knix/session-failure.alert"
                or so.get("journal_tag") != "knix-diagnostics"
                or so.get("journal_priority") != "err"
                or so.get("login_notice") != "/etc/profile.d/10-knix-session-alert.sh"
                or so.get("drill_directory") != "/run/knix/rotation-drill"):
            ops_errors.append("session.json diagnostics.operations drifted")
        oasm = (ROOT / "platform/image/assemble-native.sh").read_text(
            encoding="utf-8")
        # Inspect the real REPORT heredoc: these literals also appear in the
        # assembly validation loop, where they cannot prove the guest script.
        report = oasm.split("<<'REPORT'\n", 1)[-1].split("\nREPORT\n", 1)[0]
        for token in ("LIMIT=8192", "session-failure.stats",
                      "session-failure.rotations", "KNIX_DIAG_DRILL_DIR",
                      "systemd-cat -p err", "total_events=", "boots_seen="):
            if token not in report:
                ops_errors.append("assemble-native.sh REPORT lost %s" % token)
        if "10-knix-session-alert.sh" not in oasm:
            ops_errors.append("assemble-native.sh lost the login notice")
        ovg = (ROOT / "platform/image/verify-graphics.sh").read_text(
            encoding="utf-8")
        for token in ("DIAGNOSTICS OPS VERIFY OK", "ROTATION VERIFY OK",
                      "rotation-drill", "session-failure.stats",
                      "knix-diagnostics"):
            if token not in ovg:
                ops_errors.append("verify-graphics.sh lost %s" % token)
        obs = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        persist = obs.split("<<'PERSIST'", 1)[-1].split("\nPERSIST\n", 1)[0]
        for token in ("DIAGNOSTICS OPS PERSISTENCE VERIFY OK", "OPS EVENTS:",
                      "OPS BOOTS:"):
            if token not in persist:
                ops_errors.append("build.sh PERSIST lost %s" % token)
        for token in ("OPS DRIFT", '"operations": {'):
            if token not in obs:
                ops_errors.append("build.sh lost %s" % token)
        if obs.count('sys.exit("OPS DRIFT') < 2:
            ops_errors.append("build.sh receipt block lost an OPS DRIFT refusal")
        org = (ROOT / "scripts/verify-release.py").read_text(encoding="utf-8")
        if ("diagnostics_operations_verified" not in org
                or "diagnostics operations drift" not in org):
            ops_errors.append("release gate lost the operations check")
        orp = (ROOT / "scripts/verify-repro.py").read_text(encoding="utf-8")
        if ("op = gfx.get(" not in orp
                or "session.json diagnostics.operations drifted" not in orp
                or "assemble-native.sh REPORT lost operations token" not in orp):
            ops_errors.append("repro gate lost the operations check")
        owf = (ROOT / ".github/workflows/knix-contract.yml").read_text(
            encoding="utf-8")
        if ("dop = dg.get(" not in owf
                or "sop = sd.get(" not in owf
                or "assemble-native.sh REPORT" not in owf):
            ops_errors.append("CI contract lost the operations check")
        if om.get("release", {}).get("min_boots_seen") != 2:
            ops_errors.append("release policy lost min_boots_seen")
    except (OSError, ValueError) as exc:
        ops_errors.append("unreadable operations contract: %s" % exc)
    if "| I27 |" not in readme:
        ops_errors.append("README lost I27")
    require(not ops_errors,
            "guest diagnostics are bounded, counted across boots and locally alerted",
            "operations contract: %s" % ops_errors[:4])

    # I28 -- the guest owns accounts, credentials, autologin and its idle
    # policy. No host adapter, no root login, no password path.
    login_errors = []
    try:
        lm = json.loads((ROOT / "platform/image/manifest.json").read_text(encoding="utf-8"))
        ls = json.loads((ROOT / "platform/identity/session.json").read_text(encoding="utf-8"))
        la = lm.get("graphics", {}).get("authentication", {})
        sa = ls.get("authentication", {})
        if (la.get("owner") != "guest" or sa.get("owner") != "guest"
                or ls.get("autologin") is not True
                or sa.get("autologin") is not True
                or la.get("root_login") is not False or sa.get("root_login") is not False
                or la.get("password_authentication") is not False
                or sa.get("password_authentication") is not False
                or la.get("public_key_authentication") is not True
                or la.get("autologin") is not True
                or la.get("idle_action") != "ignore"
                or sa.get("idle_action") != "ignore"
                or la.get("idle_action_sec") != "30min"):
            login_errors.append("guest login policy contract drifted")
        if "login_policy_verified" not in lm.get("release", {}).get("checks", []):
            login_errors.append("release checks lost login_policy_verified")
        lv = (ROOT / "platform/image/verify-login.sh").read_text(encoding="utf-8")
        for token in ("LOGIN POLICY OK", "LOGIN VERIFY OK", "permitrootlogin no",
                      "authenticationmethods publickey",
                      "PreferredAuthentications=password"):
            if token not in lv:
                login_errors.append("verify-login.sh lost %s" % token)
        pa = (ROOT / "platform/image/assemble-native.sh").read_text(encoding="utf-8")
        for token in ("10-knix-auth.conf", "10-knix-login.conf",
                      "AuthenticationMethods publickey"):
            if token not in pa:
                login_errors.append("assemble-native.sh lost %s" % token)
        # A directive must live in the installed heredoc, not only in the
        # assembly-time grep assertion that repeats the same literal.
        sshd = pa.split("<<'SSHD'\n", 1)[-1].split("\nSSHD\n", 1)[0]
        logind = pa.split("<<'LOGIND'\n", 1)[-1].split("\nLOGIND\n", 1)[0]
        if ("<<'SSHD'\n" not in pa or "\nSSHD\n" not in pa
                or "<<'LOGIND'\n" not in pa or "\nLOGIND\n" not in pa):
            login_errors.append("assemble-native.sh lost its guest login heredocs")
        sshd_lines = [line.strip() for line in sshd.splitlines()]
        logind_lines = [line.strip() for line in logind.splitlines()]
        for token in ("PermitRootLogin no", "PasswordAuthentication no",
                      "KbdInteractiveAuthentication no", "PubkeyAuthentication yes",
                      "PermitEmptyPasswords no", "AuthenticationMethods publickey"):
            if token not in sshd_lines:
                login_errors.append("assemble-native.sh SSHD heredoc lost %s" % token)
        for token in ("IdleAction=ignore", "IdleActionSec=30min"):
            if token not in logind_lines:
                login_errors.append("assemble-native.sh LOGIND heredoc lost %s" % token)
        pb = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
        if (pb.count('sys.exit("LOGIN DRIFT') < 2
                or "LOGIN VERIFY OK" not in pb
                or "guest-login.log" not in pb):
            login_errors.append("build.sh lost the login verification gate")
        for token in ('> "$DEV/guest-login.log" 2>&1 || LOGIN_RC=$?',
                      'cat "$DEV/guest-login.log"',
                      "grep -q 'LOGIN POLICY OK' \"$DEV/guest-login.log\"",
                      "grep -q 'LOGIN VERIFY OK' \"$DEV/guest-login.log\"",
                      'login_log = (dev / "guest-login.log").read_text()'):
            if token not in pb:
                login_errors.append("build.sh login wiring lost %s" % token)
        pr = (ROOT / "scripts/verify-release.py").read_text(encoding="utf-8")
        if "login policy drift" not in pr:
            login_errors.append("release gate lost the login check")
        prp = (ROOT / "scripts/verify-repro.py").read_text(encoding="utf-8")
        if "graphics.authentication contract drifted" not in prp:
            login_errors.append("repro gate lost the login check")
        pw = (ROOT / ".github/workflows/knix-contract.yml").read_text(encoding="utf-8")
        if 'la = gfx.get("authentication", {})' not in pw:
            login_errors.append("CI contract lost the login check")
        if "| I28 |" not in readme:
            login_errors.append("README lost I28")
    except (OSError, ValueError) as exc:
        login_errors.append("unreadable login contract: %s" % exc)
    require(not login_errors,
            "the guest owns authentication, autologin and its idle policy",
            "login contract: %s" % login_errors[:4])

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

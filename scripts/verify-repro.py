#!/usr/bin/env python3
"""Verify the knix reproducible-build contract. Read-only.

`scripts/verify-knix.py` owns the collaboration invariants; this script owns
the build-reproducibility invariants: every declared input digest, the locked
kernel/rootfs baseline, the package-version lockfile, the receipt contract and
the rule that binary build artifacts never enter the repository.

It runs on any host with Python 3.9+ and no third-party modules. The seed
qcow2 and the QEMU/HVF build stay on the local build machine.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "platform" / "image" / "manifest.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_SUFFIXES = (".img", ".qcow2", ".zst", ".fd", ".iso", ".ko", ".ppm")
ARTIFACT_NAMES = {"BUILD-RECEIPT.json", "BUILD-RECEIPT.json.sha256", "knix-Image"}
SCAN_DIRS = ("platform", "scripts", "docs", ".github")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    errors = []
    if not MANIFEST.is_file():
        print("REPRO FAILED: missing platform/image/manifest.json", file=sys.stderr)
        return 1
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("REPRO FAILED: unreadable manifest: %s" % exc, file=sys.stderr)
        return 1

    # 1. every in-repo build input is byte-identical to its pinned digest
    checked = 0
    for group, entries in m.get("inputs", {}).items():
        if group == "seed" or not isinstance(entries, dict):
            continue
        for rel, expected in entries.items():
            p = ROOT / rel
            if not HEX64.match(str(expected)):
                errors.append("%s has a malformed digest for %s" % (group, rel))
                continue
            if not p.is_file():
                errors.append("%s input missing: %s" % (group, rel))
                continue
            checked += 1
            if sha256(p) != expected:
                errors.append("%s input drift: %s" % (group, rel))
    if checked == 0:
        errors.append("no build inputs were checked")

    # 2. the guest-produced kernel and rootfs are pinned by an in-repo baseline
    baseline = m.get("build_baseline")
    if not isinstance(baseline, dict):
        baseline = {}
        errors.append("manifest lost the build_baseline lock")
    kernel = baseline.get("kernel", {})
    rootfs = baseline.get("rootfs", {})
    kernel_hex = ("config_sha256", "image_sha256", "image_gz_sha256")
    rootfs_hex = ("tarball_sha256", "package_set_sha256", "package_versions_sha256")
    for key in kernel_hex:
        if not HEX64.match(str(kernel.get(key, ""))):
            errors.append("baseline.kernel.%s is missing or malformed" % key)
    for key in rootfs_hex:
        if not HEX64.match(str(rootfs.get(key, ""))):
            errors.append("baseline.rootfs.%s is missing or malformed" % key)
    for key in ("kernel_version", "graphics_drivers", "graphics_modules"):
        if not kernel.get(key):
            errors.append("baseline.kernel.%s is missing" % key)
    for key in ("module_count",):
        if not isinstance(kernel.get(key), int) or kernel[key] <= 0:
            errors.append("baseline.kernel.%s must be a positive integer" % key)
    for key in ("tarball_bytes", "package_count", "package_versions_count"):
        if not isinstance(rootfs.get(key), int) or rootfs[key] <= 0:
            errors.append("baseline.rootfs.%s must be a positive integer" % key)

    # 3. the package-version lockfile is a build input, not a build artifact
    lock_rel = "platform/image/package-versions.txt"
    lock = ROOT / lock_rel
    if not lock.is_file():
        errors.append("missing package-version lockfile: %s" % lock_rel)
    else:
        lock_bytes = lock.read_bytes()
        if hashlib.sha256(lock_bytes).hexdigest() != rootfs.get("package_versions_sha256"):
            errors.append("package-version lockfile does not match the locked digest")
        lines = lock_bytes.decode("utf-8").splitlines()
        if not lines:
            errors.append("package-version lockfile is empty")
        names = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) != 3 or not all(parts):
                errors.append("malformed lockfile line: %r" % line[:80])
                break
            names.append(parts[0])
        if names != sorted(names) or len(names) != len(set(names)):
            errors.append("package-version lockfile is not sorted and unique")
        if lines and len(lines) != rootfs.get("package_versions_count"):
            errors.append("package_versions_count does not match the lockfile")
        declared = m.get("inputs", {}).get("baseline_sha256", {})
        if lock_rel not in declared or not HEX64.match(str(declared.get(lock_rel, ""))):
            errors.append("lockfile is not pinned in inputs.baseline_sha256")

    # 4. every declared build stage is a real, executable script
    stages = m.get("build_stages", [])
    if len(stages) < 3:
        errors.append("build_stages lost a stage")
    for stage in stages:
        rel = stage.split()[0]
        p = ROOT / rel
        if not p.is_file():
            errors.append("build stage missing: %s" % rel)
        elif not (p.stat().st_mode & 0o111):
            errors.append("build stage is not executable: %s" % rel)

    # 5. single publish surface and the UEFI/graphics contract stay intact
    if m.get("artifact", {}).get("name") != "knix.img":
        errors.append("artifact is no longer knix.img")
    if m.get("artifact", {}).get("single") is not True:
        errors.append("single-artifact rule lost")
    if m.get("publish_surface") != ["knix.img", "origin/main"]:
        errors.append("publish_surface drifted")
    if m.get("boot", {}).get("method") != "UEFI self-boot":
        errors.append("boot.method drifted")
    if "BOOTAA64.EFI" not in m.get("boot", {}).get("esp", ""):
        errors.append("boot.esp lost the removable media path")
    gfx = m.get("graphics", {})
    if gfx.get("owner") != "guest" or gfx.get("device") != "virtio-gpu-pci":
        errors.append("guest-owned graphics contract drifted")
    if gfx.get("autostart") is not True or gfx.get("autostart_target") != "multi-user.target":
        errors.append("guest-owned autostart contract drifted")
    res = gfx.get("resilience", {})
    if (res.get("owner") != "guest" or res.get("manager") != "systemd"
            or res.get("restart") != "on-failure"
            or res.get("restart_sec") != 2
            or res.get("start_limit_burst") != 5
            or res.get("start_limit_interval_sec") != 30):
        errors.append("guest-owned crash-recovery contract drifted")
    if not (ROOT / gfx.get("verify", "")).is_file():
        errors.append("graphics verification recipe is missing")
    else:
        vgfx = (ROOT / gfx["verify"]).read_text(encoding="utf-8")
        for token in ("RESILIENCE VERIFY OK", "systemctl kill --signal=SIGKILL"):
            if token not in vgfx:
                errors.append("verify-graphics.sh lost recovery proof: %s" % token)

    # 5b. the framebuffer pixel proof is guest-owned and build-time enforced
    fb = gfx.get("framebuffer", {})
    if (fb.get("owner") != "guest"
            or fb.get("resolution") != [1280, 800]
            or fb.get("samples") != 9
            or fb.get("verify") != "platform/image/verify-framebuffer.py"
            or "QMP screendump (P6)" not in fb.get("capture", "")):
        errors.append("graphics.framebuffer pixel contract drifted")
    fb_tool = ROOT / "platform/image/verify-framebuffer.py"
    if not fb_tool.is_file():
        errors.append("framebuffer verification recipe is missing")
    else:
        text = fb_tool.read_text(encoding="utf-8")
        try:
            compile(text, str(fb_tool), "exec")
        except SyntaxError as exc:
            errors.append("verify-framebuffer.py does not parse: %s" % exc)
        for token in ("--self-test", '"format": "ppm"', "FRAMEBUFFER VERIFY OK",
                      "FRAMEBUFFER PATTERN:", "qmp_capabilities", "screendump",
                      "guest pattern"):
            if token not in text:
                errors.append("verify-framebuffer.py lost %s" % token)
    vgfx_path = ROOT / gfx.get("verify", "")
    if vgfx_path.is_file():
        text = vgfx_path.read_text(encoding="utf-8")
        for token in ("FRAMEBUFFER PATTERN:", "/dev/urandom", "solid_color",
                      "FRAMEBUFFER PATTERN PAINTED"):
            if token not in text:
                errors.append("verify-graphics.sh lost %s" % token)
    build_text = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
    for token in ("FRAMEBUFFER VERIFY OK", "verify-framebuffer.py",
                  '"framebuffer": {', "framebuffer-verify.json"):
        if token not in build_text:
            errors.append("build.sh lost framebuffer gate: %s" % token)

    # 5c. a crash loop is stopped by guest systemd, with a real fallback path
    cl = gfx.get("crash_loop", {})
    if (cl.get("owner") != "guest" or cl.get("manager") != "systemd"
            or cl.get("burst") != 5 or cl.get("interval_sec") != 30
            or cl.get("degraded_failed_units") != 1
            or cl.get("signal") != "Start request repeated too quickly."):
        errors.append("graphics.crash_loop contract drifted")
    vg_path = ROOT / gfx.get("verify", "")
    if not vg_path.is_file():
        errors.append("graphics verification recipe is missing")
    else:
        text = vg_path.read_text(encoding="utf-8")
        for token in ("LIMIT VERIFY OK", "Start request repeated too quickly",
                      "systemctl reset-failed", "sway-ipc.*.sock",
                      "FAILED_UNITS", "ssh.service", "/dev/ttyAMA0"):
            if token not in text:
                errors.append("verify-graphics.sh lost crash-loop token: %s" % token)
    for token in ("LIMIT VERIFY OK", '"crash_loop": {'):
        if token not in build_text:
            errors.append("build.sh lost crash-loop gate: %s" % token)
    try:
        sl = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8")).get("resilience", {}).get("crash_loop", {})
    except (OSError, ValueError):
        sl = {}
    if (sl.get("owner") != "guest" or sl.get("manager") != "systemd"
            or sl.get("burst") != 5 or sl.get("interval_sec") != 30):
        errors.append("session.json resilience.crash_loop drifted")

    # 6. the receipt records the same baseline the build verifies
    build = (ROOT / "platform/image/build.sh").read_text(encoding="utf-8")
    for token in ("build_baseline", "BASELINE DRIFT", "package-versions.txt",
                  '"baseline"', "graphics", "seed_sha512",
                  "AUTOSTART VERIFY OK", '"autostart"'):
        if token not in build:
            errors.append("build.sh lost receipt/verification field: %s" % token)
    recipe = (ROOT / "platform/image/build-rootfs.sh").read_text(encoding="utf-8")
    for token in ("package_versions_sha256", "package_versions_count", "dpkg-query"):
        if token not in recipe:
            errors.append("build-rootfs.sh lost the package lock: %s" % token)

    # 6b. refreshing the locked baseline stays explicit, scoped and testable
    refresh_path = ROOT / "scripts/refresh-baseline.py"
    if not refresh_path.is_file():
        errors.append("baseline refresh tool is missing")
    else:
        refresh = refresh_path.read_text(encoding="utf-8")
        try:
            compile(refresh, str(refresh_path), "exec")
        except SyntaxError as exc:
            errors.append("baseline refresh tool does not parse: %s" % exc)
        for token in ("--check", "--propose", "--apply", "--reason",
                      "--self-test", "BASELINE-PROPOSAL.json",
                      "kernel_drift_is_never_refreshed",
                      "REFUSED: stale proposal"):
            if token not in refresh:
                errors.append("refresh-baseline.py lost policy token: %s" % token)
    policy = m.get("baseline_refresh", {})
    if (policy.get("tool") != "scripts/refresh-baseline.py"
            or policy.get("require_proposal") is not True
            or policy.get("require_reason_chars") != 8
            or policy.get("fingerprints_must_match") is not True):
        errors.append("baseline_refresh policy drifted")

    # 7. binary build artifacts never enter the repository
    for top in SCAN_DIRS:
        base = ROOT / top
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            if p.name in ARTIFACT_NAMES or p.name.endswith(ARTIFACT_SUFFIXES):
                errors.append("build artifact in repository: %s" % p.relative_to(ROOT))

    if errors:
        for e in errors:
            print("REPRO FAILED: %s" % e, file=sys.stderr)
        print("\n%d reproducibility check(s) failed." % len(errors), file=sys.stderr)
        return 1
    print("REPRO OK: %d pinned inputs, locked kernel %s/%d modules, "
          "locked rootfs %d packages, %d build stages, no artifacts in tree"
          % (checked, kernel.get("kernel_version"), kernel.get("module_count"),
             rootfs.get("package_versions_count"), len(stages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

    # 5d. a failed compositor is diagnosed inside the guest, not by the host
    diag = gfx.get("diagnostics", {})
    if (diag.get("owner") != "guest" or diag.get("manager") != "systemd"
            or diag.get("unit") != "/etc/systemd/system/knix-session-failure.service"
            or diag.get("script") != "/usr/lib/knix/session-failure-report.sh"
            or diag.get("report") != "/run/knix/session-failure.txt"
            or diag.get("history") != "/run/knix/session-failure.history"
            or diag.get("min_reported_events") != 2
            or "OnFailure=" not in diag.get("trigger", "")):
        errors.append("graphics.diagnostics contract drifted")
    asm_path = ROOT / "platform/image/assemble-native.sh"
    if not asm_path.is_file():
        errors.append("assemble-native.sh is missing")
    else:
        asm = asm_path.read_text(encoding="utf-8")
        # Check the real unit heredoc: a validation-loop literal with the same
        # words must not be able to stand in for the directive itself.
        heredoc = asm.split("<<'UNIT'\n", 1)[-1].split("\nUNIT\n", 1)[0]
        lines = [line.strip() for line in heredoc.splitlines()]
        if "OnFailure=knix-session-failure.service" not in lines:
            errors.append("assemble-native.sh unit heredoc lost OnFailure=")
        for token in ("session-failure-report.sh", "session-failure.txt",
                      "session-failure.history", "knix-session-failure.service",
                      "owner=guest", "exit 0"):
            if token not in asm:
                errors.append("assemble-native.sh lost diagnostics token: %s" % token)
    vg_diag = (ROOT / gfx.get("verify", "")).read_text(encoding="utf-8")
    for token in ("DIAGNOSTICS VERIFY OK", "DIAGNOSTICS EVENTS:",
                  "session-failure.txt", "session-failure.history",
                  "knix-session-failure.service", "OnFailure=",
                  "active_state=failed", "is-failed",
                  "'failed_units=1'", 'tail -1 "$DIAG_HISTORY"',
                  '"$DIAG_EVENTS" -ge 2'):
        if token not in vg_diag:
            errors.append("verify-graphics.sh lost diagnostics token: %s" % token)
    for token in ("DIAGNOSTICS VERIFY OK", "DIAGNOSTICS EVENTS:",
                  '"diagnostics": {', "diag_events"):
        if token not in build_text:
            errors.append("build.sh lost diagnostics gate: %s" % token)
    try:
        sd = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8")).get("diagnostics", {})
    except (OSError, ValueError):
        sd = {}
    if (sd.get("owner") != "guest" or sd.get("manager") != "systemd"
            or "OnFailure=" not in sd.get("trigger", "")
            or sd.get("report") != "/run/knix/session-failure.txt"):
        errors.append("session.json diagnostics contract drifted")

    # 5e. the crash timeline is archived by the guest on its own root disk and
    # survives a real reboot; /run/knix remains only a runtime snapshot.
    dp = gfx.get("diagnostics", {}).get("persistence", {})
    if (dp.get("owner") != "guest" or dp.get("manager") != "systemd-journald"
            or dp.get("log") != "/var/lib/knix/session-failure.log"
            or dp.get("snapshot") != "/var/lib/knix/session-failure.txt"
            or dp.get("journal") != "/var/log/journal"
            or dp.get("journal_storage") != "persistent"
            or dp.get("min_reported_events") != 2
            or dp.get("survives_reboot") is not True):
        errors.append("graphics.diagnostics.persistence contract drifted")
    sp = sd.get("persistence", {})
    if (sp.get("owner") != "guest" or sp.get("manager") != "systemd-journald"
            or sp.get("log") != "/var/lib/knix/session-failure.log"
            or sp.get("journal_storage") != "persistent"):
        errors.append("session.json diagnostics.persistence drifted")
    for token in ("knix-persistence.conf", "Storage=persistent",
                  "PDIR=/var/lib/knix", "session-failure.log", "boot=$BOOT_ID"):
        if token not in asm:
            errors.append("assemble-native.sh lost persistence token: %s" % token)
    for token in ("PERSISTENCE VERIFY OK", "ARCHIVE_EVENTS",
                  "session-failure.log", "Storage=persistent"):
        if token not in vg_diag:
            errors.append("verify-graphics.sh lost persistence token: %s" % token)
    for token in ("DIAGNOSTICS PERSISTENCE VERIFY OK", "PERSISTENCE DRIFT",
                  "PERSISTENCE EVENTS:", "KNIX_KEEP_VARS=1",
                  "guest-persistence.log", '"persistence": {'):
        if token not in build_text:
            errors.append("build.sh lost persistence gate: %s" % token)
    rg_text = (ROOT / "scripts/verify-release.py").read_text(encoding="utf-8")
    if "diagnostics_persistence_verified" not in rg_text:
        errors.append("release gate lost the persistence acceptance check")

    # 5f. diagnostics.operations: the guest bounds and rotates its archive,
    # recombines cross-boot statistics and raises a local err-priority alert.
    # A verification drill is confined to /run/knix so the shipped archive is
    # never rotated by a verification step.
    op = gfx.get("diagnostics", {}).get("operations", {})
    if (op.get("owner") != "guest" or op.get("manager") != "systemd"
            or op.get("archive_limit_bytes") != 8192
            or op.get("generations") != 3
            or op.get("statistics") != "/var/lib/knix/session-failure.stats"
            or op.get("alert") != "/var/lib/knix/session-failure.alert"
            or op.get("journal_tag") != "knix-diagnostics"
            or op.get("journal_priority") != "err"
            or op.get("login_notice") != "/etc/profile.d/10-knix-session-alert.sh"
            or op.get("drill_directory") != "/run/knix/rotation-drill"
            or op.get("min_boots_seen") != 2):
        errors.append("graphics.diagnostics.operations contract drifted")
    so = sd.get("operations", {})
    if (so.get("owner") != "guest" or so.get("archive_limit_bytes") != 8192
            or so.get("generations") != 3
            or so.get("statistics") != "/var/lib/knix/session-failure.stats"
            or so.get("alert") != "/var/lib/knix/session-failure.alert"
            or so.get("journal_tag") != "knix-diagnostics"
            or so.get("journal_priority") != "err"
            or so.get("login_notice") != "/etc/profile.d/10-knix-session-alert.sh"
            or so.get("drill_directory") != "/run/knix/rotation-drill"
            or so.get("min_boots_seen") != 2):
        errors.append("session.json diagnostics.operations drifted")
    # Token checks must inspect the real heredocs. The same literals are also
    # listed in the validation loops below them, so a whole-file `in` check can
    # pass even after the guest script itself was neutered.
    report = asm.split("<<'REPORT'\n", 1)[-1].split("\nREPORT\n", 1)[0]
    for token in ("LIMIT=8192", "session-failure.stats", "session-failure.rotations",
                  "KNIX_DIAG_DRILL_DIR", "systemd-cat -p err",
                  "total_events=", "boots_seen="):
        if token not in report:
            errors.append("assemble-native.sh REPORT lost operations token: %s" % token)
    if "10-knix-session-alert.sh" not in asm:
        errors.append("assemble-native.sh lost the login-visible notice")
    for token in ("DIAGNOSTICS OPS VERIFY OK", "ROTATION VERIFY OK",
                  "rotation-drill", "session-failure.stats", "knix-diagnostics"):
        if token not in vg_diag:
            errors.append("verify-graphics.sh lost operations token: %s" % token)
    persist = build_text.split("<<'PERSIST'", 1)[-1].split("\nPERSIST\n", 1)[0]
    for token in ("DIAGNOSTICS OPS PERSISTENCE VERIFY OK", "OPS EVENTS:", "OPS BOOTS:"):
        if token not in persist:
            errors.append("build.sh PERSIST lost operations gate: %s" % token)
    for token in ("OPS DRIFT", '"operations": {'):
        if token not in build_text:
            errors.append("build.sh lost operations gate: %s" % token)
    # Count the complete refusal statements, not the bare literal: renaming a
    # message to "NO OPS DRIFT" would keep the substring and fool a raw count.
    if build_text.count('sys.exit("OPS DRIFT') < 2:
        errors.append("build.sh receipt block lost an OPS DRIFT refusal")
    if ("diagnostics_operations_verified" not in rg_text
            or "diagnostics operations drift" not in rg_text):
        errors.append("release gate lost the operations acceptance check")

    # 5g. login policy: accounts, credentials and login decisions stay in the
    # guest. sshd refuses root and every password path; logind carries an
    # explicit idle policy; the kiosk session needs no host-supplied login.
    la = gfx.get("authentication", {})
    if (la.get("owner") != "guest" or la.get("manager") != "systemd-logind"
            or la.get("policy") != "/etc/ssh/sshd_config.d/10-knix-auth.conf"
            or la.get("logind_policy") != "/etc/systemd/logind.conf.d/10-knix-login.conf"
            or la.get("verify") != "platform/image/verify-login.sh"
            or la.get("accounts") != ["knix"]
            or la.get("root_login") is not False
            or la.get("password_authentication") is not False
            or la.get("public_key_authentication") is not True
            or la.get("autologin") is not True
            or la.get("no_getty_autologin") is not True
            or la.get("idle_action") != "ignore"
            or la.get("idle_action_sec") != "30min"):
        errors.append("graphics.authentication contract drifted")
    try:
        auth_doc = json.loads((ROOT / "platform/identity/session.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        auth_doc = {}
    sa = auth_doc.get("authentication", {})
    if (sa.get("owner") != "guest" or auth_doc.get("autologin") is not True
            or sa.get("root_login") is not False
            or sa.get("password_authentication") is not False
            or sa.get("public_key_authentication") is not True
            or sa.get("autologin") is not True
            or sa.get("accounts") != ["knix"]
            or sa.get("idle_action") != "ignore"
            or sa.get("idle_action_sec") != "30min"):
        errors.append("session.json authentication contract drifted")
    login_script = ROOT / "platform/image/verify-login.sh"
    if not login_script.is_file():
        errors.append("verify-login.sh is missing")
    else:
        lv = login_script.read_text(encoding="utf-8")
        for token in ("LOGIN POLICY OK", "LOGIN VERIFY OK", "permitrootlogin no",
                      "passwordauthentication no", "authenticationmethods publickey",
                      "IdleAction=ignore", "PreferredAuthentications=password",
                      "root@127.0.0.1"):
            if token not in lv:
                errors.append("verify-login.sh lost token: %s" % token)
    for token in ("10-knix-auth.conf", "10-knix-login.conf", "PermitRootLogin no",
                  "AuthenticationMethods publickey", "IdleAction=ignore",
                  "passwd -l root"):
        if token not in asm:
            errors.append("assemble-native.sh lost login token: %s" % token)
    # Assembly-time grep assertions repeat the same literals as the installed
    # policy. Slice the real heredocs so only the guest-owned file content
    # can satisfy the contract.
    sshd = asm.split("<<'SSHD'\n", 1)[-1].split("\nSSHD\n", 1)[0]
    logind = asm.split("<<'LOGIND'\n", 1)[-1].split("\nLOGIND\n", 1)[0]
    if ("<<'SSHD'\n" not in asm or "\nSSHD\n" not in asm
            or "<<'LOGIND'\n" not in asm or "\nLOGIND\n" not in asm):
        errors.append("assemble-native.sh lost its guest login heredocs")
    sshd_lines = [line.strip() for line in sshd.splitlines()]
    logind_lines = [line.strip() for line in logind.splitlines()]
    for token in ("PermitRootLogin no", "PasswordAuthentication no",
                  "KbdInteractiveAuthentication no", "PubkeyAuthentication yes",
                  "PermitEmptyPasswords no", "AuthenticationMethods publickey"):
        if token not in sshd_lines:
            errors.append("assemble-native.sh SSHD heredoc lost %s" % token)
    for token in ("IdleAction=ignore", "IdleActionSec=30min"):
        if token not in logind_lines:
            errors.append("assemble-native.sh LOGIND heredoc lost %s" % token)
    for token in ("LOGIN POLICY OK", "LOGIN VERIFY OK", "guest-login.log",
                  '"authentication": {'):
        if token not in build_text:
            errors.append("build.sh lost login gate: %s" % token)
    for token in ('> "$DEV/guest-login.log" 2>&1 || LOGIN_RC=$?',
                  'cat "$DEV/guest-login.log"',
                  "grep -q 'LOGIN POLICY OK' \"$DEV/guest-login.log\"",
                  "grep -q 'LOGIN VERIFY OK' \"$DEV/guest-login.log\"",
                  'login_log = (dev / "guest-login.log").read_text()'):
        if token not in build_text:
            errors.append("build.sh login wiring lost %s" % token)
    if build_text.count('sys.exit("LOGIN DRIFT') < 2:
        errors.append("build.sh receipt block lost a LOGIN DRIFT refusal")
    if ("login_policy_verified" not in rg_text
            or "login policy drift" not in rg_text):
        errors.append("release gate lost the login policy check")

    # 5h. diagnostics trends: the same guest OnFailure= script recomputes a
    # bounded per-boot profile from its own archive. No host collector, no
    # network channel and no external analytics service may appear here.
    mt = gfx.get("diagnostics", {}).get("trends", {})
    expected_fields = ["events", "failed_states", "limit_events",
                       "restarts_peak", "first_seen", "last_seen"]
    if (mt.get("owner") != "guest" or mt.get("manager") != "systemd"
            or mt.get("report") != "/var/lib/knix/session-failure.trends"
            or "session-failure.log" not in mt.get("source", "")
            or mt.get("boot_limit") != 8 or mt.get("byte_limit") != 4096
            or mt.get("per_boot_fields") != expected_fields
            or mt.get("min_boots_seen") != 2):
        errors.append("graphics.diagnostics.trends contract drifted")
    st = sd.get("trends", {})
    if (st.get("owner") != "guest" or st.get("manager") != "systemd"
            or st.get("report") != mt.get("report")
            or st.get("boot_limit") != 8 or st.get("byte_limit") != 4096
            or st.get("per_boot_fields") != expected_fields
            or st.get("min_boots_seen") != 2):
        errors.append("session.json diagnostics.trends contract drifted")
    # Slice the real guest script heredoc: validation-loop literals elsewhere
    # in the recipe must not be able to stand in for the installed logic.
    report = asm.split("<<'REPORT'\n", 1)[-1].split("\nREPORT\n", 1)[0]
    if "<<'REPORT'\n" not in asm or "\nREPORT\n" not in asm:
        errors.append("assemble-native.sh lost its guest REPORT heredoc")
    for token in ("session-failure.trends", "TREND_BOOTS=8", "TREND_LIMIT=4096",
                  "write_trends()", "trend_events_sum=", "restarts_peak=",
                  "s/.* events=", "--- per-boot (newest first, guest-owned) ---"):
        if token not in report:
            errors.append("assemble-native.sh REPORT heredoc lost trends token: %s" % token)
    for token in ("TRENDS VERIFY OK", "TRENDS BOOTS:", "TRENDS EVENTS:",
                  "trend_boots_kept=", "trend_events_sum=", "s/.* events=",
                  "guest trend rows carry no events"):
        if token not in vg_diag:
            errors.append("verify-graphics.sh lost trends token: %s" % token)
    for token in ("TRENDS VERIFY OK", "DIAGNOSTICS TRENDS PERSISTENCE VERIFY OK",
                  '"trends": {', "TRENDS BOOTS:", "TRENDS EVENTS:", "s/.* events=",
                  "guest trend rows carry no events"):
        if token not in build_text:
            errors.append("build.sh lost trends gate: %s" % token)
    if build_text.count('sys.exit("TRENDS DRIFT') < 2:
        errors.append("build.sh receipt block lost a TRENDS DRIFT refusal")
    # Check the real refusal statement, not any occurrence of the drift label:
    # the self-test's expected-error literals must not be able to stand in.
    if ("diagnostics_trends_verified" not in rg_text
            or 'errors.append("diagnostics trends drift: guest per-boot trend report was not proven")'
            not in rg_text):
        errors.append("release gate lost the trends acceptance check")

    # 5i. diagnostics status: one guest-owned read-only command reconciles all
    # six evidence sources, derives the trend row sum itself and must be able
    # to refuse injected drift. Slice the installed STATUS heredoc so a
    # validation-loop literal cannot stand in for the guest command.
    ms = gfx.get("diagnostics", {}).get("status", {})
    expected_checks = ["archive_vs_stats", "stats_vs_trends", "boots_vs_boots",
                       "rows_vs_sum", "sources_present"]
    if (ms.get("owner") != "guest"
            or ms.get("command") != "/usr/local/bin/knix-status"
            or ms.get("script") != "/usr/lib/knix/knix-status.sh"
            or ms.get("format") != ["text", "json"]
            or len(ms.get("sources", [])) != 6
            or ms.get("consistency_checks") != expected_checks
            or ms.get("drill_directory") != "/run/knix/status-drill"
            or ms.get("min_drill_refusals") != 2
            or ms.get("min_boots_seen") != 2):
        errors.append("graphics.diagnostics.status contract drifted")
    ss = sd.get("status", {})
    if (ss.get("owner") != "guest"
            or ss.get("command") != ms.get("command")
            or ss.get("script") != ms.get("script")
            or ss.get("consistency_checks") != expected_checks
            or ss.get("min_drill_refusals") != 2
            or ss.get("min_boots_seen") != 2):
        errors.append("session.json diagnostics.status contract drifted")
    status_heredoc = asm.split("<<'STATUS'\n", 1)[-1].split("\nSTATUS\n", 1)[0]
    if "<<'STATUS'\n" not in asm or "\nSTATUS\n" not in asm:
        errors.append("assemble-native.sh lost its guest STATUS heredoc")
    for token in ("KNIX_STATUS_DRILL_DIR", "/run/knix/*", 's/.* events=',
                  "ROW_SUM=", "consistency_checks=5", "STATUS=degraded",
                  '"consistency": {', "[ \"$STATUS\" = degraded ] && exit 1"):
        if token not in status_heredoc:
            errors.append("assemble-native.sh STATUS heredoc lost token: %s" % token)
    vs_path = ROOT / "platform/image/verify-status.sh"
    if not vs_path.is_file():
        errors.append("platform/image/verify-status.sh is missing")
        vs_text = ""
    else:
        vs_text = vs_path.read_text(encoding="utf-8")
    for token in ("STATUS VERIFY OK", "STATUS CONSISTENCY: 5/5",
                  "STATUS DRILL: 2 refusals", "KNIX_STATUS_DRILL_DIR",
                  "archive_vs_stats", "rows_vs_sum", "sha256sum", "--json"):
        if token not in vs_text:
            errors.append("verify-status.sh lost status token: %s" % token)
    for token in ("STATUS VERIFY OK", "STATUS CONSISTENCY: 5/5",
                  "STATUS DRILL: 2 refusals", "guest-status.log",
                  "STATUS PERSISTENCE VERIFY OK", '"status": {',
                  "STATUS EVENTS:", "STATUS BOOTS:"):
        if token not in build_text:
            errors.append("build.sh lost status gate: %s" % token)
    if build_text.count('sys.exit("STATUS DRIFT') < 2:
        errors.append("build.sh receipt block lost a STATUS DRIFT refusal")
    # The self-test also contains the drift label as an expected error; require
    # the actual refusal statement rather than a bare substring.
    if ("diagnostics_status_verified" not in rg_text
            or 'errors.append("diagnostics status drift: guest health snapshot was not proven")'
            not in rg_text):
        errors.append("release gate lost the status acceptance check")

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

    # 6c. a built image has an independent, read-only acceptance gate. The gate
    # is not a build input: pinning it would change the receipt it judges.
    rel = m.get("release", {})
    if (rel.get("tool") != "scripts/verify-release.py"
            or rel.get("read_only") is not True
            or rel.get("schema") != 3
            or len(rel.get("checks", [])) != 19
            or "diagnostics_persistence_verified" not in rel.get("checks", [])
            or "diagnostics_operations_verified" not in rel.get("checks", [])
            or "diagnostics_trends_verified" not in rel.get("checks", [])
            or "diagnostics_status_verified" not in rel.get("checks", [])
            or "login_policy_verified" not in rel.get("checks", [])
            or rel.get("min_diagnostics_events") != 2
            or rel.get("min_boots_seen") != 2
            or rel.get("min_trend_boots") != 2
            or rel.get("trend_boot_limit") != 8
            or rel.get("trend_byte_limit") != 4096
            or rel.get("min_status_boots") != 2
            or rel.get("status_consistency_checks") != 5
            or rel.get("status_drill_refusals") != 2
            or rel.get("framebuffer_samples") != 9
            or rel.get("publish_surface") != ["knix.img", "origin/main"]):
        errors.append("manifest.release acceptance policy drifted")
    release_gate = ROOT / "scripts" / "verify-release.py"
    if not release_gate.is_file():
        errors.append("release verification gate is missing")
    else:
        if not (release_gate.stat().st_mode & 0o111):
            errors.append("release verification gate is not executable")
        rg = release_gate.read_text(encoding="utf-8")
        try:
            compile(rg, str(release_gate), "exec")
        except SyntaxError as exc:
            errors.append("verify-release.py does not parse: %s" % exc)
        for token in ("--self-test", "RELEASE VERIFY OK", "RELEASE FAILED",
                      "receipt checksum drift", "image sha256 drift",
                      "baseline sha256 drift", "inputs drift",
                      "publish_surface drift", "canonical_sha",
                      "diagnostics persistence drift",
                      "diagnostics operations drift",
                      "diagnostics_operations_verified"):
            if token not in rg:
                errors.append("verify-release.py lost %s" % token)
    for group, entries in m.get("inputs", {}).items():
        if isinstance(entries, dict) and any(
                "verify-release" in str(k) for k in entries):
            errors.append("release gate became a build input in %s" % group)

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

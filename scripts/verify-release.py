#!/usr/bin/env python3
"""Verify a built knix release. Read-only on the host, no QEMU, no guest.

`build.sh --build` writes the receipt when its own cold-boot verification has
passed. This script is the independent acceptance gate for someone who already
holds `knix.img`, `BUILD-RECEIPT.json` and the receipt checksum: it proves the
bytes on disk are the bytes that were verified, that the receipt is intact, and
that every pinned input, the locked baseline and the whole guest-owned evidence
chain still match `platform/image/manifest.json`.

It is deliberately NOT a build input: adding it to `manifest.json.inputs`
would change the receipt it is meant to judge. It provides no product
capability, starts no VM and writes nothing outside its own --self-test
temporary directory.

Usage:
    python3 scripts/verify-release.py [--image PATH] [--receipt PATH]
                                      [--checksum PATH] [--json]
    python3 scripts/verify-release.py --self-test
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "platform" / "image" / "manifest.json"
IDENTITY = ROOT / "platform" / "identity" / "identity.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX128 = re.compile(r"^[0-9a-f]{128}$")
OPENSSL_DGST = re.compile(r"^SHA256\(.*\)= ([0-9a-f]{64})\s*$")

# The machine-readable policy in manifest.json.release must name exactly these
# checks. Keeping the list here too makes a policy drift a refusal instead of an
# unimplemented promise.
CHECKS = (
    "image_sha256_and_bytes_match_receipt",
    "receipt_checksum_matches",
    "inputs_match_manifest",
    "seed_sha512_matches_manifest",
    "baseline_matches_manifest",
    "kernel_and_rootfs_summaries_match_baseline",
    "boot_is_uefi_self_boot",
    "graphics_verified",
    "session_autostart_verified",
    "session_resilience_verified",
    "session_crash_loop_verified",
    "session_diagnostics_verified",
    "diagnostics_persistence_verified",
    "diagnostics_operations_verified",
    "framebuffer_pixels_verified",
    "single_publish_surface",
)


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(image: pathlib.Path, receipt: pathlib.Path, checksum: pathlib.Path,
          m: dict, identity: dict) -> list[str]:
    """Return a list of refusal reasons; an empty list means RELEASE VERIFY OK."""
    errors: list[str] = []
    policy = m.get("release", {})
    if not isinstance(policy, dict) or not policy:
        return ["manifest lost the release policy"]
    if policy.get("read_only") is not True:
        errors.append("release policy is no longer read-only")
    if sorted(policy.get("checks", [])) != sorted(CHECKS):
        errors.append("release policy checks drifted from the implementation")

    if not receipt.is_file():
        return errors + ["missing receipt: %s" % receipt]
    try:
        r = load_json(receipt)
    except (OSError, ValueError) as exc:
        return errors + ["unreadable receipt: %s" % exc]
    if not isinstance(r, dict):
        return errors + ["receipt is not a JSON object"]

    # 1. the receipt file itself is the one covered by sign.sh
    if not checksum.is_file():
        errors.append("missing receipt checksum: %s" % checksum.name)
    else:
        try:
            lines = checksum.read_text(encoding="utf-8").strip().splitlines()
            match = OPENSSL_DGST.match(lines[0]) if lines else None
            if not match or len(lines) != 1:
                errors.append("receipt checksum is not an openssl dgst line")
            elif match.group(1) != sha256_file(receipt):
                errors.append("receipt checksum drift: the receipt was edited")
        except OSError as exc:
            errors.append("unreadable receipt checksum: %s" % exc)

    # 2. top-level identity and artifact contract
    if r.get("schema") != policy.get("schema"):
        errors.append("receipt schema drift")
    if r.get("product") != "knix" or r.get("artifact") != policy.get("artifact"):
        errors.append("receipt artifact drift")
    if r.get("format") != policy.get("format"):
        errors.append("receipt format drift")
    if r.get("version") != identity.get("version") or r.get("stage") != identity.get("stage"):
        errors.append("receipt identity drift")

    # 3. the bytes on disk are the bytes that build.sh verified
    if not image.is_file():
        errors.append("missing release image: %s" % image.name)
    else:
        try:
            size = image.stat().st_size
            if size != r.get("bytes"):
                errors.append("image size drift")
            digest = sha256_file(image)
            if digest != r.get("sha256"):
                errors.append("image sha256 drift")
        except OSError as exc:
            errors.append("unreadable release image: %s" % exc)

    # 4. every pinned build input in the receipt still equals the in-repo truth
    declared = {k: v for k, v in m.get("inputs", {}).items() if k != "seed"}
    if r.get("inputs") != declared:
        errors.append("inputs drift: receipt inputs do not match manifest.json")
    seed = m.get("inputs", {}).get("seed", {})
    if not HEX128.match(str(seed.get("sha512", ""))) or r.get("seed_sha512") != seed.get("sha512"):
        errors.append("seed_sha512 drift")

    # 5. the locked baseline is the one committed in the repository
    baseline = m.get("build_baseline", {})
    rb = r.get("baseline", {})
    if not isinstance(rb, dict):
        rb = {}
        errors.append("receipt lost the baseline section")
    if rb.get("sha256") != canonical_sha(baseline):
        errors.append("baseline sha256 drift")
    if rb.get("kernel") != baseline.get("kernel") or rb.get("rootfs") != baseline.get("rootfs"):
        errors.append("baseline kernel/rootfs drift")
    if rb.get("verified") is not True:
        errors.append("baseline not verified in receipt")
    if rb.get("package_lock") != "platform/image/package-versions.txt":
        errors.append("baseline package_lock drift")

    # 6. the full kernel-build.json / rootfs-build.json carry the locked fields
    for summary, locked in (("kernel", baseline.get("kernel", {})),
                            ("rootfs", baseline.get("rootfs", {}))):
        part = r.get(summary, {})
        if not isinstance(part, dict):
            errors.append("%s summary drift" % summary)
            continue
        for key, expected in locked.items():
            if part.get(key) != expected:
                errors.append("%s summary drift: %s" % (summary, key))
                break

    # 7. the image boots from its own ESP, not from a host kernel or seed
    boot = r.get("boot", {})
    mb = m.get("boot", {})
    if (boot.get("method") != mb.get("method")
            or boot.get("entry") != mb.get("entry")
            or boot.get("append") != mb.get("append")
            or boot.get("seed_iso") is not False
            or "BOOTAA64.EFI" not in str(boot.get("esp", ""))):
        errors.append("boot drift: the UEFI self-boot chain changed")

    # 8. the whole guest-owned evidence chain is present and verified
    gfx = r.get("graphics", {})
    mg = m.get("graphics", {})
    if (gfx.get("owner") != "guest" or gfx.get("device") != mg.get("device")
            or gfx.get("verified") is not True):
        errors.append("graphics not verified in receipt")
    session = gfx.get("session", {})
    # manifest names the session user `session_user`; the receipt calls it `user`.
    expected_session = {
        "user": mg.get("session_user"),
        "seat_manager": mg.get("seat_manager"),
        "unit": mg.get("unit"),
        "backends": mg.get("backends"),
        "input_devices": mg.get("input_devices"),
    }
    for key, expected in expected_session.items():
        if session.get(key) != expected:
            errors.append("session contract drift: %s" % key)
    if session.get("verified") is not True:
        errors.append("session not verified in receipt")
    if session.get("autostart", {}).get("verified") is not True:
        errors.append("session autostart not verified")
    if session.get("resilience", {}).get("verified") is not True:
        errors.append("session resilience not verified")
    if session.get("crash_loop", {}).get("verified") is not True:
        errors.append("session crash loop not verified")
    diag = session.get("diagnostics", {})
    minimum = policy.get("min_diagnostics_events", mg.get("diagnostics", {}).get(
        "min_reported_events", 2))
    if (diag.get("verified") is not True
            or not isinstance(diag.get("events"), int)
            or diag["events"] < minimum):
        errors.append("diagnostics events drift: guest crash timeline is missing")
    if diag.get("owner") != "guest" or diag.get("manager") != "systemd":
        errors.append("diagnostics ownership drift")
    mp = mg.get("diagnostics", {}).get("persistence", {})
    persist = diag.get("persistence", {})
    if (persist.get("owner") != "guest"
            or persist.get("manager") != "systemd-journald"
            or persist.get("survives_reboot") is not True
            or persist.get("verified") is not True
            or persist.get("log") != mp.get("log")
            or persist.get("snapshot") != mp.get("snapshot")
            or persist.get("journal") != mp.get("journal")
            or not isinstance(persist.get("events"), int)
            or persist["events"] < minimum):
        errors.append("diagnostics persistence drift: the crash archive did not survive a reboot")
    # P15: the archive must also be operable -- a bounded rotation drill, an
    # err-priority guest alert and statistics that advance across two boots.
    mo = mg.get("diagnostics", {}).get("operations", {})
    ops = diag.get("operations", {})
    min_boots = policy.get("min_boots_seen", mo.get("min_boots_seen", 2))
    if (ops.get("owner") != "guest"
            or ops.get("verified") is not True
            or ops.get("rotation_drill_verified") is not True
            or ops.get("cross_boot_verified") is not True
            or ops.get("statistics") != mo.get("statistics")
            or ops.get("alert") != mo.get("alert")
            or ops.get("archive_limit_bytes") != mo.get("archive_limit_bytes")
            or ops.get("generations") != mo.get("generations")
            or ops.get("journal_tag") != mo.get("journal_tag")
            or ops.get("journal_priority") != mo.get("journal_priority")
            or not isinstance(ops.get("boots_seen"), int)
            or ops["boots_seen"] < min_boots
            or not isinstance(ops.get("total_events"), int)
            or ops["total_events"] < minimum):
        errors.append("diagnostics operations drift: guest rotation, cross-boot stats or local alert was not proven")

    fb = gfx.get("framebuffer", {})
    mfb = mg.get("framebuffer", {})
    samples = policy.get("framebuffer_samples", mfb.get("samples", 9))
    resolution = mfb.get("resolution", [1280, 800])
    if (fb.get("owner") != "guest" or fb.get("verified") is not True
            or [fb.get("width"), fb.get("height")] != resolution
            or fb.get("samples") != samples
            or fb.get("matching_samples") != samples
            or not isinstance(fb.get("bar_pixels"), int)
            or fb["bar_pixels"] < policy.get("min_framebuffer_bar_pixels", 100)
            or not HEX64.match(str(fb.get("pattern_sha256", "")))):
        errors.append("framebuffer pixel proof drift")

    # 9. nothing else is published
    if (r.get("publish_surface") != policy.get("publish_surface")
            or m.get("publish_surface") != policy.get("publish_surface")
            or m.get("artifact", {}).get("single") is not True):
        errors.append("publish_surface drift")

    return errors


def default_dev_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("KNIX_DEV_DIR", str(ROOT.parent / "knix-m0")))


def synthetic_receipt(m: dict, identity: dict, image_bytes: bytes) -> dict:
    """Build a policy-consistent receipt without any real build artifact.

    Deep-copy the in-repo truth: a self-test mutator must never be able to edit
    the live manifest object shared with other cases or with check().
    """
    m = copy.deepcopy(m)
    identity = copy.deepcopy(identity)
    baseline = m["build_baseline"]
    mg = m["graphics"]
    mfb = mg["framebuffer"]
    samples = m["release"]["framebuffer_samples"]
    kernel = dict(baseline["kernel"])
    kernel.update({"schema": 1, "source": "repos/linux",
                   "config_fragment": "arch/arm64/configs/virt.config",
                   "config_note": "synthetic", "modules_dir": "modules/lib/modules/7.2.15",
                   "boot_drivers": ["CONFIG_VIRTIO_BLK"]})
    rootfs = dict(baseline["rootfs"])
    rootfs.update({"schema": 2, "generator": "mmdebstrap", "suite": "trixie",
                   "variant": "minbase", "arch": "arm64",
                   "artifact": "knix-rootfs.tar.zst",
                   "package_lock": "package-versions.txt"})
    return {
        "schema": m["release"]["schema"],
        "product": "knix",
        "version": identity["version"],
        "stage": identity["stage"],
        "artifact": m["artifact"]["name"],
        "format": m["artifact"]["format"],
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "bytes": len(image_bytes),
        "built_at": "2026-11-12T00:00:00+00:00",
        "kernel": kernel,
        "rootfs": rootfs,
        "inputs": {k: v for k, v in m["inputs"].items() if k != "seed"},
        "seed_sha512": m["inputs"]["seed"]["sha512"],
        "boot": {"entry": m["boot"]["entry"], "method": m["boot"]["method"],
                 "esp": "EFI/BOOT/BOOTAA64.EFI (removable media path)",
                 "initrd": "EFI/knix/initrd.img-7.2.15-knix",
                 "append": m["boot"]["append"], "seed_iso": False},
        "graphics": {
            "device": mg["device"], "input": mg["input"],
            "compositor": mg["compositor"], "owner": mg["owner"],
            "verify": mg["verify"], "verified": True,
            "framebuffer": {
                "owner": mfb["owner"], "capture": mfb["capture"],
                "verify": mfb["verify"],
                "width": mfb["resolution"][0], "height": mfb["resolution"][1],
                "pattern_sha256": "0" * 64, "samples": samples,
                "matching_samples": samples, "bar_pixels": 1920,
                "verified": True,
            },
            "session": {
                "user": mg["session_user"], "seat_manager": mg["seat_manager"],
                "unit": mg["unit"], "backends": mg["backends"],
                "input_devices": mg["input_devices"], "verified": True,
                "autostart": {"enabled": True, "target": mg["autostart_target"],
                              "condition": mg["autostart_condition"], "verified": True},
                "resilience": {"owner": "guest", "manager": "systemd",
                               "restart": "on-failure", "restart_sec": 2,
                               "start_limit_burst": 5,
                               "start_limit_interval_sec": 30, "verified": True},
                "crash_loop": {"owner": "guest", "manager": "systemd",
                               "burst": 5, "interval_sec": 30,
                               "degraded_failed_units": 1, "verified": True},
                "diagnostics": {"owner": "guest", "manager": "systemd",
                                "unit": mg["diagnostics"]["unit"],
                                "report": mg["diagnostics"]["report"],
                                "history": mg["diagnostics"]["history"],
                                "events": m["release"]["min_diagnostics_events"],
                                "verified": True,
                                "persistence": {
                                    "owner": "guest",
                                    "manager": "systemd-journald",
                                    "log": mg["diagnostics"]["persistence"]["log"],
                                    "snapshot": mg["diagnostics"]["persistence"]["snapshot"],
                                    "journal": mg["diagnostics"]["persistence"]["journal"],
                                    "events": m["release"]["min_diagnostics_events"],
                                    "survives_reboot": True,
                                    "verified": True,
                                },
                                "operations": {
                                    "owner": "guest",
                                    "statistics": mg["diagnostics"]["operations"]["statistics"],
                                    "alert": mg["diagnostics"]["operations"]["alert"],
                                    "archive_limit_bytes": mg["diagnostics"]["operations"]["archive_limit_bytes"],
                                    "generations": mg["diagnostics"]["operations"]["generations"],
                                    "journal_tag": mg["diagnostics"]["operations"]["journal_tag"],
                                    "journal_priority": mg["diagnostics"]["operations"]["journal_priority"],
                                    "rotation_drill_verified": True,
                                    "cross_boot_verified": True,
                                    "total_events": m["release"]["min_diagnostics_events"] + 1,
                                    "boots_seen": m["release"]["min_boots_seen"],
                                    "verified": True,
                                }},
            },
        },
        "baseline": {"sha256": canonical_sha(baseline),
                     "kernel": baseline["kernel"], "rootfs": baseline["rootfs"],
                     "package_lock": "platform/image/package-versions.txt",
                     "verified": True},
        "publish_surface": m["publish_surface"],
    }


def self_test(m: dict, identity: dict) -> int:
    """Every refusal case runs in its own temporary directory, no VM needed."""
    img_bytes = b"knix-p13-synthetic-image" * 32
    cases = []

    def case(name, mutate, expect, *, image_edit=None, checksum_edit=None):
        cases.append((name, mutate, expect, image_edit, checksum_edit))

    def nested(*path):
        def make(value):
            def mutate(r, _img, _chk):
                node = r
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
            return mutate
        return make

    case("control", None, None)
    case("image bytes appended", None, "image size drift", image_edit="append")
    case("image sha256 mismatch", lambda r, i, c: r.update({"sha256": "0" * 64}),
         "image sha256 drift")
    case("image size mismatch", lambda r, i, c: r.update({"bytes": 1}), "image size drift")
    case("missing checksum", None, "missing receipt checksum", checksum_edit="missing")
    case("checksum mismatch", None, "receipt checksum drift", checksum_edit="wrong")
    case("checksum not openssl dgst", None, "not an openssl dgst line",
         checksum_edit="malformed")
    case("receipt schema", lambda r, i, c: r.update({"schema": 2}),
         "receipt schema drift")
    case("receipt artifact", lambda r, i, c: r.update({"artifact": "other.img"}),
         "receipt artifact drift")
    case("receipt format", lambda r, i, c: r.update({"format": "raw"}),
         "receipt format drift")
    case("identity version", lambda r, i, c: r.update({"version": "9.9"}),
         "receipt identity drift")
    case("inputs group removed",
         lambda r, i, c: r["inputs"].pop("identity_sha256", None), "inputs drift")
    case("single input digest",
         lambda r, i, c: r["inputs"]["brand_sha256"].update(
             {next(iter(r["inputs"]["brand_sha256"])): "0" * 64}), "inputs drift")
    case("seed digest", lambda r, i, c: r.update({"seed_sha512": "0" * 128}),
         "seed_sha512 drift")
    case("baseline digest", nested("baseline", "sha256")("1" * 64),
         "baseline sha256 drift")
    case("baseline kernel field", nested("baseline", "kernel", "module_count")(1),
         "baseline kernel/rootfs drift")
    case("baseline not verified", nested("baseline", "verified")(False),
         "baseline not verified")
    case("kernel summary drift", nested("kernel", "config_sha256")("2" * 64),
         "kernel summary drift")
    case("rootfs summary drift", nested("rootfs", "tarball_sha256")("3" * 64),
         "rootfs summary drift")
    case("boot method", nested("boot", "method")("-kernel direct"), "boot drift")
    case("seed ISO attached", nested("boot", "seed_iso")(True), "boot drift")
    case("graphics not verified", nested("graphics", "verified")(False),
         "graphics not verified")
    case("session user", nested("graphics", "session", "user")("root"),
         "session contract drift")
    case("autostart not verified",
         nested("graphics", "session", "autostart", "verified")(False),
         "session autostart not verified")
    case("resilience not verified",
         nested("graphics", "session", "resilience", "verified")(False),
         "session resilience not verified")
    case("crash loop not verified",
         nested("graphics", "session", "crash_loop", "verified")(False),
         "session crash loop not verified")
    case("diagnostics events too few",
         nested("graphics", "session", "diagnostics", "events")(1),
         "diagnostics events drift")
    case("diagnostics host-owned",
         nested("graphics", "session", "diagnostics", "owner")("host"),
         "diagnostics ownership drift")
    case("persistent archive missing",
         lambda r, i, c: r["graphics"]["session"]["diagnostics"].pop(
             "persistence", None), "diagnostics persistence drift")
    case("persistent archive host-owned",
         nested("graphics", "session", "diagnostics", "persistence", "owner")("host"),
         "diagnostics persistence drift")
    case("persistent archive not reboot-proof",
         nested("graphics", "session", "diagnostics", "persistence",
                "survives_reboot")(False), "diagnostics persistence drift")
    case("persistent archive events too few",
         nested("graphics", "session", "diagnostics", "persistence", "events")(1),
         "diagnostics persistence drift")
    case("diagnostics operations missing",
         lambda r, i, c: r["graphics"]["session"]["diagnostics"].pop(
             "operations", None), "diagnostics operations drift")
    case("operations host-owned",
         nested("graphics", "session", "diagnostics", "operations", "owner")("host"),
         "diagnostics operations drift")
    case("rotation drill unverified",
         nested("graphics", "session", "diagnostics", "operations",
                "rotation_drill_verified")(False), "diagnostics operations drift")
    case("cross-boot proof missing",
         nested("graphics", "session", "diagnostics", "operations",
                "cross_boot_verified")(False), "diagnostics operations drift")
    case("operations saw only one boot",
         nested("graphics", "session", "diagnostics", "operations",
                "boots_seen")(1), "diagnostics operations drift")
    case("operations alert path changed",
         nested("graphics", "session", "diagnostics", "operations",
                "alert")("/tmp/alert"), "diagnostics operations drift")
    case("framebuffer samples",
         nested("graphics", "framebuffer", "matching_samples")(8),
         "framebuffer pixel proof drift")
    case("framebuffer no swaybar",
         nested("graphics", "framebuffer", "bar_pixels")(0),
         "framebuffer pixel proof drift")
    case("second publish surface",
         lambda r, i, c: r["publish_surface"].append("knix-extra.img"),
         "publish_surface drift")

    failures = []
    with tempfile.TemporaryDirectory(prefix="knix-release-") as tmp:
        base = pathlib.Path(tmp)
        for idx, (name, mutate, expect, image_edit, checksum_edit) in enumerate(cases):
            d = base / ("case-%02d" % idx)
            d.mkdir()
            img = d / "knix.img"
            rcpt = d / "BUILD-RECEIPT.json"
            chk = d / "BUILD-RECEIPT.json.sha256"
            img.write_bytes(img_bytes)
            r = synthetic_receipt(m, identity, img_bytes)
            if mutate is not None:
                mutate(r, img, chk)
            payload = (json.dumps(r, indent=2, sort_keys=True) + "\n").encode()
            rcpt.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            if checksum_edit == "missing":
                chk.unlink(missing_ok=True)
            elif checksum_edit == "wrong":
                chk.write_text("SHA256(%s)= %s\n" % (rcpt, "0" * 64))
            elif checksum_edit == "malformed":
                chk.write_text("%s  BUILD-RECEIPT.json\n" % digest)
            else:
                chk.write_text("SHA256(%s)= %s\n" % (rcpt, digest))
            if image_edit == "append":
                with img.open("ab") as fh:
                    fh.write(b"tampered")
            errors = check(img, rcpt, chk, m, identity)
            if expect is None:
                if errors:
                    failures.append("control unexpectedly refused: %s" % errors[:3])
            elif not any(expect in e for e in errors):
                failures.append("%s was not refused (expected %r, got %r)"
                                % (name, expect, errors[:3]))

    if failures:
        for f in failures:
            print("RELEASE SELF-TEST FAILED: %s" % f, file=sys.stderr)
        return 1
    print("RELEASE SELF-TEST OK: %d refusal cases, 1 control, no build artifacts"
          % (len(cases) - 1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", type=pathlib.Path)
    ap.add_argument("--receipt", type=pathlib.Path)
    ap.add_argument("--checksum", type=pathlib.Path)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        m = load_json(MANIFEST)
        identity = load_json(IDENTITY)
    except (OSError, ValueError) as exc:
        print("RELEASE FAILED: unreadable in-repo truth: %s" % exc, file=sys.stderr)
        return 1
    if args.self_test:
        return self_test(m, identity)

    dev = default_dev_dir()
    image = args.image or dev / "knix.img"
    receipt = args.receipt or dev / "BUILD-RECEIPT.json"
    checksum = args.checksum or pathlib.Path(str(receipt) + ".sha256")
    errors = check(image, receipt, checksum, m, identity)
    if args.json:
        print(json.dumps({"ok": not errors, "image": str(image),
                          "receipt": str(receipt), "errors": errors}, indent=2))
    if errors:
        for e in errors:
            print("RELEASE FAILED: %s" % e, file=sys.stderr)
        print("\n%d release check(s) failed." % len(errors), file=sys.stderr)
        return 1
    try:
        r = load_json(receipt)
        print("RELEASE VERIFY OK: %s %s / %s bytes, receipt checksum %s, "
              "%d pinned inputs, baseline %s, %d evidence checks"
              % (r["artifact"], r["sha256"][:8], r["bytes"],
                 sha256_file(receipt)[:8],
                 sum(len(v) for v in r["inputs"].values() if isinstance(v, dict)),
                 r["baseline"]["sha256"][:8], len(CHECKS)))
    except (OSError, ValueError, KeyError) as exc:
        print("RELEASE FAILED: receipt summary unavailable: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

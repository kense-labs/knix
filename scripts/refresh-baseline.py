#!/usr/bin/env python3
"""Refresh the locked rootfs baseline without silently washing away drift.

The guest-produced summaries (kernel-build.json, rootfs-build.json and
package-versions.txt) live outside the repository. build.sh --build refuses to
assemble when they differ from manifest.json's build_baseline. Historically
refreshing that lock meant copying four hashes by hand; this tool makes the
refresh explicit and reviewable instead:

    python3 scripts/refresh-baseline.py --check
    python3 scripts/refresh-baseline.py --propose
    python3 scripts/refresh-baseline.py --apply --reason "Debian mirror updated"

Scope boundary: only the rootfs baseline and the *_sha256 input groups can be
refreshed. A kernel summary drift is never refreshed here -- it must go back
through build-kernel.sh --apply, because a new kernel changes the image itself.
An --apply requires an unexpired proposal whose field-level changes still match
the current guest summaries, plus a non-trivial reason. --self-test exercises
that whole policy with no guest artifacts, so CI can run it.
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

KERNEL_FIELDS = (
    "kernel_version", "config_sha256", "image_sha256", "image_gz_sha256",
    "module_count", "graphics_drivers", "graphics_modules",
)
ROOTFS_FIELDS = (
    "tarball_sha256", "tarball_bytes", "package_set_sha256", "package_count",
    "package_versions_sha256", "package_versions_count",
)
INPUT_GROUPS = (
    "identity_sha256", "brand_sha256", "recipe_sha256",
    "verification_sha256", "baseline_sha256",
)
LOCK_REL = "platform/image/package-versions.txt"
PROPOSAL_NAME = "BASELINE-PROPOSAL.json"
MIN_REASON = 8


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(path.read_bytes()) if path.is_file() else "missing"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Context(object):
    def __init__(self, root, dev, manifest_path):
        self.root = pathlib.Path(root)
        self.dev = pathlib.Path(dev)
        self.manifest_path = pathlib.Path(manifest_path)
        self.proposal_path = self.dev / PROPOSAL_NAME
        self.manifest_bytes = self.manifest_path.read_bytes()
        self.manifest = json.loads(self.manifest_bytes.decode("utf-8"))
        self.kernel = read_json(self.dev / "kernel-build.json")
        self.rootfs = read_json(self.dev / "rootfs-build.json")
        self.guest_lock = (self.dev / "package-versions.txt").read_bytes()
        self.repo_lock_path = self.root / LOCK_REL
        self.repo_lock = (self.repo_lock_path.read_bytes()
                          if self.repo_lock_path.is_file() else b"")

    def sync_lock(self):
        return self.repo_lock != self.guest_lock

    def input_hash(self, rel):
        # A pending lockfile sync is part of the proposed state: predict the
        # digest the repository will have after --apply copies the guest file.
        if self.sync_lock() and rel == LOCK_REL:
            return sha256_bytes(self.guest_lock)
        path = self.root / rel
        return sha256_bytes(path.read_bytes()) if path.is_file() else "missing"

    def fingerprints(self):
        return {
            "kernel-build.json": sha256_file(self.dev / "kernel-build.json"),
            "rootfs-build.json": sha256_file(self.dev / "rootfs-build.json"),
            "guest-lockfile": sha256_bytes(self.guest_lock),
            "manifest.json": sha256_bytes(self.manifest_path.read_bytes()),
        }


def drift(ctx):
    base = ctx.manifest.get("build_baseline", {})
    bk, br = base.get("kernel", {}), base.get("rootfs", {})
    kernel = [(f, bk.get(f), ctx.kernel.get(f)) for f in KERNEL_FIELDS
              if ctx.kernel.get(f) != bk.get(f)]
    rootfs = [(f, br.get(f), ctx.rootfs.get(f)) for f in ROOTFS_FIELDS
              if ctx.rootfs.get(f) != br.get(f)]
    inputs = []
    for group in INPUT_GROUPS:
        for rel, locked in ctx.manifest.get("inputs", {}).get(group, {}).items():
            actual = ctx.input_hash(rel)
            if actual != locked:
                inputs.append((group, rel, locked, actual))
    lock = None
    if ctx.sync_lock():
        lock = {
            "from_sha256": sha256_bytes(ctx.repo_lock),
            "to_sha256": sha256_bytes(ctx.guest_lock),
            "bytes": len(ctx.guest_lock),
        }
    return {
        "kernel": kernel, "rootfs": rootfs, "inputs": inputs,
        "lockfile": lock,
        "lockfile_consistent":
            sha256_bytes(ctx.guest_lock) == ctx.rootfs.get("package_versions_sha256"),
    }


def drift_lines(d):
    lines = ["BASELINE DRIFT:"]
    for f, locked, guest in d["kernel"]:
        lines.append("  kernel.%s: guest=%r locked=%r (rebuild with build-kernel.sh)" %
                     (f, guest, locked))
    for f, locked, guest in d["rootfs"]:
        lines.append("  rootfs.%s: guest=%r locked=%r" % (f, guest, locked))
    for group, rel, locked, actual in d["inputs"]:
        lines.append("  inputs.%s[%s]: actual=%s locked=%s" %
                     (group, rel, actual, locked))
    if d["lockfile"]:
        lines.append("  package-versions.txt: %s -> %s (%d bytes)" %
                     (d["lockfile"]["from_sha256"],
                      d["lockfile"]["to_sha256"], d["lockfile"]["bytes"]))
    if not d["lockfile_consistent"]:
        lines.append("  guest lockfile does not match its own rootfs summary")
    lines.append("  Run: python3 scripts/refresh-baseline.py --propose")
    return lines


def has_drift(d):
    return bool(d["kernel"] or d["rootfs"] or d["inputs"] or d["lockfile"]
                or not d["lockfile_consistent"])


def do_check(args):
    ctx = Context(args.root, args.dev, args.manifest)
    d = drift(ctx)
    if has_drift(d):
        return 1, drift_lines(d)
    b = ctx.manifest["build_baseline"]
    n = sum(len(ctx.manifest["inputs"].get(g, {})) for g in INPUT_GROUPS)
    return 0, ["BASELINE CURRENT: kernel %s/%s modules, rootfs %s package "
               "versions, %d pinned inputs" %
               (b["kernel"]["kernel_version"], b["kernel"]["module_count"],
                b["rootfs"]["package_versions_count"], n)]


def proposal_payload(ctx, d):
    return {
        "schema": 1,
        "tool": "scripts/refresh-baseline.py",
        "generated_at": utcnow(),
        "reason": None,
        "applied_at": None,
        "policy": {
            "apply_requires_reason_chars": MIN_REASON,
            "fingerprints_must_match": True,
            "field_changes_must_match_current_summaries": True,
            "kernel_drift_is_never_refreshed": True,
            "scope": "build_baseline.rootfs and *_sha256 input groups only",
        },
        "fingerprints": ctx.fingerprints(),
        "rootfs_changes": [{"field": f, "from": locked, "to": guest}
                           for f, locked, guest in d["rootfs"]],
        "input_changes": [{"group": g, "path": rel, "from": locked,
                           "to": actual}
                          for g, rel, locked, actual in d["inputs"]],
        "lockfile_change": d["lockfile"],
    }


def do_propose(args):
    ctx = Context(args.root, args.dev, args.manifest)
    d = drift(ctx)
    if d["kernel"]:
        return 1, ["REFUSED: kernel summary drifted; rebuild with "
                   "build-kernel.sh --apply and refresh the kernel lock first"]
    if not d["lockfile_consistent"]:
        return 1, ["REFUSED: guest package-versions.txt contradicts its own "
                   "rootfs-build.json; rerun build-rootfs.sh --apply"]
    if not has_drift(d):
        return 0, ["NO DRIFT: baseline is current; no proposal written"]
    payload = proposal_payload(ctx, d)
    ctx.proposal_path.write_text(json.dumps(payload, indent=2) + "\n",
                                 encoding="utf-8")
    return 0, ["PROPOSAL WRITTEN: %s (%d rootfs fields, %d inputs, lockfile=%s)"
               % (ctx.proposal_path, len(payload["rootfs_changes"]),
                  len(payload["input_changes"]),
                  "yes" if payload["lockfile_change"] else "no"),
               "Review the field-level diff, then run --apply --reason \"...\""]


def set_scalar(text, key, value):
    if isinstance(value, str):
        pattern = re.compile(r'("%s"\s*:\s*)"[^"]*"' % re.escape(key))
        rendered = json.dumps(value)
    else:
        pattern = re.compile(r'("%s"\s*:\s*)(\d+)' % re.escape(key))
        rendered = str(value)
    new_text, count = pattern.subn(lambda m: m.group(1) + rendered, text,
                                   count=1)
    if count != 1:
        raise ValueError("expected one JSON scalar for %r" % key)
    return new_text


def do_apply(args):
    ctx = Context(args.root, args.dev, args.manifest)
    if not ctx.proposal_path.is_file():
        return 1, ["REFUSED: no %s; run --propose first" % PROPOSAL_NAME]
    p = read_json(ctx.proposal_path)
    if p.get("schema") != 1 or p.get("tool") != "scripts/refresh-baseline.py":
        return 1, ["REFUSED: unrecognized proposal"]
    if p.get("applied_at"):
        return 1, ["REFUSED: proposal was already applied at %s" % p["applied_at"]]
    reason = (args.reason or "").strip()
    if len(reason) < MIN_REASON:
        return 1, ["REFUSED: --reason must be at least %d characters and state "
                   "why the rootfs changed" % MIN_REASON]
    if p.get("fingerprints") != ctx.fingerprints():
        return 1, ["REFUSED: stale proposal; guest summaries, lockfile or "
                   "manifest changed since --propose"]
    d = drift(ctx)
    if d["kernel"]:
        return 1, ["REFUSED: kernel summary drifted; this tool never refreshes "
                   "the kernel lock"]
    if not d["lockfile_consistent"]:
        return 1, ["REFUSED: guest lockfile contradicts its rootfs summary"]
    expected_changes = proposal_payload(ctx, d)
    for key in ("rootfs_changes", "input_changes", "lockfile_change"):
        if p.get(key) != expected_changes.get(key):
            return 1, ["REFUSED: proposal %s no longer matches the current "
                       "guest summaries" % key]

    text = ctx.manifest_path.read_text(encoding="utf-8")
    for change in p["rootfs_changes"]:
        text = set_scalar(text, change["field"], change["to"])
    for change in p["input_changes"]:
        text = set_scalar(text, change["path"], change["to"])
    json.loads(text)  # never write an unparsable manifest
    if p["lockfile_change"]:
        ctx.repo_lock_path.write_bytes(ctx.guest_lock)
    ctx.manifest_path.write_text(text, encoding="utf-8")
    p["reason"] = reason
    p["applied_at"] = utcnow()
    ctx.proposal_path.write_text(json.dumps(p, indent=2) + "\n",
                                 encoding="utf-8")
    return 0, ["BASELINE REFRESHED: %d rootfs fields, %d inputs, lockfile=%s" %
               (len(p["rootfs_changes"]), len(p["input_changes"]),
                "synced" if p["lockfile_change"] else "unchanged"),
               "reason: %s" % reason,
               "Next: python3 scripts/refresh-baseline.py --check && "
               "build.sh --build"]


def self_test_fixture(base):
    # Every case gets a fresh directory: some python shims mishandle
    # mkdir(exist_ok=True) on an existing path.
    root = base / "repo"
    dev = base / "dev"
    (root / "platform/image").mkdir(parents=True)
    (root / "platform/identity").mkdir(parents=True)
    dev.mkdir(parents=True)
    session = root / "platform/identity/session.json"
    build = root / "platform/image/build.sh"
    lock = root / LOCK_REL
    session.write_text('{"schema": 1, "user": "knix"}\n', encoding="utf-8")
    build.write_text('#!/bin/bash\necho build\n', encoding="utf-8")
    lock.write_text("base 1.0 arm64\n", encoding="utf-8")
    kernel = {
        "kernel_version": "7.2.15", "config_sha256": "a" * 64,
        "image_sha256": "b" * 64, "image_gz_sha256": "c" * 64,
        "module_count": 773,
        "graphics_drivers": ["CONFIG_DRM_VIRTIO_GPU=m"],
        "graphics_modules": ["virtio-gpu"],
    }
    rootfs = {
        "tarball_sha256": "d" * 64, "tarball_bytes": 100,
        "package_set_sha256": "e" * 64, "package_count": 39,
        "package_versions_sha256": sha256_bytes(lock.read_bytes()),
        "package_versions_count": 1,
    }
    (dev / "kernel-build.json").write_text(json.dumps(kernel, indent=2) + "\n")
    (dev / "rootfs-build.json").write_text(json.dumps(rootfs, indent=2) + "\n")
    (dev / "package-versions.txt").write_bytes(lock.read_bytes())
    manifest = {
        "schema": 3,
        "inputs": {
            "identity_sha256": {"platform/identity/session.json": sha256_file(session)},
            "recipe_sha256": {"platform/image/build.sh": sha256_file(build)},
            "baseline_sha256": {LOCK_REL: sha256_file(lock)},
        },
        "build_baseline": {"kernel": kernel, "rootfs": rootfs},
    }
    mani = root / "platform/image/manifest.json"
    mani.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root, dev, mani


def do_self_test(args):
    cases = []
    with tempfile.TemporaryDirectory(prefix="knix-baseline-policy-") as td:
        tmp = pathlib.Path(td)
        counter = [0]
        root = dev = mani = None

        def fixture():
            nonlocal root, dev, mani
            counter[0] += 1
            root, dev, mani = self_test_fixture(
                tmp / ("case-%d" % counter[0]))

        fixture()

        def run(*extra):
            argv = ["--root", str(root), "--dev", str(dev),
                    "--manifest", str(mani)] + list(extra)
            parsed = build_parser().parse_args(argv)
            return execute(parsed)[0]

        def expect(name, rc, wanted):
            cases.append((name, rc, wanted))
            if rc != wanted:
                raise AssertionError("%s: expected rc=%d, got %d" %
                                     (name, wanted, rc))

        def mutate_summary(key, value, kernel=False):
            path = dev / ("kernel-build.json" if kernel else "rootfs-build.json")
            obj = read_json(path)
            obj[key] = value
            path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

        expect("control check", run("--check"), 0)
        mutate_summary("tarball_sha256", "f" * 64)
        expect("drift detected", run("--check"), 1)
        expect("propose", run("--propose"), 0)
        expect("apply without reason", run("--apply"), 1)
        expect("apply with short reason", run("--apply", "--reason", "x"), 1)
        proposal = dev / PROPOSAL_NAME
        p = read_json(proposal)
        p["rootfs_changes"][0]["to"] = "0" * 64
        proposal.write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")
        expect("tampered proposal", run("--apply", "--reason",
                                        "reviewed mirror update"), 1)
        fixture()
        mutate_summary("tarball_sha256", "f" * 64)
        run("--propose")
        mutate_summary("tarball_sha256", "1" * 64)
        expect("stale proposal", run("--apply", "--reason",
                                     "reviewed mirror update"), 1)
        fixture()
        mutate_summary("tarball_sha256", "f" * 64)
        mutate_summary("image_sha256", "2" * 64, kernel=True)
        expect("kernel drift refused", run("--propose"), 1)
        fixture()
        mutate_summary("tarball_sha256", "f" * 64)
        new_lock = b"base 2.0 arm64\n"
        (dev / "package-versions.txt").write_bytes(new_lock)
        obj = read_json(dev / "rootfs-build.json")
        obj["package_versions_sha256"] = sha256_bytes(new_lock)
        (dev / "rootfs-build.json").write_text(json.dumps(obj, indent=2) + "\n",
                                               encoding="utf-8")
        expect("propose with lockfile sync", run("--propose"), 0)
        expect("apply with reason", run("--apply", "--reason",
                                        "Debian mirror updated trixie"), 0)
        expect("check after apply", run("--check"), 0)
        expect("re-apply refused", run("--apply", "--reason",
                                       "Debian mirror updated trixie"), 1)
    return 0, ["POLICY OK: %d cases (drift, proposal, reason, tamper, stale, "
               "kernel boundary, lockfile sync, idempotence)" % len(cases)]


def execute(args):
    if args.command == "check":
        return do_check(args)
    if args.command == "propose":
        return do_propose(args)
    if args.command == "apply":
        return do_apply(args)
    if args.command == "self-test":
        return do_self_test(args)
    return 1, ["REFUSED: no command selected"]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent),
                        help="repository root (default: parent of scripts/)")
    parser.add_argument("--dev", default=None,
                        help="build artifact directory (default: $KNIX_DEV_DIR or ../knix-m0)")
    parser.add_argument("--manifest", default=None,
                        help="manifest path (default: <root>/platform/image/manifest.json)")
    parser.add_argument("--reason", default=None, help="why the rootfs baseline changed")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", dest="command", action="store_const", const="check")
    group.add_argument("--propose", dest="command", action="store_const", const="propose")
    group.add_argument("--apply", dest="command", action="store_const", const="apply")
    group.add_argument("--self-test", dest="command", action="store_const",
                       const="self-test")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.dev is None:
        args.dev = os.environ.get("KNIX_DEV_DIR") or str(
            pathlib.Path(args.root).parent / "knix-m0")
    if args.manifest is None:
        args.manifest = str(pathlib.Path(args.root) / "platform/image/manifest.json")
    if args.command == "self-test":
        rc, lines = do_self_test(args)
    else:
        try:
            rc, lines = execute(args)
        except (OSError, ValueError, KeyError) as exc:
            print("REFUSED: %s" % exc, file=sys.stderr)
            return 1
    for line in lines:
        print(line, file=sys.stderr if rc else sys.stdout)
    return rc


if __name__ == "__main__":
    sys.exit(main())

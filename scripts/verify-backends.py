#!/usr/bin/env python3
"""Verify the knix run-backend contract. Read-only.

`platform/contract.json` owns the boundary rules. Every backend declared there
owns a `backend.json` machine truth. This script proves that backends are launch
paths, not capability sources: one artifact, one publish surface, guest-owned
device/authentication/interface/input semantics, no host adapters, no build
artifacts and no claim of bare-metal verification that did not happen.

Runs on Python 3.9+ with no third-party modules. It never boots anything and
never needs Apple Silicon hardware.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "platform" / "contract.json"
MANIFEST = ROOT / "platform" / "image" / "manifest.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_SUFFIXES = (".img", ".qcow2", ".zst", ".fd", ".iso", ".ko")
ARTIFACT_NAMES = ("BUILD-RECEIPT.json", "BUILD-RECEIPT.json.sha256",
                  "knix-Image", "knix-rootfs.tar.zst")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    errors = []
    try:
        c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("BACKENDS FAILED: unreadable platform/contract.json: %s" % exc,
              file=sys.stderr)
        return 1

    backends = c.get("backends", [])
    surface = c.get("publish_surface", [])
    deny = c.get("deny_in_publishable_code", [])
    non_normative = [str(p).rstrip("/") for p in c.get("non_normative", [])]
    owner_value = c.get("backend_capability_source", "guest")
    owner_fields = c.get("backend_ownership_fields", [])
    unverified = c.get("unverified_backends", [])
    truth_template = c.get("backend_truth", "")
    if not backends or "<name>" not in truth_template:
        print("BACKENDS FAILED: contract lost backends or backend_truth", file=sys.stderr)
        return 1
    if len(surface) != 2 or "origin/main" not in surface:
        errors.append("publish_surface in contract.json drifted: %s" % surface)

    # A backend must never become an image build input: switching launch paths
    # cannot change the receipt, the locked baseline or the pinned inputs.
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for group, entries in m.get("inputs", {}).items():
            if not isinstance(entries, dict):
                continue
            for rel in entries:
                if str(rel).startswith("platform/backends/"):
                    errors.append("backend became an image build input: %s" % rel)
        pinned_artifact = m.get("artifact", {}).get("name")
    except (OSError, ValueError) as exc:
        errors.append("unreadable image manifest: %s" % exc)
        pinned_artifact = None

    states = []
    for name in backends:
        rel = truth_template.replace("<name>", str(name))
        p = ROOT / rel
        d = p.parent
        where = rel
        if not p.is_file():
            errors.append("backend machine truth missing: %s" % where)
            continue
        if not (d / "README.md").is_file():
            errors.append("backend documentation missing: %s/README.md" % name)
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append("unreadable %s: %s" % (where, exc))
            continue

        if b.get("schema") != c.get("backend_schema"):
            errors.append("%s: schema drift" % name)
        if b.get("name") != name:
            errors.append("%s: name does not match its directory" % name)
        if b.get("kind") != c.get("backend_kind"):
            errors.append("%s: a backend is being used as something other than a launch path" % name)
        if b.get("artifact") != "knix.img" or pinned_artifact != "knix.img":
            errors.append("%s: artifact is not the single knix.img" % name)
        if b.get("publish_surface") != surface:
            errors.append("%s: publish surface drifted" % name)
        if b.get("host_adapters") != "none":
            errors.append("%s: host adapters are enabled" % name)

        ownership = b.get("ownership", {})
        if not isinstance(ownership, dict):
            ownership = {}
            errors.append("%s: ownership is missing" % name)
        for field in owner_fields:
            if ownership.get(field) != owner_value:
                errors.append("%s: ownership.%s is not %s" % (name, field, owner_value))

        kernel = b.get("kernel", {})
        if not isinstance(kernel, dict):
            kernel = {}
            errors.append("%s: kernel baseline is missing" % name)
        frag = str(kernel.get("fragment", ""))
        frag_path = ROOT / frag
        if not frag.startswith("repos/linux/arch/arm64/configs/") or not frag_path.is_file():
            errors.append("%s: kernel fragment is not a read-only snapshot: %s" % (name, frag))
        elif not HEX64.match(str(kernel.get("fragment_sha256", ""))):
            errors.append("%s: malformed kernel fragment digest" % name)
        elif sha256(frag_path) != kernel.get("fragment_sha256"):
            errors.append("%s: kernel fragment sha256 drifted" % name)
        if kernel.get("arch") != "arm64":
            errors.append("%s: kernel arch drifted" % name)

        chain = b.get("boot_chain", [])
        if not isinstance(chain, list):
            errors.append("%s: boot_chain is not a list" % name)
            chain = []
        for item in chain:
            item = str(item)
            if not item.startswith("repos/") or not (ROOT / item).is_dir():
                errors.append("%s: boot chain member is not a read-only snapshot: %s" % (name, item))

        entry = b.get("boot_entry")
        if entry is not None:
            entry = str(entry)
            ep = ROOT / entry
            if not ep.is_file() or not (ep.stat().st_mode & 0o111):
                errors.append("%s: boot entry is not an executable script: %s" % (name, entry))

        fallback = b.get("fallback")
        if fallback is not None and (fallback not in backends or fallback == name):
            errors.append("%s: fallback is not another declared backend" % name)

        verified = b.get("verified")
        if not isinstance(verified, bool):
            errors.append("%s: verified is not a boolean" % name)
        elif name in unverified:
            if verified or b.get("status") != "contract-only" or entry is not None:
                errors.append("%s: claims verification that did not happen" % name)
            if not b.get("deferred"):
                errors.append("%s: deferred work is not declared" % name)
            states.append("%s=contract-only" % name)
        else:
            if not verified or b.get("status") != "verified" or entry is None:
                errors.append("%s: verified backend lost its executable launch path" % name)
            proof = b.get("verification", [])
            if not proof:
                errors.append("%s: verified backend has no verification recipe" % name)
            for item in proof:
                first = str(item).split()[0]
                if not (ROOT / first).is_file():
                    errors.append("%s: verification recipe missing: %s" % (name, first))
            states.append("%s=verified" % name)

        # A backend contract must not carry a host dependency, a private
        # framework name, a second artifact or a non-normative adapter path.
        text = p.read_text(encoding="utf-8")
        for word in deny:
            if word in text:
                errors.append("%s: forbidden host dependency in %s" % (name, where))
                break
        for banned in non_normative:
            if banned in text:
                errors.append("%s: references the non-normative adapter tree" % name)
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            if f.name in ARTIFACT_NAMES or f.name.endswith(ARTIFACT_SUFFIXES):
                errors.append("%s: build artifact in backend tree: %s"
                              % (name, f.relative_to(ROOT)))

    if errors:
        for e in errors:
            print("BACKENDS FAILED: %s" % e, file=sys.stderr)
        print("\n%d backend check(s) failed." % len(errors), file=sys.stderr)
        return 1
    print("BACKENDS OK: %d launch-path backends (%s), one knix.img, "
          "guest-owned capabilities, no host adapters, no build artifacts"
          % (len(backends), ", ".join(states)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

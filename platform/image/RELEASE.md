# Release

## Surface

`knix.img` and `origin/main` are the only publish surfaces. The image is a
build artifact and is not stored in this repository; `origin/main` carries the
identity, the build recipe and the verification gate that reproduce it.

| 阶段 | 命令 | 产物 |
| --- | --- | --- |
| 输入校验 | `platform/image/build.sh --check` | 无 |
| 镜像构建 | `platform/image/build.sh --build` | `$KNIX_DEV_DIR/knix.img` |
| 摘要与签名 | `platform/image/sign.sh` | `BUILD-RECEIPT.json` + `.sha256`（+ 可选 `.sig`） |
| 契约校验 | `python3 scripts/verify-knix.py` | 无 |

## Verify a release

```bash
# 1. the receipt itself is intact
openssl dgst -sha256 BUILD-RECEIPT.json
cat BUILD-RECEIPT.json.sha256

# 2. the image matches the receipt
python3 - <<'PY'
import hashlib, json, pathlib
r = json.loads(pathlib.Path("BUILD-RECEIPT.json").read_text())
h = hashlib.sha256()
with pathlib.Path("knix.img").open("rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
print("receipt:", r["sha256"])
print("image:  ", h.hexdigest())
print("MATCH" if h.hexdigest() == r["sha256"] else "MISMATCH")
PY

# 3. when a release key was used
openssl dgst -sha256 -verify knix-release.pub \
  -signature BUILD-RECEIPT.json.sig BUILD-RECEIPT.json
```

The receipt's `sha256` must equal `knix.img`. `.sha256` covers the receipt, not
the image. A release key is never stored in the repository;
`KNIX_SIGNING_KEY` selects one at signing time, and the matching public key is
distributed out of band.

## Identity ownership

`platform/identity/` owns the name, version, kernel baseline, package set,
session, brand bytes and publish surface. A run backend reads this identity and
never defines it. `repos.json` is an archive baseline and is never rewritten by
a build, a boot or a release.

## Rollback

Revert the `origin/main` commit that changed `platform/identity/` or
`platform/image/`, then rebuild. The image itself is derived, so it has no
independent version line.

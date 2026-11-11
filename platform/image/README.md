# Image

`knix.img` 的构建入口。构建输入来自 `platform/identity/`、固定 SHA512 的 seed qcow2 与 `docs/brand/`，产物只有一个镜像。自编译内核与原生 rootfs 由 `build-kernel.sh` / `build-rootfs.sh` 在 arm64 Linux guest 内从 `repos/linux` 与 `packages.json` 另行生成，落在仓外构建盘，是下一阶段镜像的输入。`platform/host-adapters/` 不参与构建，也不进入产物。

| 文件 | 角色 |
| --- | --- |
| `manifest.json` | 构建配方：seed SHA512、6 份身份文件与 4 个品牌资产的 SHA256、产物定义 |
| `build.sh` | 顶层编排；`--check` 只校验输入，`--build` 完整生成镜像与 receipt |
| `inject-identity.sh` | 把身份与品牌字节原样装进运行中的 guest |
| `build-rootfs.sh` | 在 guest 内用 `packages.json` 生成原生 rootfs 与 tarball |
| `build-kernel.sh` | 在 guest 内原生编译 `repos/linux`，产出 `Image.gz` 与 `modules_install` |
| `assemble-native.sh` | 在 guest 内把原生 rootfs、自编译内核与模块装进一张空白 GPT 盘，写 `fstab` / 用户 / SSH / 网络，供 `-kernel` 直启 |
| `sign.sh` | 生成 receipt 校验和；提供密钥时追加 detached 签名 |
| `RELEASE.md` | 发布面、校验与回滚 |

`build.sh --build` 在注入身份后显式 `sync`、等待 QEMU 正常退出、重启一次并复验身份与品牌字节，然后才写 receipt；否则 guest 页缓存里尚未落盘的写入会随 VM 停止而丢失，产生一个没有身份的镜像。构建盘上的产物同样需要在写入后 `sync`。

`build-rootfs.sh` 的覆盖变量：`KNIX_SETS`（默认全集）、`KNIX_VARIANT`（默认 `minbase`，包集由 `packages.json` 决定）、`KNIX_MIRROR`（默认 `http://deb.debian.org/debian`，QEMU slirp 下 HTTPS 并发下载会挂住）、`KNIX_DEB_CACHE`（默认关闭；仅用于本地复用已下载的 `.deb`，不是构建输入）。

`assemble-native.sh` 的目标盘必须是**新建的空白 qcow2**：从同一 seed 派生两张盘会带上相同的 ext4 UUID，guest 会把目标盘误认成根文件系统并挂到 `/`，格式化它就等于毁掉正在运行的系统。目标盘在 helper guest 里是 `/dev/vdd`，作为主盘直启时变成 `/dev/vda`，所以 `fstab` 与 `KERNEL_APPEND` 都用 `/dev/vda1`。内核把 virtio-blk / virtio-net / virtio-pci / ext4 / PL011 / devtmpfs 全部编入，因此该盘不需要 initrd 或 GRUB 即可直接引导。`knix.img` 是 dev preview 的 receipt 产物，组装阶段不碰它。

`.github/workflows/knix-contract.yml` 在 Linux CI 上校验仓内契约、输入哈希与脚本语法；seed qcow2 与 QEMU/HVF 启动只在本地构建机执行，CI 不下载它们。

镜像与 receipt 落在 `$KNIX_DEV_DIR`（默认仓外同级 `knix-m0/`），不入库。仓内只有可复现的定义与哈希；`origin/main` 是唯一发布面。

构建不改变 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 或 `totals`。

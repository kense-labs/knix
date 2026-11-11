# Image

`knix.img` 的唯一构建入口。固定输入来自 `platform/identity/`、`docs/brand/` 与 `virt.config`；seed qcow2 只作一次性包源与 helper 引导，不参与镜像内容。自编译内核、匹配模块与原生 rootfs 由 `build-kernel.sh` / `build-rootfs.sh` 在 arm64 Linux guest 内生成，各自产出 `kernel-build.json` / `rootfs-build.json` 记录摘要；`build.sh` 消费这些产物，组装出唯一镜像。`platform/host-adapters/` 不参与构建，也不进入产物。

| 文件 | 角色 |
| --- | --- |
| `manifest.json` | 构建配方：seed SHA512、6 份身份文件、4 个品牌资产与 `virt.config` 的摘要，三阶段构建顺序与发布面 |
| `build.sh` | 顶层编排；`--check` 校验全部输入哈希，`--build` 建空白盘 → guest 内组装 → 冷启复验 → 写含全部输入摘要的 receipt |
| `inject-identity.sh` | 把身份与品牌字节原样装进运行中的 guest |
| `build-rootfs.sh` | 在 guest 内用 `packages.json` 生成原生 rootfs、tarball 与 `rootfs-build.json` |
| `build-kernel.sh` | 在 guest 内原生编译 `repos/linux`：`--check` 校验 fragment 与内置启动驱动，`--apply` 产出 `Image.gz`、`knix-Image`、`modules/lib/modules/7.2.15` 与 `kernel-build.json`；检测到截断的构建树会自动清空重编 |
| `assemble-native.sh` | 在 guest 内把原生 rootfs、自编译内核与模块装进一张空白 GPT 盘，写 `fstab` / 用户 / SSH / 网络，供 `-kernel` 直启 |
| `sign.sh` | 生成 receipt 校验和；提供密钥时追加 detached 签名 |
| `RELEASE.md` | 发布面、校验与回滚 |

`build-kernel.sh` 在 `olddefconfig` 之后把 virtio-blk / virtio-net / virtio-pci / ext4 / PL011 / devtmpfs 六项硬校验为 `=y`：原生盘靠 `-kernel` 直启，挂载根分区前没有 initrd，也没有 `/lib/modules`，这些驱动不能是模块。`modules_install` 落在构建盘的 `modules/` 下，由 `assemble-native.sh` 装进目标盘；`kernel-build.json` 记录内核版本、两个内核镜像的 sha256 与模块数量。编译前后都检查 `vmlinux.o` 的 ELF section header 表是否落在文件内，避免截断对象让 `modpost` 失败。`assemble-native.sh` 把 `modules/lib/modules/$KVER` 整目录复制进目标盘，版本从 `kernel-build.json` 读取，并用目标盘自身 rootfs 的 `depmod` 生成 `modules.dep`；模块缺失时组装直接失败。

`kernel-build.json` 还记录最终 `.config` 的 `config_sha256`；`assemble-native.sh` 把 `wireless-regdb` 的 alternative 切到上游签名变体，使 `cfg80211` 能用内核内置密钥验证 `regulatory.db`。

`build.sh --build` 先把旧 `knix.img` 移到 `.prev`，`qemu-img create` 一张 8 GiB 空白 qcow2，再以 `DISK=knix-helper.img EXTRA_DISKS="build-disk.qcow2 knix.img"` 启动 helper guest，把 `kernel-build.json` / `rootfs-build.json` / `knix-Image` 取回 host 后交给 `assemble-native.sh` 组装到 `/dev/vdd`。组装脚本逐项比对两份摘要与模块数量，输入漂移即失败。随后 `sync` + guest 内 poweroff，`boot-native.sh` 用 `-kernel` 冷启一次，复验身份、内核版本、模块数量、`modprobe`、品牌字节、零失败单元与 cloud-init 残留，最后才写 receipt。guest 页缓存未 flush 会让写入随 VM 停止丢失，所有写入脚本都内置 `sync`。

`build-rootfs.sh` 的覆盖变量：`KNIX_SETS`（默认全集）、`KNIX_VARIANT`（默认 `minbase`，包集由 `packages.json` 决定）、`KNIX_MIRROR`（默认 `http://deb.debian.org/debian`，QEMU slirp 下 HTTPS 并发下载会挂住）、`KNIX_DEB_CACHE`（默认关闭；仅用于本地复用已下载的 `.deb`，不是构建输入）。

`assemble-native.sh` 的目标盘必须是**新建的空白 qcow2**：从同一 seed 派生两张盘会带上相同的 ext4 UUID，guest 会把目标盘误认成根文件系统并挂到 `/`，格式化它就等于毁掉正在运行的系统。目标盘在 helper guest 里是 `/dev/vdd`，作为主盘直启时变成 `/dev/vda`，所以 `fstab` 与 `KERNEL_APPEND` 都用 `/dev/vda1`。内核把 virtio-blk / virtio-net / virtio-pci / ext4 / PL011 / devtmpfs 全部编入，因此该盘不需要 initrd 或 GRUB 即可直接引导。`knix.img` 就是组装的目标盘；`build.sh --build` 会先把旧镜像移到 `.prev` 再建空白盘，失败时可回滚。

`.github/workflows/knix-contract.yml` 在 Linux CI 上校验仓内契约、输入哈希与脚本语法；seed qcow2 与 QEMU/HVF 启动只在本地构建机执行，CI 不下载它们。

镜像与 receipt 落在 `$KNIX_DEV_DIR`（默认仓外同级 `knix-m0/`），不入库。仓内只有可复现的定义与哈希；`origin/main` 是唯一发布面。

构建不改变 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 或 `totals`。

# Image

`knix.img` 的唯一构建入口。固定输入来自 `platform/identity/`、`docs/brand/` 与 `virt.config`；seed qcow2 只作一次性包源与 helper 引导，不参与镜像内容。自编译内核、匹配模块与原生 rootfs 由 `build-kernel.sh` / `build-rootfs.sh` 在 arm64 Linux guest 内生成，各自产出 `kernel-build.json` / `rootfs-build.json` 记录摘要；`build.sh` 消费这些产物，组装出唯一镜像。`platform/host-adapters/` 不参与构建，也不进入产物。

| 文件 | 角色 |
| --- | --- |
| `manifest.json` | 构建配方：seed SHA512、6 份身份文件、4 个品牌资产、5 份构建配方脚本与 `verify-graphics.sh` 的 sha256，三阶段构建顺序、UEFI / 图形契约与发布面 |
| `build.sh` | 顶层编排；`--check` 校验全部输入哈希，`--build` 建空白盘 → guest 内组装 → 冷启复验 → 写含全部输入摘要的 receipt |
| `inject-identity.sh` | 把身份与品牌字节原样装进运行中的 guest |
| `build-rootfs.sh` | 在 guest 内用 `packages.json` 生成原生 rootfs、tarball 与 `rootfs-build.json` |
| `build-kernel.sh` | 在 guest 内原生编译 `repos/linux`：`--check` 校验 fragment 与内置启动驱动，`--apply` 产出 `Image.gz`、`knix-Image`、`modules/lib/modules/7.2.15` 与 `kernel-build.json`；检测到截断的构建树会自动清空重编 |
| `assemble-native.sh` | 在 guest 内建 ESP + root 双分区 GPT 盘，装原生 rootfs、自编译内核与 773 个模块，生成自有 initramfs 并把 systemd-boot / 内核 / initrd / loader entry 写入 ESP，写 `fstab` / 用户 / SSH / 网络与图形模块加载配置，供 UEFI 自举 |
| `verify-graphics.sh` | 在冷启后的 guest 内以 root 验证 virtio-gpu DRM 节点、virtio 输入、sway 的 GBM / GL 实际渲染与 `foot` Wayland 窗口；纯 shell，不依赖 guest 内的 python3 / jq |
| `sign.sh` | 生成 receipt 校验和；提供密钥时追加 detached 签名 |
| `RELEASE.md` | 发布面、校验与回滚 |

`build-kernel.sh` 在 `olddefconfig` 之后把 virtio-blk / virtio-net / virtio-pci / ext4 / PL011 / devtmpfs 六项硬校验为 `=y`：自举路径在挂载根分区前不能依赖可加载模块，initramfs 也不打包这些驱动。图形驱动由脚本自己追加 `CONFIG_DRM_VIRTIO_GPU=m` / `CONFIG_VIRTIO_INPUT=m` 并硬校验 `CONFIG_DRM_VIRTIO_GPU_KMS=y`，不改写 `repos/linux/arch/arm64/configs/virt.config` 快照；模块文件是 `virtio-gpu.ko` / `virtio_input.ko`，`assemble-native.sh` 写 `/etc/modules-load.d/knix-graphics.conf` 让冷启自动加载，`kernel-build.json` 记录 `graphics_drivers` 与 `graphics_modules`。`modules_install` 落在构建盘的 `modules/` 下，由 `assemble-native.sh` 装进目标盘；`kernel-build.json` 记录内核版本、两个内核镜像的 sha256 与模块数量。编译前后都检查 `vmlinux.o` 的 ELF section header 表是否落在文件内，避免截断对象让 `modpost` 失败。`assemble-native.sh` 把 `modules/lib/modules/$KVER` 整目录复制进目标盘，版本从 `kernel-build.json` 读取，并用目标盘自身 rootfs 的 `depmod` 生成 `modules.dep`；模块缺失时组装直接失败。

`kernel-build.json` 还记录最终 `.config` 的 `config_sha256`；`assemble-native.sh` 把 `wireless-regdb` 的 alternative 切到上游签名变体，使 `cfg80211` 能用内核内置密钥验证 `regulatory.db`。`packages.json` 的 `boot` 集合提供 `systemd-boot-efi`（EFI 二进制）、`dosfstools`（ESP 格式化）与 `initramfs-tools`（生成 initrd），三者由 `build-rootfs.sh` 装进原生 rootfs；`KNIX_SETS` 默认包含该集合。

`build.sh --build` 先把旧 `knix.img` 移到 `.prev`，`qemu-img create` 一张 8 GiB 空白 qcow2，再以 `DISK=knix-helper.img EXTRA_DISKS="build-disk.qcow2 knix.img"` 启动 helper guest，把 `kernel-build.json` / `rootfs-build.json` / `knix-Image` 取回 host 后交给 `assemble-native.sh` 组装到 `/dev/vdd`。组装脚本逐项比对两份摘要与模块数量，输入漂移即失败。随后 `sync` + guest 内 poweroff，`boot-native.sh` 用干净 EDK2 变量存储做一次 **UEFI 自举冷启**（不传 `-kernel`、不挂 seed ISO，显式挂 `virtio-gpu-pci` 与 virtio 键鼠），复验身份、内核版本、模块数量、`modprobe`、品牌字节、零失败单元、cloud-init 残留、`cfg80211` 告警，以及 `/proc/cmdline` 的 `root=/dev/vda2`、ESP 上的 `BOOTAA64.EFI` / loader entry 与自有 initrd；随后把 `verify-graphics.sh` 以 root 流式送进 guest，验证实际渲染并输出 `GRAPHICS VERIFY OK`。任一步失败都中止构建，guest 侧打印诊断。最后才写 receipt，其中 `graphics` 段记录设备、验证脚本与 `verified`。guest 页缓存未 flush 会让写入随 VM 停止丢失，所有写入脚本都内置 `sync`。helper 启动显式 `GPU=none GPU_INPUT=none`，避免新增 PCI 设备改变 virtio-blk probe 顺序、挪动 `/dev/vdc` / `/dev/vdd`。

`build-rootfs.sh` 的覆盖变量：`KNIX_SETS`（默认全集）、`KNIX_VARIANT`（默认 `minbase`，包集由 `packages.json` 决定）、`KNIX_MIRROR`（默认 `http://deb.debian.org/debian`，QEMU slirp 下 HTTPS 并发下载会挂住）、`KNIX_DEB_CACHE`（默认关闭；仅用于本地复用已下载的 `.deb`，不是构建输入）。

`assemble-native.sh` 的目标盘必须是**新建的空白 qcow2**：从同一 seed 派生两张盘会带上相同的 ext4 UUID，guest 会把目标盘误认成根文件系统并挂到 `/`，格式化它就等于毁掉正在运行的系统。目标盘在 helper guest 里是 `/dev/vdd`，作为主盘自举时变成 `/dev/vda`：`/dev/vda1` 是 512 MiB FAT32 ESP，`/dev/vda2` 是 ext4 root，`fstab` 把 ESP 挂到 `/efi`，loader entry 的 `root=` 与 `fstab` 都指向 `/dev/vda2`。ESP 上是 `EFI/BOOT/BOOTAA64.EFI`（从 rootfs 的 `systemd-boot-efi` 包逐字节复制）、`EFI/knix/` 下的内核与 initrd、`loader/loader.conf` 与 `loader/entries/knix.conf`；initrd 由目标盘自身 rootfs 的 `mkinitramfs` 在 bind mount `/dev` / `/proc` / `/sys` 后生成，helper 用户态不进入产物。removable media path 不需要 EFI Boot#### 变量，因此干净固件变量也能启动。`knix.img` 就是组装的目标盘；`build.sh --build` 会先把旧镜像移到 `.prev` 再建空白盘，失败时可回滚。

`verify-graphics.sh` 只检查 guest 自有的东西：`/dev/dri/card0` 与 `renderD128`、`/sys/module/virtio_gpu` 与 `virtio_input`、`card0-Virtual-1` 的 1280×800 mode、`/dev/input/by-path` 的 virtio 键盘与指针、`knix.desktop` 的 `Exec=sway`；再以 `XDG_RUNTIME_DIR=/run/user/0 WLR_BACKENDS=drm` 启动 sway 1.10.1，**不能**设置 `LIBGL_ALWAYS_SOFTWARE=1`（显式硬件 EGL device 与该变量冲突会让 Mesa segfault）。随后用 `swaymsg` 确认 `Virtual-1` active、日志里出现 `Allocated ... GBM buffer` / `Created GL FBO`，并 `swaymsg exec /usr/bin/foot` 让一个真实 Wayland 客户端出现在 `get_tree` 且提交 `New wlr_surface`。脚本以 `trap` 收掉 sway，输出 `GRAPHICS VERIFY OK`。headless 验证会话以 root 启动、没有 logind seat，`swaymsg -t get_inputs` 为空；virtio 输入节点仍由 guest 内核拥有，正式会话由 seatd / logind 接管。`GPU=none` 时串口与 SSH 保持可用。

`.github/workflows/knix-contract.yml` 在 Linux CI 上校验仓内契约、全部输入哈希、UEFI 自举与 guest-owned DRM 契约、脚本语法；seed qcow2、QEMU/HVF 启动与 sway 实际渲染只在本地构建机执行，CI 不下载它们。

镜像与 receipt 落在 `$KNIX_DEV_DIR`（默认仓外同级 `knix-m0/`），不入库。仓内只有可复现的定义与哈希；`origin/main` 是唯一发布面。

构建不改变 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 或 `totals`。

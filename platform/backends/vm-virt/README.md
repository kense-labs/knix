# Backend: vm-virt

标准 virtio 虚拟化启动后端。内核基线 `repos/linux/arch/arm64/configs/virt.config`（`# CONFIG_ARCH_APPLE is not set`）；设备语义为 `virtio-blk` / `virtio-net` / `virtio-gpu` / `virtio-input` / `virtio-balloon`，由 QEMU 或兼容 VMM 提供。

只使用标准 compatible，不依赖专有 UAPI，因此不受品牌化改名的 ABI 影响，是当前零冲突的可执行路径。图形加速走标准 Mesa virtio-gpu（virgl / Venus）；宿主侧实现不属于 knix。

产品身份由 `platform/identity/` 定义；后端不改变 `knix.img` 与 `origin/main`。

`boot-dev.sh` 是可复现启动入口：

```bash
KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-dev.sh
```

`boot-native.sh` 是原生系统盘的直启入口，用 `-kernel` 加载 `knix-Image`，不经过 GRUB、不借用 seed 的 initrd：

```bash
KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-native.sh
```

它默认读 `$KNIX_DEV_DIR/knix-native.img` 与 `$KNIX_DEV_DIR/knix-Image`，内核参数为 `root=/dev/vda1 rw console=ttyAMA0`；三者都可用 `DISK` / `KERNEL` / `KERNEL_APPEND` 覆盖。该盘由 `assemble-native.sh` 在 guest 内组装，是开发期验证产物，不是发布面。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KNIX_DEV_DIR` | 仓外同级 `knix-m0/` | 镜像、seed、固件 vars、日志与 pidfile |
| `DISK` | `$KNIX_DEV_DIR/knix.img` | 根盘 |
| `CODE` / `VARS` | QEMU 自带 EDK2 / 本地 vars 副本 | UEFI pflash |
| `ACCEL` / `CPU` / `GIC` / `SMP` / `MEM` | `hvf` / `host` / `3` / `4` / `4096` | 启动参数 |
| `KERNEL` / `INITRD` / `KERNEL_APPEND` | 空 | 设置 `KERNEL` 时用 `-kernel` 直启，不经过 GRUB |
| `EXTRA_DISK` | 空 | 单个附加 virtio 盘，用于 guest 内原生内核/rootfs 构建 |
| `EXTRA_DISKS` | 空 | 空格分隔的多个附加盘，按给定顺序成为 `/dev/vdc`、`/dev/vdd`…；用于同时挂构建盘与目标盘 |
| `SSH_PORT` | `2222` | 用户态网络端口转发 |

启动脚本只决定启动路径。身份、认证、界面、输入与设备语义全部在 Guest；宿主只提供标准 virtio。固件使用 QEMU 自带的 `edk2-aarch64-code.fd` + `edk2-arm-vars.fd`；Debian `qemu-efi-aarch64` 的 AAVMF 变体会停在固件阶段，不作为本后端的固件来源。

附加构建盘是 qcow2。停止 VM 前必须在 guest 内 `sync`，否则页缓存里未回写的产物会变成 0 字节；`build.sh --build`、`build-kernel.sh`、`build-rootfs.sh` 与 `inject-identity.sh` 都已内置该步骤。

Deferred：能力协商清单与 `knix check` 后端探测。

# Backend: vm-virt

标准 virtio 虚拟化启动后端。内核基线 `repos/linux/arch/arm64/configs/virt.config`（`# CONFIG_ARCH_APPLE is not set`）；设备语义为 `virtio-blk` / `virtio-net` / `virtio-gpu` / `virtio-input` / `virtio-balloon`，由 QEMU 或兼容 VMM 提供。

只使用标准 compatible，不依赖专有 UAPI，因此不受品牌化改名的 ABI 影响，是当前零冲突的可执行路径。图形加速走标准 Mesa virtio-gpu（virgl / Venus）；宿主侧实现不属于 knix。

产品身份由 `platform/identity/` 定义；后端不改变 `knix.img` 与 `origin/main`。

`boot-dev.sh` 是可复现启动入口：

```bash
KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-dev.sh
```

`boot-native.sh` 是唯一镜像 `knix.img` 的 UEFI 自举入口：不挂 `-kernel`、不挂 seed ISO、不用宿主内核或 initrd，并默认挂上 `virtio-gpu-pci` 与 virtio 键鼠。Guest 内核里的 `virtio-gpu` / `virtio_input` 模块由 `/etc/modules-load.d/knix-graphics.conf` 加载，sway 直接在 guest 自有的 `/dev/dri/card0` 上渲染；宿主只提供标准 virtio 设备，不提供图形能力。`build.sh --build` 的冷启验收用 `platform/image/verify-graphics.sh` 验证 GBM / GL 实际渲染与 Wayland 客户端窗口。设置 `GPU=none GPU_INPUT=none` 可回到纯串口 + SSH 路径；helper / 构建路径显式关闭图形设备，避免 PCI probe 顺序改变构建盘设备名。

```bash
KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-native.sh
```

它默认读 `$KNIX_DEV_DIR/knix.img`，每次从 QEMU 的 `edk2-arm-vars.fd` 复制一份干净的 EFI 变量存储到 `AAVMF_VARS-selfboot.fd`，因此 EDK2 只能走 ESP 的 removable media path `EFI/BOOT/BOOTAA64.EFI`，启动不依赖任何 EFI Boot#### 变量。内核参数由 ESP 上的 `loader/entries/knix.conf` 提供（`root=/dev/vda2 rw console=ttyAMA0`），不来自 QEMU。`DISK` / `VARS` / `KNIX_KEEP_VARS` 可覆盖，`SEED` 固定为空（镜像没有 cloud-init）；`KERNEL` 仍是 `boot-dev.sh` 上的调试覆盖。镜像由 `build.sh --build` 在 guest 内组装，自带 `/lib/modules/$KVER` 与 `modules.dep`，是唯一发布产物。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KNIX_DEV_DIR` | 仓外同级 `knix-m0/` | 镜像、seed、固件 vars、日志与 pidfile |
| `DISK` | `$KNIX_DEV_DIR/knix.img` | 根盘；helper 构建时覆盖为 `knix-helper.img` |
| `CODE` / `VARS` | QEMU 自带 EDK2 / `AAVMF_VARS.fd`；自举路径用 `AAVMF_VARS-selfboot.fd` | UEFI pflash |
| `GPU` | `none`（`boot-dev.sh`）；`virtio-gpu-pci`（自举路径） | 标准 virtio-gpu；DRM 驱动、合成器与会话都在 Guest |
| `GPU_INPUT` | `none`（`boot-dev.sh`）；`virtio-keyboard-pci virtio-tablet-pci`（自举路径） | 标准 virtio 输入；空格分隔可给多个 `-device` |
| `DISPLAY_BACKEND` | `none` | QEMU 显示后端；`cocoa` 可在本机开窗，headless 验收保持 `none` |
| `SEED` | `$KNIX_DEV_DIR/seed.iso`；自举路径为空 | cloud-init NoCloud 数据源，仅 helper 使用 |
| `ACCEL` / `CPU` / `GIC` / `SMP` / `MEM` | `hvf` / `host` / `3` / `4` / `4096` | 启动参数 |
| `KERNEL` / `INITRD` / `KERNEL_APPEND` | 空 | 设置 `KERNEL` 时用 `-kernel` 直启（调试覆盖），不经过 ESP |
| `EXTRA_DISK` | 空 | 单个附加 virtio 盘，用于 guest 内原生内核/rootfs 构建 |
| `EXTRA_DISKS` | 空 | 空格分隔的多个附加盘，按给定顺序成为 `/dev/vdc`、`/dev/vdd`…；`build.sh --build` 用它同时挂构建盘与目标镜像 |
| `SSH_PORT` | `2222` | 用户态网络端口转发 |

启动脚本只决定启动路径。身份、认证、界面、输入与设备语义全部在 Guest；宿主只提供标准 virtio。固件使用 QEMU 自带的 `edk2-aarch64-code.fd` + `edk2-arm-vars.fd`；Debian `qemu-efi-aarch64` 的 AAVMF 变体会停在固件阶段，不作为本后端的固件来源。

附加构建盘是 qcow2。停止 VM 前必须在 guest 内 `sync`，否则页缓存里未回写的产物会变成 0 字节；`build.sh --build`、`build-kernel.sh`、`build-rootfs.sh`、`assemble-native.sh` 与 `inject-identity.sh` 都已内置该步骤。

Deferred：能力协商清单与 `knix check` 后端探测。

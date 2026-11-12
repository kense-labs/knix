# Backend: vm-virt

机器真源 `backend.json`（schema 1，`status: verified`）；规则所有者 `platform/contract.json`，执行者 `scripts/verify-backends.py`。后端真源不是镜像构建输入，不进入 `platform/image/manifest.json` 的 `inputs`。

标准 virtio 虚拟化启动后端。内核基线 `repos/linux/arch/arm64/configs/virt.config`（`# CONFIG_ARCH_APPLE is not set`）；设备语义为 `virtio-blk` / `virtio-net` / `virtio-gpu` / `virtio-input` / `virtio-balloon`，由 QEMU 或兼容 VMM 提供。

只使用标准 compatible，不依赖专有 UAPI，因此不受品牌化改名的 ABI 影响，是当前零冲突的可执行路径。图形加速走标准 Mesa virtio-gpu（virgl / Venus）；宿主侧实现不属于 knix。

产品身份由 `platform/identity/` 定义；后端不改变 `knix.img` 与 `origin/main`。

`boot-dev.sh` 是可复现启动入口：

```bash
KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-dev.sh
```

`boot-native.sh` 是唯一镜像 `knix.img` 的 UEFI 自举入口：不挂 `-kernel`、不挂 seed ISO、不用宿主内核或 initrd，并默认挂上 `virtio-gpu-pci` 与 virtio 键鼠。Guest 内核里的 `virtio-gpu` / `virtio_input` 模块由 `/etc/modules-load.d/knix-graphics.conf` 加载，sway 直接在 guest 自有的 `/dev/dri/card0` 上渲染；宿主只提供标准 virtio 设备，不提供图形能力。镜像开机即由 guest 自己的 `multi-user.target` 启动 `knix-session.service`，不依赖任何宿主启动器。`build.sh --build` 的冷启验收先证明该 unit 已 `enabled` + `active`，`platform/image/verify-graphics.sh` 只 restart 取干净日志：sway 以 `knix` 非 root 用户经 seatd 运行，`WLR_BACKENDS=drm,libinput` 让 `get_inputs` 报告 virtio 键鼠，同时验证 GBM / GL 实际渲染与 Wayland 客户端窗口。设置 `GPU=none GPU_INPUT=none` 可回到纯串口 + SSH 路径，unit 的 `ConditionPathExists=/dev/dri/card0` 不触发（实测 enabled 但 inactive、0 失败单元）；`DISPLAY_BACKEND=cocoa` 可在 macOS 上打开窗口看到这个桌面。headless 验收不需要开窗：`boot-dev.sh` 始终暴露一个 QMP unix socket，`build.sh --build` 在关掉 guest 之前用 `screendump format=ppm` 只读导出 virtio-gpu scanout，核对 guest 自选并绘制的纯色图案与 swaybar 像素（`FRAMEBUFFER VERIFY OK`）。主机只观察，不绘制也不注入 guest 内容。helper / 构建路径显式关闭图形设备，避免 PCI probe 顺序改变构建盘设备名。

会话的失败态也归 Guest：`knix-session.service` 的 `Restart=on-failure` / `RestartSec=2` / `StartLimitBurst=5` / `StartLimitIntervalSec=30` 由 guest 自己的 systemd 执行。`build.sh --build` 的冷启验收在单次 SIGKILL 恢复之后继续故意击杀，直到 systemd 报 `Start request repeated too quickly.` 并进入 `failed`，此时要求零 sway 进程、恰好 1 个失败单元（会话 unit）、`ssh` 与串口完好；随后 `reset-failed` + `restart` 清场，恢复后的 sway 重新提供 libinput / DRM / GBM / GL / Wayland 客户端并画出 P10 的像素图案。宿主只提供标准 virtio 设备，不代为重启、停用或伪造失败态；`GPU=none` 时 DRM condition 不满足，不会构造崩溃循环。30 秒滑动窗口下 `NRestarts` 为 4 或 5、`Result` 恒为 `signal`，门禁不硬编码次数。

失败诊断同样归 Guest：`knix-session.service` 的 `OnFailure=knix-session-failure.service` 让 systemd 在每次失败态自动调用 guest 自有的 oneshot 脚本，把 unit 状态、journal 与本地设备节点写成 `/run/knix/session-failure.txt` 与 `session-failure.history`。验收脚本只读这两份 guest 产物，不代写报告；报告单元自身必须成功，否则会变成第二个失败单元。`/run` 是运行期快照，持久证据由 guest journal 保存，主机不收集、不上报、不注入任何诊断数据。

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
| `QMP` | `$KNIX_DEV_DIR/qmp.sock` | QEMU QMP unix socket；headless `screendump` 像素验收从这里读取标准 virtio-gpu scanout |
| `PIDFILE` / `SERIAL` | `$KNIX_DEV_DIR/qemu.pid` / `serial.log` | QEMU 进程与串口日志，可覆盖以便并行使用 overlay 副本 |

启动脚本只决定启动路径。身份、认证、界面、输入与设备语义全部在 Guest；宿主只提供标准 virtio。固件使用 QEMU 自带的 `edk2-aarch64-code.fd` + `edk2-arm-vars.fd`；Debian `qemu-efi-aarch64` 的 AAVMF 变体会停在固件阶段，不作为本后端的固件来源。

附加构建盘是 qcow2。停止 VM 前必须在 guest 内 `sync`，否则页缓存里未回写的产物会变成 0 字节；`build.sh --build`、`build-kernel.sh`、`build-rootfs.sh`、`assemble-native.sh` 与 `inject-identity.sh` 都已内置该步骤。

发布产物由仓内 `scripts/verify-release.py` 只读验收，与本后端的启动路径无关：切换后端不改变 18 个锁定输入、`build_baseline` 或 receipt，因此后端真源始终不在 `manifest.json.inputs` 里。该验收只判定已构建的字节与证据链，不启动本后端的 VM，也不改写 `knix.img`。

P14 的重启复验也走本后端：`build.sh --build` 在第一次 clean power-off 之后用 `KNIX_KEEP_VARS=1` 保留 EFI 变量、用**同一张** `knix.img` 再启动一次，验证 guest 自有的持久崩溃档案跨 boot 保留；串行日志与 QMP socket 使用独立文件，避免覆盖冷启证据。镜像 sha256 在第二次 power-off 之后才计算，因此 receipt 仍对应最终发布字节。切换后端不改变这条验收链，后端真源始终不在 `manifest.json.inputs` 里。

P15 复用同一次重启：guest 自己的 `OnFailure=` 单元在新 boot 里再报一次失败，验收只通过 guest systemd 发一个 SIGKILL，随后硬校验跨 boot 统计推进到 2 个 boot、档案仍含两个 boot id、持久告警与 `err` 级 journal 消息仍在，并由 guest systemd 自己恢复零失败单元的 `knix` 会话。轮转演练完全发生在 guest 的 `/run/knix/rotation-drill`，本后端不提供任何诊断写入或转发能力；发布验收升级为 16 项证据检查。

Deferred：能力协商清单与 `knix check` 后端探测。

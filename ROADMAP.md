# Roadmap

`platform/CONTRACT.md` 拥有产品边界。本文件记录镜像从开发验证走向可发布状态的阶段顺序；每阶段的输入、产物与验收都落在仓内脚本与门禁里，产物不入库。

## 阶段

| # | 阶段 | 入口 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| P0 | 修复构建树 | `platform/image/build-kernel.sh --apply` | `Image.gz` · `knix-Image` · `modules/lib/modules/7.2.15` | `.ko` 数量非零；`vmlinux.o` 的 ELF section headers 落在文件内；`sync` 后重新挂载仍完整 |
| P1 | 合并单一镜像 | `platform/image/build.sh --build` | 一个 `knix.img` 与一个 receipt | 镜像内是原生 rootfs、自编译内核与配套模块；receipt 的 sha256 与实际字节一致；`--check` 通过 |
| P2 | 自引导 | `platform/image/assemble-native.sh` · `platform/backends/vm-virt/boot-native.sh` | ESP + systemd-boot + 自有 initramfs | 不挂 `-kernel` 即可启动；串口与 SSH 可用；`systemctl --failed` 为 0 |
| P3 | 图形栈 | `platform/image/verify-graphics.sh` · `platform/backends/vm-virt/boot-native.sh` | 标准 virtio-gpu / virtio-input + guest 内 sway 实际渲染 | DRM 节点由 Guest 拥有、sway 在 DRM backend 上分配 GBM/GL 缓冲、Wayland 客户端拿到窗口 |
| P4 | 可复现与 CI | `scripts/verify-repro.py` · `.github/workflows/knix-contract.yml` | 锁定构建基线 + CI 可复现性门禁 | 全部 17 个输入哈希、内核/rootfs 基线、包版本 lockfile、receipt 契约与「仓内无二进制构建产物」在 CI 上可执行；`build.sh --build` 组装前拒绝任何基线漂移 |
| P5 | `native-apple` | `platform/backends/native-apple/backend.json` · `scripts/verify-backends.py` | 两个后端的机器真源与边界门禁 | 通过 I14 与 I17；身份、镜像、认证、界面、输入与设备语义仍留在 Guest；后端不进入镜像构建输入 |

## 状态

| # | 状态 | 依据 |
| --- | --- | --- |
| P0 | 完成 | 全量重编产出 770 个 `.ko` 与 `modules.dep`；原生盘直启后 `modprobe br_netfilter` 成功，无 vermagic / unknown symbol 错误；`/boot/vmlinuz-7.2.15-knix` 与 `kernel-build.json` 的 sha256 一致 |
| P1 | 完成 | `knix.img` 是唯一发布产物：原生 rootfs + 自编译 7.2.15 内核 + 770 个 `.ko`；receipt 记录内核 `config_sha256`、rootfs tarball 与全部输入摘要；`build.sh --build` 组装 + 冷启复验通过，`boot-native.sh` 为启动入口 |
| P2 | 完成 | `knix.img` 为 GPT 双分区：512 MiB FAT32 ESP + ext4 root；ESP 上是 systemd-boot、自编译内核、guest 内生成的 initramfs 与 loader entry。`boot-native.sh` 用干净 EDK2 变量、无 `-kernel`、无 seed ISO 冷启复验通过，receipt 记录 `UEFI self-boot` |
| P3 | 完成 | `boot-native.sh` 默认挂 `virtio-gpu-pci` + virtio 键鼠；`build-kernel.sh` 编入 `CONFIG_DRM_VIRTIO_GPU=m` / `CONFIG_VIRTIO_INPUT=m`，模块数 773。`verify-graphics.sh` 在 guest 内验证 `/dev/dri/card0`、`renderD128`、`Virtual-1` 1280×800、sway 1.10.1 的 GBM buffer / GL FBO、`foot` 的 Wayland 窗口与 `New wlr_surface`，输出 `GRAPHICS VERIFY OK`；已接入 `build.sh --build` 冷启硬门槛与 receipt 的 `graphics` 段 |
| P4 | 完成 | `build_baseline` 把 guest 侧内核（`config_sha256` / 两个镜像 hash / 773 模块 / 图形驱动）与 rootfs（tarball hash / 字节数 / 包集 / 388 个包版本）固化进 `manifest.json`；`package-versions.txt` 是文本锁定基线，`build.sh --build` 逐字段强校验并在漂移时中止，receipt 记录 `baseline.sha256` 与 `baseline.verified`。`scripts/verify-repro.py` 作为 CI 独立步骤校验 17 个输入、基线结构、lockfile、构建阶段、receipt 字段与「仓内无二进制产物」。CI 内完整构建重新界定为 deferred：seed 不入库、HVF 只在 macOS 可用。最终实测 `BASELINE OK` → `GRAPHICS VERIFY OK` → `BUILD OK`：`knix.img` sha256 `644a6109…` / 1,255,931,904 bytes，receipt schema 3 `baseline.sha256 b75be222…`；4 类故意漂移全部被 `BASELINE DRIFT` 拦截 |
| P5 | 契约完成 · 裸机实测 deferred | `native-apple` 与 `vm-virt` 各有 schema 1 的 `backend.json`；`platform/contract.json` 的 `backend_truth` / `backend_ownership_fields` / `unverified_backends` 定义规则。`scripts/verify-backends.py` 校验 launch-path 语义、Guest 对能力/设备/认证/界面/输入的归属、单一 `knix.img` 与发布面、引导链的只读快照引用、两份内核 fragment 的 sha256、无宿主依赖与后端目录无构建产物，并拒绝 `native-apple` 假称实测。I17 与 CI `Run-backend contract gate` 落地。后端真源不进入 17 个构建输入，receipt 与锁定基线不变；`vm-virt` 仍是唯一已实测启动路径。Apple Silicon 裸机启动、外部 ABI 映射、该后端的镜像构建与安装流程仍 deferred |

## 顺序约束

- P0 的模块目录是 P1 的输入；P1 完成后 `knix.img` 才脱离 seed 的用户态。
- P2 依赖 P1 的单镜像；P3 依赖 P2 之后的常规 UEFI 启动路径。
- P4 贯穿全部阶段：每次新增构建输入都要同步 `platform/image/manifest.json`、I15 与 I16；重跑 `build-rootfs.sh --apply` 后必须把新的 `package-versions.txt` 与全部摘要一起刷新到基线，否则 `build.sh --build` 会拒绝组装。
- P5 不改变发布面，也不放宽 I14；新增后端必须同步 `platform/contract.json`、两个后端 README、I17 与 CI 门禁，且不得进入 `manifest.json.inputs`。

## 不做

- 不出现第二个发布面、第二个 `origin`、tag 或发布分支。
- 不重写 Git 历史。
- 不把宿主状态、私有框架或宿主适配器当作能力来源。
- 不把 `native-apple` 标为已实测，也不为它生成第二个镜像或第二套发布面。
- 不手工编辑 `docs/brand/` 的 PNG，也不绕过生成器改写 logo。
- 不把 `knix.img`、构建盘、rootfs tarball 或其他二进制构建产物提交进仓；文本形式的包版本锁定清单是构建输入，属于仓内真源。
- 不在 CI 里跑 QEMU/HVF 完整构建：seed qcow2 不入库、HVF 只在 macOS 可用；完整构建由本机执行并产出 receipt。
- 不改写 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 与 `totals`。

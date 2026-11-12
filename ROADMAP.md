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
| P6 | 正式图形会话 | `platform/identity/session.json` · `platform/image/verify-graphics.sh` | 非 root sway + guest 自有 seatd + 真实 libinput 设备 | sway 以 `knix` 运行；`get_inputs` 报告 virtio 键鼠；`drm,libinput` 双 backend、seat 标签与 `GPU=none` 降级路径都在门禁里 |
| P7 | 开机自启桌面 | `platform/identity/session.json` · `platform/image/assemble-native.sh` | guest 自有 `multi-user.target` 启用 `knix-session.service` | 冷启后、任何验收脚本触碰 unit 之前即 `enabled` + `active`；`GPU=none` 时保持 enabled/inactive、0 失败单元、串口与 SSH 可用 |
| P8 | 锁定基线刷新 | `scripts/refresh-baseline.py` · `manifest.json.baseline_refresh` | 三段式提案（check / propose / apply）+ 字段级 diff + 无 guest 产物的策略自测 | `--check` 逐字段比对 17 个输入与两份摘要；`--propose` 写 old/new 与四份指纹；`--apply --reason` 校验理由、未应用、指纹与字段一致；内核漂移一律拒绝；CI 不依赖 seed 或 VM 即可跑 `--self-test` |
| P9 | 会话韧性 | `platform/identity/session.json` · `platform/image/verify-graphics.sh` | guest 自有 systemd 崩溃恢复 + 冷启硬门槛 | `SIGKILL` 合成器后由 guest systemd 拉起新 PID，sway 仍属 `knix`，DRM / libinput / GBM / GL / Wayland 客户端全部由重启后的进程重新提供；`StartLimitBurst` 限制重启循环；receipt 记录 `graphics.session.resilience` |

## 状态

| # | 状态 | 依据 |
| --- | --- | --- |
| P0 | 完成 | 全量重编产出 770 个 `.ko` 与 `modules.dep`；原生盘直启后 `modprobe br_netfilter` 成功，无 vermagic / unknown symbol 错误；`/boot/vmlinuz-7.2.15-knix` 与 `kernel-build.json` 的 sha256 一致 |
| P1 | 完成 | `knix.img` 是唯一发布产物：原生 rootfs + 自编译 7.2.15 内核 + 770 个 `.ko`；receipt 记录内核 `config_sha256`、rootfs tarball 与全部输入摘要；`build.sh --build` 组装 + 冷启复验通过，`boot-native.sh` 为启动入口 |
| P2 | 完成 | `knix.img` 为 GPT 双分区：512 MiB FAT32 ESP + ext4 root；ESP 上是 systemd-boot、自编译内核、guest 内生成的 initramfs 与 loader entry。`boot-native.sh` 用干净 EDK2 变量、无 `-kernel`、无 seed ISO 冷启复验通过，receipt 记录 `UEFI self-boot` |
| P3 | 完成 | `boot-native.sh` 默认挂 `virtio-gpu-pci` + virtio 键鼠；`build-kernel.sh` 编入 `CONFIG_DRM_VIRTIO_GPU=m` / `CONFIG_VIRTIO_INPUT=m`，模块数 773。`verify-graphics.sh` 在 guest 内验证 `/dev/dri/card0`、`renderD128`、`Virtual-1` 1280×800、sway 1.10.1 的 GBM buffer / GL FBO、`foot` 的 Wayland 窗口与 `New wlr_surface`，输出 `GRAPHICS VERIFY OK`；已接入 `build.sh --build` 冷启硬门槛与 receipt 的 `graphics` 段 |
| P4 | 完成 | `build_baseline` 把 guest 侧内核（`config_sha256` / 两个镜像 hash / 773 模块 / 图形驱动）与 rootfs（tarball hash / 字节数 / 包集 / 388 个包版本）固化进 `manifest.json`；`package-versions.txt` 是文本锁定基线，`build.sh --build` 逐字段强校验并在漂移时中止，receipt 记录 `baseline.sha256` 与 `baseline.verified`。`scripts/verify-repro.py` 作为 CI 独立步骤校验 17 个输入、基线结构、lockfile、构建阶段、receipt 字段与「仓内无二进制产物」。CI 内完整构建重新界定为 deferred：seed 不入库、HVF 只在 macOS 可用。最终实测 `BASELINE OK` → `GRAPHICS VERIFY OK` → `BUILD OK`：`knix.img` sha256 `644a6109…` / 1,255,931,904 bytes，receipt schema 3 `baseline.sha256 b75be222…`；4 类故意漂移全部被 `BASELINE DRIFT` 拦截 |
| P5 | 契约完成 · 裸机实测 deferred | `native-apple` 与 `vm-virt` 各有 schema 1 的 `backend.json`；`platform/contract.json` 的 `backend_truth` / `backend_ownership_fields` / `unverified_backends` 定义规则。`scripts/verify-backends.py` 校验 launch-path 语义、Guest 对能力/设备/认证/界面/输入的归属、单一 `knix.img` 与发布面、引导链的只读快照引用、两份内核 fragment 的 sha256、无宿主依赖与后端目录无构建产物，并拒绝 `native-apple` 假称实测。I17 与 CI `Run-backend contract gate` 落地。后端真源不进入 17 个构建输入，receipt 与锁定基线不变；`vm-virt` 仍是唯一已实测启动路径。Apple Silicon 裸机启动、外部 ABI 映射、该后端的镜像构建与安装流程仍 deferred |
| P6 | 完成 | `session.json` 的 `seat_provider` 声明 guest 自有 seatd、`knix` 用户与 `drm,libinput` 双 backend；`assemble-native.sh` 装 `71-knix-virtio-input.rules` 与默认关闭的 `knix-session.service`；`verify-graphics.sh` 用 `systemctl start` 启动 unit，硬校验 sway 进程属 knix、`get_inputs` 返回 3 个设备（含 QEMU Virtio Keyboard / Tablet）、GBM / GL 与 foot 窗口，输出 `SESSION VERIFY OK`。rootfs 因身份变更重建，tarball `c79bc430…` / 226,810,351 bytes 与 `build_baseline` 同步刷新；receipt 新增 `graphics.session`。最终 `P6_BUILD_RC=0`，`knix.img` sha256 `e18dd60d…` / 1,267,073,024 bytes，`baseline.sha256 38d5abac…`，I18 与 CI 会话契约落地 |
| P7 | 完成 | `session.json` 新增 `autostart` 契约（`multi-user.target` / `/dev/dri/card0` / guest 归属），`assemble-native.sh` 建 `multi-user.target.wants` 自启 symlink 并让 unit `After` / `Wants` udev settle；`build.sh` 在任何验证脚本之前硬校验 unit `enabled` + `active`，`verify-graphics.sh` 只 restart 取干净日志并输出 `AUTOSTART VERIFY OK`，不再 stop 会话。`manifest.json` 补 graphics 自启字段、I19 与 CI 契约落地。rootfs 因身份变更重建为 `a09aebf2…` / 226,810,817 bytes 并刷新锁定基线。最终 `P7_BUILD_RC=0`，`knix.img` sha256 `95a6fed1…` / 1,258,225,664 bytes、`baseline.sha256 d8602a0b…`、receipt 记录 `graphics.session.autostart.verified`；`GPU=none` 用 qcow2 overlay 副本独立复验为 enabled / inactive / 0 失败单元 / 串口与 SSH 完好，发布镜像字节未变；6 类自启契约越界全部被 CI 契约块与 I19 拒绝，还原后对照全绿 |
| P8 | 完成 | `scripts/refresh-baseline.py` 把锁定基线刷新变成可评审动作：`--check` 逐字段比对 17 个输入与 kernel/rootfs 摘要、guest lockfile 与仓内 lockfile，`--propose` 把 rootfs 字段 / 输入哈希 / lockfile 的 old-new 与四份指纹写进仓外 `BASELINE-PROPOSAL.json`，`--apply --reason "..."` 要求理由不少于 8 字符、提案未应用、指纹未变、字段仍与当前摘要一致，才定点替换 `build_baseline.rootfs` 与 `*_sha256` 输入组并同步 lockfile。`manifest.json.baseline_refresh` 是策略真源，I20 + `verify-repro.py` + CI 契约块 + `Baseline refresh policy` 步骤（共 9 步）拦截策略漂移，`--self-test` 在临时目录跑 12 个用例。内核摘要漂移永不由此脚本刷新，必须回到 `build-kernel.sh --apply`。实测：真实 `build.sh` 输入漂移经提案 + 理由刷新（1 个 input change），随后 `build.sh --build` 全链路通过；`knix-m0/p8-refresh-drift.log` 记录 32 个漂移/越界用例全部被拒或按预期通过、0 失败，并证明 `--apply` 只改 manifest 与 lockfile、内核与产品契约零改动；最终 `knix.img` sha256 `cffcc1da…` / 1,259,274,240 bytes，receipt checksum `57066bc1…`，`baseline.sha256 d8602a0b…` 不变 |
| P9 | 完成 | `session.json.resilience` 与 `manifest.json.graphics.resilience` 声明 guest 自有 systemd 的 `on-failure` 恢复（2s 退避、30s 内最多 5 次）；`assemble-native.sh` 把四条 directive 写进 unit heredoc 并逐项验收；`verify-graphics.sh` 在首轮渲染验收后用 `systemctl kill --signal=SIGKILL` 杀死合成器，等待新 `MainPID` 并重新校验 knix 用户 / 3 个 libinput 设备 / Virtual-1 / GBM / GL / foot 窗口，输出 `RESILIENCE VERIFY OK` 才允许写 receipt；receipt 新增 `graphics.session.resilience.verified`。I21、CI heredoc 逐行精确匹配与 `verify-repro.py` 拦截契约漂移，`p9-resilience-drift.log` 20 个越界用例 0 失败。P8 工具首次真实复用：`--propose` 检出 2 个 rootfs 字段 + 4 个输入哈希漂移、`--apply --reason` 刷新后 `--check` 全绿。rootfs 重建为 `de2760f2…` / 226,909,874 bytes；完整构建 `RESILIENCE VERIFY OK: systemd restarted sway 533 -> 642`，最终 `knix.img` sha256 `cbd92c2e…` / 1,259,339,776 bytes，receipt checksum `3151cfbd…`，`baseline.sha256 99b8a18c…`；`GPU=none` 仍保持 inactive / 串口 + SSH 降级 |

## 顺序约束

- P0 的模块目录是 P1 的输入；P1 完成后 `knix.img` 才脱离 seed 的用户态。
- P2 依赖 P1 的单镜像；P3 依赖 P2 之后的常规 UEFI 启动路径。
- P4 贯穿全部阶段：每次新增构建输入都要同步 `platform/image/manifest.json`、I15 与 I16；重跑 `build-rootfs.sh --apply` 后必须把新的 `package-versions.txt` 与全部摘要一起刷新到基线，否则 `build.sh --build` 会拒绝组装。
- P5 不改变发布面，也不放宽 I14；新增后端必须同步 `platform/contract.json`、两个后端 README、I17 与 CI 门禁，且不得进入 `manifest.json.inputs`。
- P6 依赖 P3 的 DRM 渲染与 P4 的锁定基线；`session.json` 是 rootfs 输入，改动它必须重跑 `build-rootfs.sh --apply` 并刷新 `build_baseline` 与全部输入哈希。
- P7 依赖 P6 的 seatd 会话：自启只能由 guest 自己的 systemd target 完成，验收脚本不得启用或启动 unit；`GPU=none` 条件失败路径必须与自启一起复验。
- P8 依赖 P4 的 `build_baseline` 与 17 个输入：任何配方脚本改动都会改变 `recipe_sha256`，必须走 `--propose` / `--apply --reason` 刷新输入哈希并重跑 `build.sh --build` 重写 receipt；刷新只作用于 rootfs 摘要、输入哈希与 lockfile，内核摘要漂移回到 `build-kernel.sh --apply`。
- P9 依赖 P7 的开机自启会话：崩溃恢复只能由 guest 自己的 systemd 完成，验收脚本只负责杀死合成器并等待新 PID，不得自己 `systemctl start` 来伪造恢复；`GPU=none` 的降级路径与重启限流一起成立。

## 不做

- 不出现第二个发布面、第二个 `origin`、tag 或发布分支。
- 不重写 Git 历史。
- 不把宿主状态、私有框架或宿主适配器当作能力来源。
- 不把 `native-apple` 标为已实测，也不为它生成第二个镜像或第二套发布面。
- 不手工编辑 `docs/brand/` 的 PNG，也不绕过生成器改写 logo。
- 不把 `knix.img`、构建盘、rootfs tarball 或其他二进制构建产物提交进仓；文本形式的包版本锁定清单是构建输入，属于仓内真源。
- 不手工编辑 `manifest.json` 的 `build_baseline` / `*_sha256` 输入组，也不让 `refresh-baseline.py` 刷新内核摘要或触碰产品边界字段。
- 不在 CI 里跑 QEMU/HVF 完整构建：seed qcow2 不入库、HVF 只在 macOS 可用；完整构建由本机执行并产出 receipt。
- 不改写 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 与 `totals`。

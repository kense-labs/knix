# Roadmap

`platform/CONTRACT.md` 拥有产品边界。本文件记录镜像从开发验证走向可发布状态的阶段顺序；每阶段的输入、产物与验收都落在仓内脚本与门禁里，产物不入库。

## 阶段

| # | 阶段 | 入口 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| P0 | 修复构建树 | `platform/image/build-kernel.sh --apply` | `Image.gz` · `knix-Image` · `modules/lib/modules/7.2.15` | `.ko` 数量非零；`vmlinux.o` 的 ELF section headers 落在文件内；`sync` 后重新挂载仍完整 |
| P1 | 合并单一镜像 | `platform/image/build.sh --build` | 一个 `knix.img` 与一个 receipt | 镜像内是原生 rootfs、自编译内核与配套模块；receipt 的 sha256 与实际字节一致；`--check` 通过 |
| P2 | 自引导 | `platform/image/assemble-native.sh` · `platform/backends/vm-virt/boot-native.sh` | ESP + systemd-boot + 自有 initramfs | 不挂 `-kernel` 即可启动；串口与 SSH 可用；`systemctl --failed` 为 0 |
| P3 | 图形栈 | `platform/identity/session.json` | virtio-gpu 上实际渲染的 Wayland 会话 | sway 可运行、`knix.desktop` 可登录、DRM 设备由 Guest 拥有 |
| P4 | 可复现与 CI | `.github/workflows/knix-contract.yml` | 锁定输入哈希与完整构建 | CI 校验全部输入与脚本语法，并执行一次 guest 内完整构建 |
| P5 | `native-apple` | `platform/backends/native-apple/` | 第二个启动后端 | 通过 I14；身份、镜像、认证、界面、输入与设备语义仍留在 Guest |

## 状态

| # | 状态 | 依据 |
| --- | --- | --- |
| P0 | 完成 | 全量重编产出 770 个 `.ko` 与 `modules.dep`；原生盘直启后 `modprobe br_netfilter` 成功，无 vermagic / unknown symbol 错误；`/boot/vmlinuz-7.2.15-knix` 与 `kernel-build.json` 的 sha256 一致 |
| P1 | 完成 | `knix.img` 是唯一发布产物：原生 rootfs + 自编译 7.2.15 内核 + 770 个 `.ko`；receipt 记录内核 `config_sha256`、rootfs tarball 与全部输入摘要；`build.sh --build` 组装 + 冷启复验通过，`boot-native.sh` 为启动入口 |
| P2 | 完成 | `knix.img` 为 GPT 双分区：512 MiB FAT32 ESP + ext4 root；ESP 上是 systemd-boot、自编译内核、guest 内生成的 initramfs 与 loader entry。`boot-native.sh` 用干净 EDK2 变量、无 `-kernel`、无 seed ISO 冷启复验通过，receipt 记录 `UEFI self-boot` |
| P3 | 未开始 | headless 环境只校验包集与会话文件 |
| P4 | 部分 | CI 已校验仓内契约、全部 11 个输入哈希、单镜像发布面与 UEFI 自举契约、脚本语法；seed、QEMU/HVF 与镜像构建仍在本机 |
| P5 | 未开始 | 只有契约定义 |

## 顺序约束

- P0 的模块目录是 P1 的输入；P1 完成后 `knix.img` 才脱离 seed 的用户态。
- P2 依赖 P1 的单镜像；P3 依赖 P2 之后的常规 UEFI 启动路径。
- P4 贯穿全部阶段：每次新增构建输入都要同步 `platform/image/manifest.json` 与 I15。
- P5 不改变发布面，也不放宽 I14。

## 不做

- 不出现第二个发布面、第二个 `origin`、tag 或发布分支。
- 不重写 Git 历史。
- 不把宿主状态、私有框架或宿主适配器当作能力来源。
- 不手工编辑 `docs/brand/` 的 PNG，也不绕过生成器改写 logo。
- 不把 `knix.img`、构建盘、rootfs tarball 或其他构建产物提交进仓。
- 不改写 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 与 `totals`。

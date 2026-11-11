# knix

**Knix 组织 43 个公开仓库的源码快照 monorepo —— 不是 43 个可构建项目的合集，而是一份 SHA 锁定、可逐项核对的归档。**

[First principles](#first-principles) · [Architecture](#architecture) · [Invariants](#invariants) · [Operations](#operations) · [Do not](#do-not) · [Known Limitations](#known-limitations-and-deferred-work) · [Repository index](#repository-index)

> [!IMPORTANT]
> **本文件是 AI 编码代理与人类协作者在本仓的共同强制约束。** 开工前先读 First principles、Invariants、Do not 三节。`repos/` 是派生产物，禁止手工改动后不回写 `repos.json`。

> [!NOTE]
> **单一真源是 [repos.json](repos.json)**。43 条记录的 `snapshot_sha` / `snapshot_files` / `snapshot_bytes` / `local_path` 以它为准；本页与架构图都是派生视图，数字冲突时一律以 `repos.json` 为准。

> [!TIP]
> 任何改动后先跑 `python3 scripts/verify-knix.py`，全部通过再提交。不要用肉眼判断快照是否完整。

## First principles

协作的第一性原理：**可信度来自「真源唯一、变更路径唯一、产物可复核」**，而不是来自收录了多少文件。

| Knix 是 | Knix 不是 |
| --- | --- |
| 43 个仓库默认分支在同一时刻的源码快照 | 43 个可独立 clone / build 的活仓库 |
| 以 `repos.json` 为机器真源的归档与检索基线 | 上游的 fork、镜像或新的上游 |
| 一次采集、一次 SHA 锁定、一条 `main` 发布线 | 带上游历史、tag、其他分支的完整镜像 |
| 人与 AI agent 共同遵守的协作契约 | 可随手改写的介绍文案 |
| 品牌统一为 Knix 的内部参考副本 | 对上游代码的重新许可或重新署名 |

**Truth hierarchy（真理层级，冲突时自上而下裁决）**

1. `repos.json` —— 43 条记录、SHA 锁定、逐仓文件数与字节数；**机器真源**。
2. `scripts/collect-knix.py` —— **唯一被承认的采集 / 刷新机制**；`repos/` 只能由它，或等价且同步回写真源的操作产生。
3. `repos/` —— 43 份浅克隆源码快照；**派生产物**，可删除重建，不承载决策。
4. `README.md`、`docs/knix-architecture.*`、`docs/brand/` —— **派生视图**，随真源更新。
5. `origin/main`（`git@github.com:kense-labs/knix.git`）—— **唯一发布面**。

## Architecture

<p align="center">
  <img src="docs/knix-architecture.svg" width="680" alt="Knix 协作契约与源码快照架构" />
</p>

[SVG 源](docs/knix-architecture.svg) · [PNG 预览](docs/knix-architecture.png)。图纸遵循 Kense OS 纸底规范（`#F8F6F0` / `#141413` / `#4E60FF`、Menlo、开放 V 箭头、`680×470`），标题栏与纸底使用 `../kense` 官方 `mark-dark.png` / `mark-light.png`，以 base64 内嵌保证 SVG 自包含。

一次变更只走一条路径：

1. **Collect** —— 对默认分支执行 `git clone --depth 1 --single-branch --no-tags`，剥离各仓 `.git`，整体替换 `repos/<name>`。
2. **Pin** —— 把 43 个 tip commit 写入 `snapshot_sha`，并记录逐仓文件数与字节数。
3. **Verify** —— `scripts/verify-knix.py` 核对目录、SHA、嵌套 `.git`、单文件上限、统计漂移、品牌残留与 logo 契约。
4. **Publish** —— 单一提交推送 `origin/main`；不新增分支、tag 或 submodule。

## Invariants

| # | 不变量 | 为什么 |
| --- | --- | --- |
| I1 | `repos/` 恰好 43 个目录，名字与 `repos.json` 一一对应 | 清单是唯一真源 |
| I2 | 每条记录有 40 位十六进制 `snapshot_sha` | 快照可复核到具体 commit |
| I3 | `repos/` 内无嵌套 `.git` | 快照不是 submodule，也不是嵌套仓库 |
| I4 | 无超过 95 MB 的单文件 | GitHub 100 MB 硬限制 |
| I5 | `snapshot_files` / `snapshot_bytes` / `totals` 与磁盘一致 | README 与图纸的数字不得漂移 |
| I6 | 上游组织品牌字面（大小写及连字符／下划线变体）残留为 0 | 品牌统一 |
| I7 | 独立 `asahi` 标识符保持原样 | 内核 UAPI `drm/asahi`、设备树 `asahi-kasei,*`、上游仓库名是 ABI 与第三方专有名词，不是品牌词 |
| I8 | `docs/brand/` 的官方 mark 字节与品牌契约一致 | 图标不得手工改色或缩放 |
| I9 | 单一 `main` 分支，无 tag / submodule | 唯一发布面 |

全部由 `python3 scripts/verify-knix.py` 自动断言，脚本只读、不写任何文件。

## Operations

```bash
# 0) 改动前 / 提交前：一条命令核对全部不变量
python3 scripts/verify-knix.py

# 1) 刷新仓库清单（需已登录 gh CLI）；从真实上游重采时用环境变量覆盖组织名
KNIX_ORG=<上游组织名> python3 scripts/collect-knix.py --list

# 2) 按现有真源重采全部快照；或只刷个别仓库
python3 scripts/collect-knix.py
python3 scripts/collect-knix.py --only linux,m1n1
python3 scripts/collect-knix.py --jobs 4

# 3) 重采后把新的 SHA / 文件数 / 字节数回写 repos.json，
#    再由它重新生成本页的统计与 43 行清单（不要手改数字）

# 4) 校验、提交、推送
python3 scripts/verify-knix.py && git add -A && git commit -m "chore: refresh snapshots" && git push origin main
```

脚本在临时目录完成浅克隆与导出，再整体替换 `repos/<name>`；旧快照移入临时 trash 目录而非就地删除，便于回滚。

**回滚**：`git revert <commit>` 或 `git reset --hard <上一个已知良好的 commit>`；`repos/` 可由 `collect-knix.py` 重建，不需要单独备份。

## Do not

- 不要手工增删或重命名 `repos/` 下任何文件而不回写 `repos.json`（含文件名、文件夹名、符号链接名与目标字符串）。
- 不要把上游 `.git` 历史、tag、其他分支塞回快照；`repos/` 永远是普通目录。
- 不要 `git submodule add`，也不要递归拉取上游声明的 submodule。
- 不要改动 `repos/` 内的 `LICENSE` / `COPYING`，不要重新署名或重新授权上游代码。
- 不要替换独立 `asahi` 标识符（I7）：改内核 UAPI 或设备树 compatible 会破坏编译与硬件匹配契约。
- 不要绕过 `../kense` 的品牌生成器手工编辑 `docs/brand/` 的 PNG，也不要仅凭截图调整 logo 尺寸／配色。
- 不要把本页或架构图当作真源去改统计数字；先改 `repos.json`。
- 不要新增发布分支、tag 或第二个 origin。

## Known Limitations and Deferred Work

- **快照边界**：只含默认分支在采集时刻（2026-11-11T02:27:42+00:00）的状态；不含上游提交历史、tag 与其他分支。完整历史约 8.33 GB（`linux` 6.4 GB、`devicetrees` 1.5 GB）被刻意排除，剥离后源码约 1.93 GB。
- **submodule 未递归**：上游声明的 `artwork` / `m1n1` / gitlab mesa 等 submodule 在浅克隆中为空；其中 `artwork`、`m1n1` 已作为独立仓库收录在 `repos/`。少数 logo 符号链接在上游即悬空，本仓只替换其字面，未补全目标文件。
- **URL 已品牌化**：`repos.json` 的 `source_url` / `clone_url` 指向 `github.com/Knix/...`；从真实上游重采必须设置 `KNIX_ORG`。
- **统计是采集时刻值**：147,032 个文件 / 2,076,111,665 字节；重采或任何内容改动后须重算并重新生成本页。
- **品牌替换边界**：复合品牌词已在文件内容、文件名、文件夹名、符号链接名与目标字符串中全量替换；独立 `asahi` 标识符（I7）与二进制资产字节保持不变，二进制资产仅可重命名。
- **Deferred**：可复用的统计／清单生成器与定期重采调度尚未入库；当前 `verify-knix.py` 已覆盖 I1–I9，但清单再生成仍是手工流程。

## Scale and status

| 指标 | 数值 |
| --- | --- |
| 仓库数 | 43 |
| 文件数 | 147,032 |
| 源码总体积 | 1.93 GB（2,076,111,665 字节） |
| 上游完整历史体积（参考） | 8.33 GB |
| 默认分支分布 | asahi: 6, main: 28, master: 9 |
| 已归档 / fork | 14 / 9 |
| 单文件最大值 | < 100 MB（已校验） |
| 采集时间 | 2026-11-11T02:27:42+00:00 |
| 发布面 | `origin/main` · `git@github.com:kense-labs/knix.git` |
| 首次推送包体积 | 411 MiB |

## Layout

```
knix/
├── README.md                        # 本契约：人机协作者的唯一入口
├── repos.json                       # 机器真源：43 条记录 + SHA + 统计
├── scripts/
│   ├── collect-knix.py              # 唯一采集 / 刷新机制
│   └── verify-knix.py               # 可执行的不变量门禁
├── docs/
│   ├── knix-architecture.svg        # 派生视图（Kense OS 风格）
│   ├── knix-architecture.png        # SVG 的 PNG 预览
│   └── brand/                       # 官方 Kense mark（字节不可手改）
└── repos/                           # 派生产物：43 份源码快照
    ├── linux/  ├── u-boot/  ├── m1n1/  ├── .github/  └── ...
```

`repos.json` 每条记录字段：`name`、`source_url`、`clone_url`、`default_branch`、
`snapshot_sha`、`snapshot_at`、`description`、`archived`、`fork`、`license`、
`upstream_pushed_at`、`upstream_size_kb`、`local_path`、`snapshot_files`、`snapshot_bytes`。

## Repository index

下表由 `repos.json` 派生；`体积` 为剥离 `.git` 后的常规文件字节数，不含符号链接自身。

| 仓库 | 默认分支 | 快照 commit | License | 标记 | 体积 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| [`.github`](repos/.github) | `main` | `7aec1bb4d3e9` | NOASSERTION | - | 0.0 MB | GitHub settings for the Knix organization |
| [`alsa-ucm-conf-asahi`](repos/alsa-ucm-conf-asahi) | `master` | `1473329acab5` | BSD-3-Clause | - | 0.0 MB | Asahi overlay with ALSA Use Case Manager configuration |
| [`artwork`](repos/artwork) | `main` | `80d14f8b6f48` | NOASSERTION | - | 2.1 MB | Knix logo and other artwork |
| [`asahi-audio`](repos/asahi-audio) | `main` | `ab1b46928d05` | GPL-2.0 | - | 1.9 MB | Userspace audio for Knix |
| [`asahi-calamares-configs`](repos/asahi-calamares-configs) | `main` | `8c957638e732` | MIT | archived | 0.1 MB | Asahi reference distro Calamares setup configs |
| [`asahi-fedora-remix-logos`](repos/asahi-fedora-remix-logos) | `main` | `e68f9ef8578c` | CC-BY-SA-4.0 | archived | 1.0 MB | Asahi Fedora Remix-related icons and pictures |
| [`asahi-fedora-remix-release`](repos/asahi-fedora-remix-release) | `main` | `2adaa98695b7` | MIT | archived | 0.1 MB | Asahi Fedora Remix release files |
| [`asahi-fedora-remix.org`](repos/asahi-fedora-remix.org) | `main` | `29ef4f318916` | NOASSERTION | archived | 0.0 MB | Web stuff for https://asahi-fedora-remix.org |
| [`asahi-fix27`](repos/asahi-fix27) | `master` | `7718b90e0dbc` | GPL-2.0 | - | 0.0 MB | Fix macOS 27 bootability flag from linux side |
| [`asahi-installer`](repos/asahi-installer) | `main` | `e93743d52178` | MIT | - | 0.2 MB | Knix installer |
| [`asahi-installer-data`](repos/asahi-installer-data) | `main` | `06543833b81f` | MIT | - | 0.0 MB | Knix installer data for alx.sh |
| [`asahi-nvram`](repos/asahi-nvram) | `master` | `3df52c939fbc` | MIT | - | 0.1 MB | Nvram reader/writer for Apple Silicon Macs |
| [`asahi-plymouth`](repos/asahi-plymouth) | `main` | `14f4f7099ad8` | GPL-3.0 | - | 0.1 MB | Plymouth theme for Knix |
| [`asahi-scripts`](repos/asahi-scripts) | `main` | `755b1d19e61d` | MIT | - | 0.0 MB | Miscellaneous admin scripts for the Knix reference distro |
| [`asahi-va-driver`](repos/asahi-va-driver) | `master` | `0e54d318004e` | GPL-3.0 | - | 0.3 MB | - |
| [`knix-keyring`](repos/knix-keyring) | `master` | `a9f10ab8fb1c` | NOASSERTION | archived, fork | 0.0 MB | Knix Keyring |
| [`Knix.github.io`](repos/Knix.github.io) | `main` | `e31339891811` | NOASSERTION | - | 79.5 MB | Knix.org website |
| [`asahistats`](repos/asahistats) | `main` | `633ccdea9506` | MIT | - | 0.0 MB | Knix install stats server |
| [`avd-fw`](repos/avd-fw) | `main` | `c870554fce20` | MIT | - | 0.1 MB | - |
| [`binfmt-dispatcher`](repos/binfmt-dispatcher) | `main` | `c195c9eec48f` | MIT | - | 0.0 MB | Simple dispatcher for binfmt_misc |
| [`calamares`](repos/calamares) | `asahi` | `fe07fccec3ca` | NOASSERTION | archived, fork | 24.7 MB | Distribution-independent installer framework |
| [`dcp-parser`](repos/dcp-parser) | `main` | `363f10217e9f` | NOASSERTION | archived | 0.0 MB | Userspace parser for dcpav properties |
| [`devicetrees`](repos/devicetrees) | `asahi` | `db67c1514f77` | NOASSERTION | archived, fork | 41.0 MB | Knix device trees |
| [`docs`](repos/docs) | `main` | `ed6f9d6ac4ac` | NOASSERTION | - | 5.0 MB | Knix documentation |
| [`fex-emu-rootfs-generator`](repos/fex-emu-rootfs-generator) | `main` | `5ef759a7938a` | MIT | - | 0.0 MB | systemd generator for the FEX RootFS |
| [`gpu`](repos/gpu) | `main` | `3ef620de2129` | NOASSERTION | archived | 0.1 MB | Dissecting the M1's GPU for 3D acceleration |
| [`hidpipe`](repos/hidpipe) | `master` | `f868cf645d7b` | MIT | archived | 0.0 MB | Pass input devices through to virtual machines |
| [`kisd`](repos/kisd) | `main` | `28608c038bc9` | MIT | - | 0.0 MB | Kanzi-in-System / DebugUSB UART support for Linux hosts |
| [`linux`](repos/linux) | `asahi` | `6ba0637d1ed9` | NOASSERTION | fork | 1544.9 MB | Linux kernel source tree |
| [`m1n1`](repos/m1n1) | `main` | `cf37927d6bf4` | GPL-2.0 | - | 5.3 MB | A bootloader and experimentation playground for Apple Sil... |
| [`macvdmtool`](repos/macvdmtool) | `main` | `b22ae51eb43a` | Apache-2.0 | - | 0.0 MB | Apple Silicon to Apple Silicon VDM utility |
| [`mesa-nightly`](repos/mesa-nightly) | `main` | `e468dc8ee292` | NOASSERTION | - | 0.0 MB | packit |
| [`mkinitcpio`](repos/mkinitcpio) | `asahi` | `0e54169d3748` | GPL-2.0 | archived, fork | 0.1 MB | Arch Linux initramfs generation tools |
| [`muvm`](repos/muvm) | `main` | `b761e07b8465` | NOASSERTION | - | 0.3 MB | muvm - run programs from your system in a microVM |
| [`pidcodes.github.com`](repos/pidcodes.github.com) | `master` | `c580d320769f` | NOASSERTION | archived, fork | 0.3 MB | Website for pid.codes |
| [`speakersafetyd`](repos/speakersafetyd) | `main` | `a87e6da56271` | GPL-2.0 | - | 0.1 MB | Rust speaker safety daemon for Knix |
| [`tilecalc`](repos/tilecalc) | `main` | `79d1640b3ec1` | NOASSERTION | archived, fork | 13.9 MB | AGX GPU tiling mystery |
| [`tiny-dfr`](repos/tiny-dfr) | `master` | `a79f2f43e6c6` | MIT | - | 0.2 MB | The most basic dynamic function row daemon possible |
| [`tuxvdmtool`](repos/tuxvdmtool) | `main` | `4f3fed6a8c7b` | Apache-2.0 | - | 0.0 MB | Linux Apple Silicon to Apple Silicon VDM utility |
| [`u-boot`](repos/u-boot) | `asahi` | `b33034a515a0` | NOASSERTION | - | 249.7 MB | "Das U-Boot" Source Tree |
| [`vdmtool`](repos/vdmtool) | `master` | `2723606490ca` | MIT | fork | 0.1 MB | USB-C Power Delivery VDM exploration tool for Arduino |
| [`widevine-installer`](repos/widevine-installer) | `main` | `eab8c668ad3f` | MIT | - | 0.0 MB | Widevine CDM installer for aarch64 systems |
| [`xkeyboard-config`](repos/xkeyboard-config) | `asahi` | `e116438196c1` | NOASSERTION | archived, fork | 8.3 MB | XKB data. |

## License and attribution

各仓库源码版权归各自上游项目及贡献者所有，原有 `LICENSE` / `COPYING` 等许可文件随快照完整保留，
许可标识见 `repos.json` 的 `license` 字段。本仓库仅作归档、检索与内部参考，**不改变也不重新授权**
上游代码的许可；使用前请遵守各仓库自身的许可条款。

架构图中的 K mark 来自 Kense 官方品牌资产，按 `../kense/kstudio/AGENTS.md` 的图标契约原样使用；
本仓仅内嵌已发布的 PNG 字节，不修改其生成器或产物。

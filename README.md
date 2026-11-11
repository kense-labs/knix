# knix

**AsahiLinux 组织全部公开仓库的源码快照集合（monorepo）。**

## 这是什么

- **来源**：[github.com/AsahiLinux](https://github.com/AsahiLinux)，采集时 GitHub API 报告 `public_repos = 43`，本仓库收录 **43 个**。
- **采集方式**：对每个仓库的**默认分支**执行 `git clone --depth 1 --single-branch --no-tags`，保留检出后的源码文件，剥离各仓库自身的 `.git` 元数据。因此 `repos/<name>/` 是**普通目录**，不是 git submodule。
- **采集时间**：2026-11-11T02:27:42+00:00
- **快照精度**：每个目录锁定的 commit 见 [repos.json](repos.json) 的 `snapshot_sha` 字段与下表。
- **为何不保留提交历史**：43 个仓库含完整 git 历史共约 8.3 GB（其中 `linux` 6.4 GB、`devicetrees` 1.5 GB），放进单一仓库会远超 GitHub 建议体积并导致推送失败。剥离历史后源码约 1.9 GB。

## 规模

| 指标 | 数值 |
| --- | --- |
| 仓库数 | 43 |
| 文件数 | 147,032 |
| 源码总体积 | 1.93 GB |
| 上游完整历史体积（参考） | 8.33 GB |
| 默认分支分布 | asahi: 6, main: 28, master: 9 |
| 已归档 / fork | 14 / 9 |
| 单文件最大值 | < 100 MB（已校验，满足 GitHub 单文件限制） |

## 目录结构

```
knix/
├── README.md                        # 本说明
├── repos.json                       # 43 个仓库的完整清单与元数据
├── scripts/
│   └── collect-asahilinux.py        # 可复现的采集 / 刷新脚本
└── repos/
    ├── linux/                       # AsahiLinux/linux 默认分支 asahi 的源码快照
    ├── m1n1/
    ├── .github/                     # 目录名保留上游仓库原名（含隐藏名）
    └── ...                          # 共 43 个目录
```

`repos.json` 每条记录包含：`name`、`source_url`、`clone_url`、`default_branch`、`snapshot_sha`、
`snapshot_at`、`description`、`archived`、`fork`、`license`、`upstream_pushed_at`、
`upstream_size_kb`、`local_path`、`snapshot_files`、`snapshot_bytes`。

## 更新快照

```bash
# 从 GitHub 刷新仓库列表（需已登录 gh CLI），再重采全部快照并更新 SHA
python3 scripts/collect-asahilinux.py --list

# 按现有 repos.json 重采全部快照
python3 scripts/collect-asahilinux.py

# 只刷新个别仓库
python3 scripts/collect-asahilinux.py --only linux,m1n1

# 限制并发
python3 scripts/collect-asahilinux.py --jobs 4
```

脚本在临时目录完成浅克隆与文件导出，再整体替换 `repos/<name>`；旧快照移动到临时
trash 目录而不是就地删除，便于回滚。更新后请自行提交变更。

## 已知边界

- 快照是**默认分支在采集时刻**的状态，包含已归档（archived）仓库，不包含其他分支、tag 与提交历史。
- 部分上游仓库声明了 git submodule（`AsahiLinux.github.io`、`asahi-installer`、`m1n1`、`docs` 指向
  `artwork` / `m1n1`；`mesa-nightly` 指向 gitlab.freedesktop.org 的 mesa）。浅克隆未递归拉取
  submodule，因此这些路径在快照中为空目录；其中 `artwork`、`m1n1` 已作为独立仓库收录在 `repos/` 下。
- 目录名、文件名、`.gitignore`、`.gitmodules`、LICENSE 等一律保持上游原样，未做重写或重新许可。

## 许可与归属

各仓库源码版权归各自上游项目及贡献者所有，原有 `LICENSE` / `COPYING` 等许可文件随快照完整保留，
具体许可标识见 `repos.json` 的 `license` 字段。本仓库仅作归档、检索与内部参考，不改变也不重新授权
上游代码的许可；使用前请遵守各仓库自身的许可条款。

## 仓库清单（43 个）

| 仓库 | 默认分支 | 快照 commit | License | 标记 | 体积 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| [`.github`](repos/.github) | `main` | `7aec1bb4d3e9` | NOASSERTION | - | 0.0 MB | GitHub settings for the AsahiLinux organization |
| [`alsa-ucm-conf-asahi`](repos/alsa-ucm-conf-asahi) | `master` | `1473329acab5` | BSD-3-Clause | - | 0.0 MB | Asahi overlay with ALSA Use Case Manager configuration |
| [`artwork`](repos/artwork) | `main` | `80d14f8b6f48` | NOASSERTION | - | 2.1 MB | Asahi Linux logo and other artwork |
| [`asahi-audio`](repos/asahi-audio) | `main` | `ab1b46928d05` | GPL-2.0 | - | 1.9 MB | Userspace audio for Asahi Linux |
| [`asahi-calamares-configs`](repos/asahi-calamares-configs) | `main` | `8c957638e732` | MIT | archived | 0.1 MB | Asahi reference distro Calamares setup configs |
| [`asahi-fedora-remix-logos`](repos/asahi-fedora-remix-logos) | `main` | `e68f9ef8578c` | CC-BY-SA-4.0 | archived | 1.0 MB | Asahi Fedora Remix-related icons and pictures |
| [`asahi-fedora-remix-release`](repos/asahi-fedora-remix-release) | `main` | `2adaa98695b7` | MIT | archived | 0.1 MB | Asahi Fedora Remix release files |
| [`asahi-fedora-remix.org`](repos/asahi-fedora-remix.org) | `main` | `29ef4f318916` | NOASSERTION | archived | 0.0 MB | Web stuff for https://asahi-fedora-remix.org |
| [`asahi-fix27`](repos/asahi-fix27) | `master` | `7718b90e0dbc` | GPL-2.0 | - | 0.0 MB | Fix macOS 27 bootability flag from linux side |
| [`asahi-installer`](repos/asahi-installer) | `main` | `e93743d52178` | MIT | - | 0.2 MB | Asahi Linux installer |
| [`asahi-installer-data`](repos/asahi-installer-data) | `main` | `06543833b81f` | MIT | - | 0.0 MB | Asahi Linux installer data for alx.sh |
| [`asahi-nvram`](repos/asahi-nvram) | `master` | `3df52c939fbc` | MIT | - | 0.1 MB | Nvram reader/writer for Apple Silicon Macs |
| [`asahi-plymouth`](repos/asahi-plymouth) | `main` | `14f4f7099ad8` | GPL-3.0 | - | 0.1 MB | Plymouth theme for Asahi Linux |
| [`asahi-scripts`](repos/asahi-scripts) | `main` | `755b1d19e61d` | MIT | - | 0.0 MB | Miscellaneous admin scripts for the Asahi Linux reference... |
| [`asahi-va-driver`](repos/asahi-va-driver) | `master` | `0e54d318004e` | GPL-3.0 | - | 0.3 MB | - |
| [`asahilinux-keyring`](repos/asahilinux-keyring) | `master` | `a9f10ab8fb1c` | NOASSERTION | archived, fork | 0.0 MB | Asahi Linux Keyring |
| [`AsahiLinux.github.io`](repos/AsahiLinux.github.io) | `main` | `e31339891811` | NOASSERTION | - | 79.5 MB | AsahiLinux.org website |
| [`asahistats`](repos/asahistats) | `main` | `633ccdea9506` | MIT | - | 0.0 MB | Asahi linux install stats server |
| [`avd-fw`](repos/avd-fw) | `main` | `c870554fce20` | MIT | - | 0.1 MB | - |
| [`binfmt-dispatcher`](repos/binfmt-dispatcher) | `main` | `c195c9eec48f` | MIT | - | 0.0 MB | Simple dispatcher for binfmt_misc |
| [`calamares`](repos/calamares) | `asahi` | `fe07fccec3ca` | NOASSERTION | archived, fork | 24.7 MB | Distribution-independent installer framework |
| [`dcp-parser`](repos/dcp-parser) | `main` | `363f10217e9f` | NOASSERTION | archived | 0.0 MB | Userspace parser for dcpav properties |
| [`devicetrees`](repos/devicetrees) | `asahi` | `db67c1514f77` | NOASSERTION | archived, fork | 41.0 MB | Asahi Linux device trees |
| [`docs`](repos/docs) | `main` | `ed6f9d6ac4ac` | NOASSERTION | - | 5.0 MB | Asahi Linux documentation |
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
| [`speakersafetyd`](repos/speakersafetyd) | `main` | `a87e6da56271` | GPL-2.0 | - | 0.1 MB | Rust speaker safety daemon for Asahi Linux |
| [`tilecalc`](repos/tilecalc) | `main` | `79d1640b3ec1` | NOASSERTION | archived, fork | 13.9 MB | AGX GPU tiling mystery |
| [`tiny-dfr`](repos/tiny-dfr) | `master` | `a79f2f43e6c6` | MIT | - | 0.2 MB | The most basic dynamic function row daemon possible |
| [`tuxvdmtool`](repos/tuxvdmtool) | `main` | `4f3fed6a8c7b` | Apache-2.0 | - | 0.0 MB | Linux Apple Silicon to Apple Silicon VDM utility |
| [`u-boot`](repos/u-boot) | `asahi` | `b33034a515a0` | NOASSERTION | - | 249.7 MB | "Das U-Boot" Source Tree |
| [`vdmtool`](repos/vdmtool) | `master` | `2723606490ca` | MIT | fork | 0.1 MB | USB-C Power Delivery VDM exploration tool for Arduino |
| [`widevine-installer`](repos/widevine-installer) | `main` | `eab8c668ad3f` | MIT | - | 0.0 MB | Widevine CDM installer for aarch64 systems |
| [`xkeyboard-config`](repos/xkeyboard-config) | `asahi` | `e116438196c1` | NOASSERTION | archived, fork | 8.3 MB | XKB data. |

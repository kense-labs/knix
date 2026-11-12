# knix

43 个公开仓库默认分支的源码快照 monorepo，SHA 锁定，单一 `main` 发布面。

[First principles](#first-principles) · [Architecture](#architecture) · [Run backends](#run-backends) · [Invariants](#invariants) · [Operations](#operations) · [Do not](#do-not) · [Known Limitations](#known-limitations-and-deferred-work) · [Scale](#scale-and-status) · [Roadmap](ROADMAP.md)

> [!NOTE]
> 机器真源：[repos.json](repos.json)。

> [!TIP]
> 提交前执行 `python3 scripts/verify-knix.py`。

## First principles

| Knix 是 | Knix 不是 |
| --- | --- |
| 43 份同一时刻的默认分支源码快照 | 43 个可独立 clone / build 的活仓库 |
| 以 `repos.json` 为机器真源的归档基线 | 上游的 fork、镜像或新上游 |
| 一次采集、一次 SHA 锁定、一条 `main` 发布线 | 带上游历史、tag、其他分支的完整镜像 |
| 人与 AI agent 共同遵守的协作契约 | 介绍文案 |
| 品牌统一的内部参考副本 | 对上游代码的重新许可或重新署名 |

**Truth hierarchy**

1. `repos.json`
2. `scripts/collect-knix.py`
3. `repos/`
4. `README.md` · `docs/knix-architecture.*` · `docs/brand/`
5. `origin/main` · `git@github.com:kense-labs/knix.git`

## Architecture

<p align="center">
  <img src="docs/knix-architecture.svg" width="680" alt="Knix architecture" />
</p>

[SVG](docs/knix-architecture.svg) · [PNG](docs/knix-architecture.png)

1. **Collect** — `git clone --depth 1 --single-branch --no-tags`，剥离 `.git`，替换 `repos/<name>`。
2. **Pin** — 写入 43 个 `snapshot_sha` 与逐仓文件数、字节数。
3. **Verify** — `python3 scripts/verify-knix.py`。
4. **Publish** — 单次提交推送 `origin/main`。

## Run backends

| 后端 | 内核基线 | 设备语义 | 机器真源 · 状态 |
| --- | --- | --- | --- |
| `native-apple` | `knix.config` · `CONFIG_ARCH_APPLE` | `m1n1` · `u-boot` · `devicetrees` | `native-apple/backend.json` · contract-only |
| `vm-virt` | `virt.config` | 标准 virtio · QEMU | `vm-virt/backend.json` · verified |

`platform/CONTRACT.md` 拥有产品边界；后端是启动方式，不是能力来源。`platform/host-adapters/` 为非规范资产，可选、默认关闭、不进入镜像。`platform/contract.json` 的 `backend_truth` 定义每个后端的机器真源；`scripts/verify-backends.py` 校验 launch-path 语义、Guest 归属、单一 `knix.img`、引导链的只读快照引用、内核基线哈希与未实测状态。

| 规则 | 内容 |
| --- | --- |
| 单一身份 | 一个 `knix.img`、一个 `origin/main`、一套品牌 |
| 单一真源 | `repos.json` 的 43 条、SHA 与统计不随后端改变 |
| 能力 | 宿主只提供标准 virtio；私有扩展可关闭且有降级路径 |
| 归属 | 认证、界面、输入与设备语义留在 Guest |

`platform/identity/` 拥有名称、版本、内核基线、包集、会话与品牌字节；`platform/image/build.sh` 是镜像构建入口，`platform/backends/vm-virt/boot-native.sh` 是启动入口。镜像本身是派生产物，不入库。

## Invariants

| # | 不变量 |
| --- | --- |
| I1 | `repos/` 恰好 43 个目录，与 `repos.json` 的 `name` 一一对应 |
| I2 | 每条 `snapshot_sha` 为 40 位十六进制 |
| I3 | `repos/` 内无嵌套 `.git` |
| I4 | 无超过 95 MB 的单文件 |
| I5 | `snapshot_files` / `snapshot_bytes` / `totals` 与磁盘一致 |
| I6 | 全仓无上游品牌与代号字面（含二进制元数据） |
| I7 | 内核 UAPI、设备树 compatible 与仓库名标识符统一为 Knix 命名 |
| I8 | `docs/brand/` 官方 mark 字节与品牌契约一致 |
| I9 | 单一 `main` 分支，无 tag，无启用 submodule 或嵌套 `.git` |
| I10 | `README.md` 无上游品牌字面与代号残留 |
| I11 | `repos/artwork/logos/` 全部资产由 `docs/brand/` 官方 mark 派生 |
| I12 | `repos/` 内无悬空符号链接 |
| I13 | `README.md` 无自指元叙述与解释性说明 |
| I14 | 运行后端不越产品边界；宿主适配器不进入镜像与发布清单 |
| I15 | 身份、镜像清单与品牌哈希与全部构建输入一致 |
| I16 | 内核与 rootfs 的构建基线锁定在仓内；`verify-repro.py` 可解析，仓内无二进制构建产物 |
| I17 | 每个运行后端都有机器真源；`verify-backends.py` 校验后端只是启动路径，Guest 拥有全部能力 |
| I18 | 图形会话以 `knix` 非 root 用户经 guest 的 seatd 运行；`drm,libinput` 双 backend 与真实输入设备由验收硬校验 |
| I19 | `knix-session.service` 由 guest 自己的 `multi-user.target` 开机启用；验收在任何脚本触碰 unit 之前证明它已 active，`GPU=none` 仍是完整降级路径 |
| I20 | 锁定基线只能经 `refresh-baseline.py` 的提案 + 理由 + 字段级 diff 刷新；内核漂移不在此脚本范围内，CI 用策略自测验证 |
| I21 | 崩溃的 sway 由 guest 自己的 systemd 恢复；`verify-graphics.sh` 故意 SIGKILL 后要求新 PID、knix 用户、DRM/libinput 与 Wayland 客户端全部回来 |
| I22 | 桌面必须真的画出像素：guest 自选颜色并由 sway 绘制，主机只用 QMP 读取 virtio-gpu 帧缓冲，9/9 采样与 swaybar 像素都匹配才允许写 receipt |
| I23 | 合成器连续崩溃时由 guest systemd 的 start limit 停止重启；验收要求 `ActiveState=failed`、零 sway 进程、恰好 1 个失败单元、串口 + SSH 可用，随后由 guest 自己恢复完整会话 |
| I24 | 失败的合成器由 guest 自己的 `OnFailure=` 单元写出诊断报告与崩溃时间线；报告只来自 guest 的 unit 状态、journal 与本地设备节点，receipt 记录 guest 上报的崩溃事件数 |

## Operations

```bash
python3 scripts/verify-knix.py
python3 scripts/verify-repro.py
python3 scripts/verify-backends.py
python3 scripts/refresh-baseline.py --check
python3 scripts/refresh-baseline.py --self-test
python3 platform/image/verify-framebuffer.py --self-test

python3 scripts/collect-knix.py --list
python3 scripts/collect-knix.py
python3 scripts/collect-knix.py --only linux,m1n1
python3 scripts/collect-knix.py --jobs 4

platform/image/build.sh --check
platform/image/build.sh --build
platform/image/sign.sh

# inside the arm64 Linux build guest; scripts are streamed over ssh
platform/image/build-kernel.sh --check
platform/image/build-kernel.sh --apply
platform/image/build-rootfs.sh --check
platform/image/build-rootfs.sh --apply
platform/image/assemble-native.sh     # requires root
platform/image/verify-graphics.sh     # inside a booted guest, requires root

# launch paths on the host
platform/backends/vm-virt/boot-dev.sh
platform/backends/vm-virt/boot-native.sh

python3 scripts/verify-knix.py && git add -A && git commit -m "chore: refresh snapshots" && git push origin main
```

**回滚**：`git revert <commit>` 或 `git reset --hard <commit>`。

## Do not

- 手工改动 `repos/` 而不回写 `repos.json`。
- 把上游 `.git` 历史、tag 或其他分支塞回快照。
- `git submodule add` 或递归拉取 submodule。
- 改动 `repos/` 内的 `LICENSE` / `COPYING`。
- 回退或混用旧标识符。
- 手工编辑 `docs/brand/` 的 PNG，或调整 logo 尺寸与配色。
- 绕过 `make-knix-logos.py` 直接改写 `artwork/logos` 的派生资产。
- 运行 `repos/artwork/logos/Makefile` 重新生成 logo。
- 改写统计数字或采集时间。
- 把 `knix.img` 或其他构建产物提交进仓。
- 让宿主适配器参与构建、启动、认证或镜像。
- 把宿主状态当作 knix 的硬件语义。
- 新增发布分支、tag 或第二个 origin。

## Known Limitations and Deferred Work

- 仅默认分支采集时刻状态；不含历史、tag、其他分支。
- 上游 `.gitmodules` 文本原样保留；submodule 未启用，`artwork`、`m1n1` 已作为独立仓库收录。
- `source_url` / `clone_url` 指向 `github.com/Knix/...`；`--list` 拒绝上游命名漂移。
- `docs/brand/` 官方 SVG 保留 `aria-label="KStudio"`；`artwork/logos` 上游 Makefile 不作为生成路径。
- 统计为采集时刻值。
- 内核 UAPI 与设备树 compatible 已重命名；`repos/linux` 已在 arm64 Linux guest 内编译出 7.2.15 内核，其余快照不保证与上游 ABI 对齐或可编译。
- 第三方插画、横幅与启动图保留原始构图；`artwork/logos/` 已由官方 mark 派生。
- `repos/mesa-nightly/mesa` 为未拉取的 submodule，用户态图形栈源码不在快照内。
- `vm-virt` 已跑通 QEMU/HVF 启动、单镜像构建与 UEFI 自举冷启复验；`native-apple` 有机器真源与门禁，裸机启动、外部 ABI 映射、镜像构建与安装流程仍是 deferred。
- 内核在 Linux guest 内原生编译；macOS 宿主只提供标准 virtio，构建工具、源码副本与产物都在仓外构建盘中。
- `knix.img` 是唯一发布产物：`build.sh --build` 在 guest 内把原生 rootfs、自编译 7.2.15 内核、773 个模块、身份与品牌装进新建空白 GPT 盘，seed 只作一次性包源与 helper 引导。receipt 记录镜像 sha256 与内核、rootfs、身份、品牌、`virt.config`、组装脚本与验证脚本的全部输入摘要。
- `knix.img` 自引导：GPT 双分区（512 MiB FAT32 ESP `/dev/vda1` + ext4 root `/dev/vda2），ESP 上 `EFI/BOOT/BOOTAA64.EFI` 是 systemd-boot、`EFI/knix/` 放自编译内核与 guest 内生成的 initramfs，`loader/entries/knix.conf` 提供 `root=/dev/vda2 rw console=ttyAMA0`。`boot-native.sh` 用干净 EDK2 变量副本、不挂 `-kernel` 与 seed ISO，冷启复验通过。
- `knix-native.img` 是 P0 阶段的开发验证盘，已退役，不是发布面；当前启动入口只指向 `knix.img`。
- 构建盘的 `out/vmlinux.o` 曾被早期 VM 强停截断；`build-kernel.sh` 现用 ELF section header 检测并清空重建，已产出 773 个 `.ko` 与 `modules.dep`。启动所需的 virtio、ext4、PL011 与 devtmpfs 仍全部编入内核，挂载根分区不依赖可加载模块。
- `wireless-regdb` 默认指向发行版签名的 `regulatory.db`，与内核内置的上游密钥不匹配；`assemble-native.sh` 把 alternative 切到 `-upstream` 变体，冷启日志已无 `cfg80211` 告警。
- 图形栈已在 guest 内实测：`boot-native.sh` 默认提供 `virtio-gpu-pci` 与 virtio 键鼠，`build-kernel.sh` 额外编入 `virtio-gpu` / `virtio_input` 模块，`assemble-native.sh` 写 `modules-load.d/knix-graphics.conf`。冷启后 `/dev/dri/card0`、`renderD128`、`card0-Virtual-1` 均由 guest 内核拥有；`platform/image/verify-graphics.sh` 在 guest 内经 `knix-session.service` 以 `knix` 用户启动 sway 1.10.1，验证 `Virtual-1` 1280×800 active、GBM buffer 与 GL FBO 分配、`foot` 拿到 Wayland 窗口并提交 `wlr_surface`。`build.sh --build` 把该脚本作为冷启验收的硬门槛，receipt 记录 `graphics.verified`。
- 镜像开机即进入自有桌面：`knix-session.service` 由 guest 自己的 `multi-user.target` 启用（`session.json` 的 `autostart` 契约），不依赖宿主启动器或验收脚本。`assemble-native.sh` 写 `71-knix-virtio-input.rules` 给 virtio 键鼠打 `seat` / `uaccess` 标签、把 knix 加入 `video` / `input` / `render` 组，并建 enablement symlink；unit 用 `WLR_BACKENDS=drm,libinput` 同时加载两个 backend，只写 `drm` 会排除 libinput。`build.sh` 的 guest verify 在任何验证脚本触碰 unit 之前硬校验它已 `enabled` + `active`；`verify-graphics.sh` 只 `systemctl restart` 取干净日志，校验 sway 属 knix、`get_inputs` 返回 QEMU Virtio Keyboard / Tablet、GBM / GL 与 foot 窗口，输出 `AUTOSTART VERIFY OK`。`GPU=none` 时 `ConditionPathExists=/dev/dri/card0` 不满足，串口与 SSH 仍是完整降级路径；receipt 记录 `graphics.session.autostart`。
- 会话 resilience 由 guest 的 systemd 拥有：`knix-session.service` 声明 `Restart=on-failure` / `RestartSec=2` / `StartLimitBurst=5` / `StartLimitIntervalSec=30`。`verify-graphics.sh` 在完成首轮验收后用 `systemctl kill --signal=SIGKILL` 杀死合成器，等待新的 `MainPID`，并重新校验 sway 属 knix、`get_inputs` 含 virtio 键鼠、`Virtual-1` active、GBM buffer / GL FBO、foot 窗口与 `New wlr_surface` 全部来自重启后的进程（unit 的 `ExecStartPre` 每次启动清空日志）；输出 `RESILIENCE VERIFY OK` 后才允许 `build.sh --build` 写 receipt，receipt 记录 `graphics.session.resilience`。`GPU=none` 时 DRM condition 不满足，unit 保持 inactive，串口与 SSH 仍是完整降级路径。
- 「真有像素」由 guest 自己证明：崩溃恢复后的 sway 从 `/dev/urandom` 选一个 24 位颜色（每通道抬到 `0x40..0xff`，避免黑屏误判），用 `output * background #RRGGBB solid_color` 画到标准 virtio-gpu scanout，并把颜色打印在 `guest-graphics.log`。`platform/image/verify-framebuffer.py` 在主机侧只用 QMP `screendump` 导出 P6 PPM 读取帧缓冲，不绘制、不注入、不配置 guest 内容：要求 1280×800、9/9 采样点精确等于 guest 报出的颜色、顶部有 guest swaybar 的非图案像素。`build.sh --build` 输出 `FRAMEBUFFER VERIFY OK` 后才写 receipt，receipt 记录 `graphics.framebuffer`（含 `pattern_sha256`）；CI 用 `--self-test` 在不需要 QEMU 的情况下跑 12 个像素契约用例。
- 崩溃循环同样由 guest 的 systemd 拥有：`StartLimitBurst=5` / `StartLimitIntervalSec=30` 此前只是声明。P11 的 `verify-graphics.sh` 在 P9 单次恢复之后继续故意 SIGKILL，直到 systemd 报出 `Start request repeated too quickly.` 并进入 `failed`；硬校验 `MainPID=0`、零 sway 进程、`NRestarts` 落在限流窗口内、**恰好 1 个失败单元**（unit 自己）、`ssh` active 与 `/dev/ttyAMA0` 串口存在。随后 `reset-failed` + `restart` 是测试清理而非恢复证明（P9 的 SIGKILL 段已独占证明恢复），并要求重启后的 sway 仍属 `knix`、重新提供 3 个 libinput 设备、`Virtual-1` active、GBM / GL 与 foot 窗口；P10 的像素图案由这个恢复后的合成器绘制。`LIMIT VERIFY OK` 是冷启硬门槛，receipt 记录 `graphics.session.crash_loop`。注意 30 秒滑动窗口下 `NRestarts` 可能是 4 或 5，`Result` 始终是 `signal`，门禁不硬编码次数、也不依赖 `start-limit-hit`。
- 崩溃诊断也归 Guest：主 unit 的 `OnFailure=knix-session-failure.service` 让 systemd 在**每一次**失败态（含自动重启前的短暂失败）调用 guest 自有的 oneshot 报告脚本，从 guest 的 unit 状态、journal 与 `/dev` 节点生成 `/run/knix/session-failure.txt`（最新快照）与 `/run/knix/session-failure.history`（崩溃时间线），不读任何宿主状态。报告单元自己必须成功退出，否则会变成第二个失败单元、破坏 P11 的「恰好 1 个失败单元」。`verify-graphics.sh` 在失败态硬校验报告字段（`active_state=failed` / `result=signal` / `exec_main_status=9` / `failed_units=1` / 零 sway / SSH + 串口）、历史至少 2 个事件且最后一条是终态，输出 `DIAGNOSTICS VERIFY OK` 与 `DIAGNOSTICS EVENTS: N`；`build.sh --build` 只有解析到该事件数才写 receipt，receipt 记录 `graphics.session.diagnostics`。`/run` 是运行期快照，持久证据由 guest journal 保存；`GPU=none` 不产生失败态，串口与 SSH 仍是完整降级路径。
- `manifest.json` 的 `build_baseline` 锁定 guest 侧内核与 rootfs 的全部摘要；`platform/image/package-versions.txt` 是 388 个已安装包的版本锁定清单，属于构建输入而非构建产物。`build.sh --build` 在组装前逐字段比对两份摘要与 lockfile 字节，任一漂移即中止并提示刷新基线；receipt 记录 `baseline.sha256` 与 `baseline.verified`。
- receipt 的 `sha256` / `bytes` 只对应构建完成那一刻的 `knix.img`：qcow2 是可写盘，任何后续启动都会写入 journal 与运行期状态。构建完成后不再启动发布镜像；需要额外验收（如 `GPU=none` 降级）时，用 `qemu-img create -b knix.img` 建 overlay 副本启动，并在门禁里复核 backing 镜像字节未变。
- `scripts/verify-repro.py` 是仓内可复现性门禁：校验全部输入哈希、基线字段完整性、lockfile 排序与唯一性、构建阶段脚本可执行、UEFI / 图形 / 帧缓冲契约、receipt 字段，以及 `platform/` / `scripts/` / `docs/` / `.github/` 内没有 `.img` / `.qcow2` / `.zst` / `.ppm` 等二进制构建产物。
- 锁定基线的刷新不是手工编辑 manifest：`scripts/refresh-baseline.py --check` 逐字段比对 guest 摘要与锁定值，`--propose` 生成含 old/new、输入哈希与四份指纹的 `BASELINE-PROPOSAL.json`，`--apply --reason "..."` 只有在提案未过期、字段仍与当前摘要一致、理由不少于 8 字符时才写回 `build_baseline.rootfs` 与 `*_sha256` 输入组（必要时同步 lockfile）。内核摘要漂移一律拒绝，必须回到 `build-kernel.sh --apply`；CI 用 `--self-test` 在没有 guest 产物的情况下跑 12 个策略用例。
- 两个后端的 `backend.json` 不是镜像构建输入：切换后端不改变全部输入哈希、锁定基线与 receipt。`native-apple` 的 `verified` 为 false 且无 `boot_entry`，门禁拒绝它假称完成；两个后端的能力、设备、认证、界面与输入语义都归 Guest。
- CI 校验仓内契约、输入哈希、锁定基线、后端边界、帧缓冲像素契约与脚本语法；seed qcow2、QEMU/HVF 启动、镜像组装、UEFI 冷启、sway 实际渲染与 QMP 抓帧仍在本机执行，CI 不跑完整构建。
- Deferred：能力协商清单、宿主适配器参考实现、统计与清单生成器入库、定期重采调度、增量 diff 报告、多用户登录与空闲策略、诊断的持久化导出与主动告警（报告本身已由 P12 实测）、CI 内完整构建与基线刷新自动发起评审。

## Scale and status

| 指标 | 数值 |
| --- | --- |
| 仓库数 | 43 |
| 文件数 | 147,032 |
| 源码总体积 | 1.94 GB（2,081,410,824 字节） |
| 上游完整历史体积 | 8.33 GB |
| 默认分支 | main 28 · master 9 · 其余 6 |
| 已归档 / fork | 14 / 9 |
| 单文件最大值 | < 100 MB |
| 采集时间 | 2026-11-11T02:27:42+00:00 |
| 发布面 | `origin/main` · `git@github.com:kense-labs/knix.git` |
| 首次推送包体积 | 411 MiB |

## Layout

```
knix/
├── README.md
├── ROADMAP.md
├── repos.json
├── .github/workflows/                # knix-contract.yml
├── scripts/
│   ├── collect-knix.py
│   ├── make-knix-architecture.py
│   ├── make-knix-logos.py
│   ├── verify-knix.py
│   ├── verify-repro.py
│   └── verify-backends.py
├── platform/
│   ├── CONTRACT.md
│   ├── contract.json
│   ├── identity/                     # identity.json · os-release · packages.json · session.json
│   ├── image/                        # build.sh · build-kernel.sh · build-rootfs.sh · assemble-native.sh · verify-graphics.sh · package-versions.txt（锁定基线）· inject-identity.sh · sign.sh · RELEASE.md
│   ├── backends/
│   │   ├── native-apple/             # backend.json（contract-only）· README.md
│   │   └── vm-virt/                  # backend.json（verified）· boot-dev.sh · boot-native.sh
│   └── host-adapters/                # non-normative
├── docs/
│   ├── knix-architecture.svg
│   ├── knix-architecture.png
│   └── brand/
└── repos/                           # 43 dirs
```

完整清单与逐仓元数据：[repos.json](repos.json)。字段：`name`、`source_url`、`clone_url`、`default_branch`、`snapshot_sha`、`snapshot_at`、`description`、`archived`、`fork`、`license`、`upstream_pushed_at`、`upstream_size_kb`、`local_path`、`snapshot_files`、`snapshot_bytes`。

## License and attribution

各仓库源码版权归各自上游项目及贡献者所有，`LICENSE` / `COPYING` 随快照完整保留；许可标识见 `repos.json` 的 `license` 字段。本仓库不改变也不重新授权上游代码的许可。

`repos/artwork/logos/` 的图形由 `docs/brand/` 官方资产派生；上游 `LICENSE` 文本原样保留。

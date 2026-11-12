# knix product boundary

机器可读形式：`platform/contract.json`。执行者：`scripts/verify-knix.py` 的 I14。

## Verdict

移除宿主后，knix 仍能构建、启动、自检、更新与发布。任何使这条不成立的改动都越界。

## Ownership

| 归 knix 所有 | 不归 knix 所有 |
| --- | --- |
| 品牌、内核基线、包集、镜像定义、发布面 | 宿主的窗口策略、手势、状态栏与生物识别 |
| 认证（PAM）、界面渲染、输入语义、设备语义 | 宿主私有框架与宿主地址 |
| `platform/identity/` · `platform/image/` · `platform/backends/` | `platform/host-adapters/` |

`repos.json` 仍是 43 份上游快照的唯一机器真源；`platform/` 是本仓自有代码的真源。两个真源的域不重叠。

## Run backends

| 后端 | 内核基线 | 设备语义 | 状态 |
| --- | --- | --- | --- |
| `native-apple` | `knix.config` · `CONFIG_ARCH_APPLE` | `m1n1` · `u-boot` · `devicetrees` | contract-only |
| `vm-virt` | `virt.config` | 标准 virtio · QEMU | verified |

后端是启动方式，不是能力来源。`platform/contract.json` 的 `backend_truth` 指向每个后端同 schema 的机器真源。

## Backend rules

- 一个 `knix.img`、一个 `origin/main`、一套品牌；后端不改变其中任何一个。
- 后端切换不得改写 `repos.json` 的 `count`、`snapshot_sha`、`snapshot_files`、`snapshot_bytes` 与 `totals`。
- `repos/` 是只读派生产物；`platform/` 不写入 `repos/`。
- 新增后端须同时更新 `platform/contract.json` 的 `backends`、本节的表格与 `platform/backends/<name>/backend.json`。
- 后端真源不是镜像构建输入，不得进入 `platform/image/manifest.json` 的 `inputs`；切换后端不改变 17 个输入哈希、锁定基线与 receipt。
- `unverified_backends` 列出的后端保持 `verified: false`、`status: contract-only`、无 `boot_entry`，并声明 deferred 项；门禁拒绝其假称完成。

## Capability rules

- 宿主只提供标准 virtio 设备；knix 使用标准 Linux 驱动。
- 私有扩展必须可关闭，并声明降级路径；所有 guest 侧 feature 默认关闭。
- 宿主状态不得作为 knix 的硬件语义：网络、电源、显示、音量、输入与认证由 knix 自己的抽象决定。
- 认证权威、界面所有权与输入语义留在 Guest。

## Host adapters

`platform/host-adapters/` 位于仓内仅为版本化方便，属于 non-normative 资产：

- 可选、默认关闭；不参与构建、启动、自检、认证与更新。
- 不进入 `knix.img` 与任何发布清单。
- 删除该目录后，`python3 scripts/verify-knix.py` 与镜像构建仍须通过。
- `deny_in_publishable_code` 中的名字不得出现在可发布代码与配置中。

## ABI

品牌命名不回滚。专有 ABI 冲突由构建期外部映射解决，映射不入库。`vm-virt` 只依赖标准 virtio，不受影响。

## Gate

I14 断言：契约文件与必需条款齐全、README 引用 `platform/CONTRACT.md`、后端目录存在、`repos/` 无宿主命名空间、可发布代码与配置中无禁止名、单一 origin 且与 `platform/contract.json` 一致。

I17 断言：每个声明的后端都有 `backend.json`，`scripts/verify-backends.py` 可解析。该独立门禁校验 launch-path 语义、Guest 对能力/设备/认证/界面/输入的完整归属、单一 `knix.img` 与发布面、引导链的只读快照引用、内核基线哈希、无宿主依赖与私有框架名、后端目录无构建产物，以及未实测后端不得声明 `verified: true`。

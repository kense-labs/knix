# Backend: native-apple

Apple Silicon 裸机启动后端。机器真源 `backend.json`（schema 1）；规则所有者 `platform/contract.json`，执行者 `scripts/verify-backends.py`。

| 项 | 值 |
| --- | --- |
| 状态 | `contract-only` · `verified: false` |
| 类型 | `launch-path` |
| 内核基线 | `repos/linux/arch/arm64/configs/knix.config`（`CONFIG_ARCH_APPLE`、16K 页、Apple SoC 驱动；sha256 锁定在 `backend.json`） |
| 引导链 | `repos/m1n1` · `repos/u-boot` · `repos/devicetrees`（只读快照） |
| 启动入口 | 无（`boot_entry: null`） |
| 降级 | `vm-virt` |

后端只选择启动路径：一个 `knix.img`、一个 `origin/main`、一套品牌不变。能力、设备语义、认证、界面与输入语义归 Guest，`host_adapters` 为 `none`。`knix.config` 与引导链目录都在 `repos/` 快照内，`platform/` 不写入 `repos/`；后端真源不是镜像构建输入，切换后端不改变 19 个输入哈希、锁定基线与 receipt。

已知边界：全仓品牌化改动了专有 UAPI 与设备树 compatible，此路径与上游 ABI 不对齐。兼容由构建期外部映射负责，映射不入库，品牌命名不回滚。

Deferred：Apple Silicon 裸机实测、外部 ABI 映射、镜像构建、安装流程与该后端的可复现构建输入。未实测前 `verified` 保持 false 且不提供 `boot_entry`；门禁拒绝任何提前声明完成或引入宿主依赖的改动。

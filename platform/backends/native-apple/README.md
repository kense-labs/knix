# Backend: native-apple

Apple Silicon 裸机启动后端。内核基线 `repos/linux/arch/arm64/configs/knix.config`（`CONFIG_ARCH_APPLE=y`、16K 页、Apple SoC 驱动），引导链 `m1n1` → `u-boot`，设备描述来自 `devicetrees`。

产品身份由 `platform/identity/` 定义；后端只决定启动路径，不改变 `knix.img` 与 `origin/main`。

已知边界：全仓品牌化改动了专有 UAPI 与设备树 compatible，此路径与上游 ABI 不对齐。兼容由构建期外部映射负责，映射不入库，品牌命名不回滚。

Deferred：镜像构建、安装流程与可复现构建输入。

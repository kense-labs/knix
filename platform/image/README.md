# Image

`knix.img` 的构建入口。构建输入来自 `platform/identity/` 与 `repos/` 的快照，产物只有一个镜像。

`platform/host-adapters/` 不参与构建，也不进入产物。

Deferred：rootfs 工具链、可复现构建输入与签名发布。

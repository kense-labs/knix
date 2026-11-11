# Backend: vm-virt

标准 virtio 虚拟化启动后端。内核基线 `repos/linux/arch/arm64/configs/virt.config`（`# CONFIG_ARCH_APPLE is not set`）；设备语义为 `virtio-blk` / `virtio-net` / `virtio-gpu` / `virtio-input` / `virtio-balloon`，由 QEMU 或兼容 VMM 提供。

只使用标准 compatible，不依赖专有 UAPI，因此不受品牌化改名的 ABI 影响，是当前零冲突的可执行路径。图形加速走标准 Mesa virtio-gpu（virgl / Venus）；宿主侧实现不属于 knix。

产品身份由 `platform/identity/` 定义；后端不改变 `knix.img` 与 `origin/main`。

Deferred：镜像构建、能力协商清单与 `knix check` 后端探测。

# Host adapters

状态：non-normative · 可选 · 默认关闭 · 不进入镜像。

本目录位于仓内仅为版本化方便，不属于 knix 产品。产品边界由 `platform/CONTRACT.md` 拥有，机器规则在 `platform/contract.json` 的 `non_normative` 中列出本目录。

- 不参与构建、启动、自检、认证与更新。
- 不进入 `knix.img` 与任何发布清单。
- 宿主只提供标准 virtio 设备；私有扩展必须可关闭且有降级路径。
- 删除本目录后，knix 仍须能构建、启动、自检、更新与发布。

协议命名空间：`org.knix.host`。该命名空间不得出现在 `repos/` 或任何可发布代码与配置中。

Deferred：宿主适配器的参考实现与能力协商协议。

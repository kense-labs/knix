# Identity

knix 产品身份的单一所有者：品牌、内核基线、包集、镜像定义与发布面。运行后端从此处读取身份；后端不改变身份。边界规则见 `platform/CONTRACT.md`。

| 文件 | 角色 |
| --- | --- |
| `identity.json` | 身份机器真源：版本、内核基线（含内核产物清单与内置驱动约束）、原生 rootfs 定义、镜像与发布面；内核工具链为 arm64 Linux guest 内的原生 gcc + GNU make |
| `os-release` | 写入 `knix.img` 的 `/etc/os-release` 字节 |
| `knix-release` | 写入 `knix.img` 的 `/etc/knix-release` 字节 |
| `brand.manifest.json` | `docs/brand/` 官方资产哈希与安装路径，字节原样复制 |
| `packages.json` | 原生 rootfs 的包集与生成器 |
| `session.json` | Wayland 会话、输入/输出/认证归属，以及 guest 自有 seatd 的 seat 提供者契约：运行用户、组、`drm,libinput` 双 wlroots backend、udev 规则与会话 unit 路径 |

`docs/brand/` 是品牌字节的唯一来源。安装到镜像时只做复制，不重渲染、不缩放、不改配色。`repos.json` 的 43 条、SHA 与统计不属于身份层，不因镜像构建改写。

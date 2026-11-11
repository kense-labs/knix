# knix

43 个公开仓库默认分支的源码快照 monorepo，SHA 锁定，单一 `main` 发布面。

[First principles](#first-principles) · [Architecture](#architecture) · [Invariants](#invariants) · [Operations](#operations) · [Do not](#do-not) · [Known Limitations](#known-limitations-and-deferred-work) · [Scale](#scale-and-status)

> [!IMPORTANT]
> 本文件是 AI 编码代理与人类协作者在本仓的共同强制约束。

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

## Invariants

| # | 不变量 |
| --- | --- |
| I1 | `repos/` 恰好 43 个目录，与 `repos.json` 的 `name` 一一对应 |
| I2 | 每条 `snapshot_sha` 为 40 位十六进制 |
| I3 | `repos/` 内无嵌套 `.git` |
| I4 | 无超过 95 MB 的单文件 |
| I5 | `snapshot_files` / `snapshot_bytes` / `totals` 与磁盘一致 |
| I6 | 上游品牌字面残留为 0 |
| I7 | 内核 UAPI、设备树 compatible 与上游仓库名标识符保持原样 |
| I8 | `docs/brand/` 官方 mark 字节与品牌契约一致 |
| I9 | 单一 `main` 分支，无 tag / submodule |
| I10 | `README.md` 无上游品牌字面与代号残留 |

## Operations

```bash
python3 scripts/verify-knix.py

KNIX_ORG=<上游组织名> python3 scripts/collect-knix.py --list
python3 scripts/collect-knix.py
python3 scripts/collect-knix.py --only linux,m1n1
python3 scripts/collect-knix.py --jobs 4

python3 scripts/verify-knix.py && git add -A && git commit -m "chore: refresh snapshots" && git push origin main
```

**回滚**：`git revert <commit>` 或 `git reset --hard <commit>`。

## Do not

- 手工改动 `repos/` 而不回写 `repos.json`。
- 把上游 `.git` 历史、tag 或其他分支塞回快照。
- `git submodule add` 或递归拉取 submodule。
- 改动 `repos/` 内的 `LICENSE` / `COPYING`。
- 改动内核 UAPI、设备树 compatible 或上游仓库名标识符。
- 手工编辑 `docs/brand/` 的 PNG，或调整 logo 尺寸与配色。
- 在本页或架构图中修改统计数字。
- 新增发布分支、tag 或第二个 origin。

## Known Limitations and Deferred Work

- 仅默认分支采集时刻状态；不含历史、tag、其他分支。
- submodule 未递归；`artwork`、`m1n1` 已作为独立仓库收录；部分 logo 符号链接在上游即悬空。
- `source_url` / `clone_url` 指向 `github.com/Knix/...`；从真实上游重采需设置 `KNIX_ORG`。
- 统计为采集时刻值。
- Deferred：统计与清单生成器入库、定期重采调度、增量 diff 报告。

## Scale and status

| 指标 | 数值 |
| --- | --- |
| 仓库数 | 43 |
| 文件数 | 147,032 |
| 源码总体积 | 1.93 GB（2,076,111,665 字节） |
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
├── repos.json
├── scripts/
│   ├── collect-knix.py
│   └── verify-knix.py
├── docs/
│   ├── knix-architecture.svg
│   ├── knix-architecture.png
│   └── brand/
└── repos/                           # 43 dirs
```

完整清单与逐仓元数据：[repos.json](repos.json)。字段：`name`、`source_url`、`clone_url`、`default_branch`、`snapshot_sha`、`snapshot_at`、`description`、`archived`、`fork`、`license`、`upstream_pushed_at`、`upstream_size_kb`、`local_path`、`snapshot_files`、`snapshot_bytes`。

## License and attribution

各仓库源码版权归各自上游项目及贡献者所有，`LICENSE` / `COPYING` 随快照完整保留；许可标识见 `repos.json` 的 `license` 字段。本仓库不改变也不重新授权上游代码的许可。

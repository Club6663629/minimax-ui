# MiniMax H3 运行环境变更包（git 管理）

本仓库整理 MiniMax H3 视频生成集群（3 生成节点 + 1 超分节点 + 云端 5090 + 宿主机 gpu05）的运行环境变更，
产出 **代码 patch + 环境文档**，纳入 git 管理。

## 目录
```
.
├── README.md                            本文件
├── patches/
│   ├── minimax-ui-local-changes-20260917.patch
│   └── minimax-ui-local-changes-20260926.patch
│                                         minimax-ui 本地修改（vs origin/main，可直接 git apply）
├── env-info/                            仓库之外的运行环境变更（不入 patch，归档于此）
│   ├── 00-环境变更说明.md               后端运行形态 / .env 字段 / 云端节点 / nginx 变更说明
│   ├── server.env.masked                server/.env 脱敏副本（密钥已打码，真实文件不入库）
│   ├── comfyui-cloud-tunnel.service     gpu05 上 systemd 隧道单元（45:8183 → 云端 8188）
│   ├── nginx-minimax-ui.conf            gpu05 上 nginx 站点配置（8080 / /video/ alias 等）
│   ├── diff-nginx.conf.txt / diff-server.env.txt
│                                        2026-09-26 与 2026-09-17 归档的逐项 diff
│   └── minimax-env-对比.md              与 /data/workspace/minimax-env 目录的对比结论
├── docs/
│   ├── README.md                        交付说明
│   ├── MiniMax运行环境-总览.md          集群拓扑 / 版本 / 参数 汇总
│   ├── minimax-ui-本地修改说明.md       已推送补丁（74c010c）逐文件说明（历史）
│   ├── minimax-ui-本地修改说明-20260917.md  2026-09-17 补丁包说明（23 文件 + 环境变更）
│   └── minimax-ui-本地修改说明-20260926.md  2026-09-26 补丁包说明（14 文件 + 环境变更）
│   ├── node45-A100-生成节点.md
│   ├── node51-4090-生成节点.md
│   ├── node246-4090-生成节点.md
│   └── node205-3090-超分节点.md
├── node45/ node51/ node246/ node205/    各节点环境快照（env.txt / 启动脚本 / 模型清单）
└── scripts/collect_node.sh              只读采集脚本
```

## 应用代码 patch
```bash
cd /data/workspace/minimax-ui
git fetch origin
git apply --check ../minimax-env/patches/minimax-ui-local-changes-20260926.patch
git apply         ../minimax-env/patches/minimax-ui-local-changes-20260926.patch
```

> patch 覆盖不到的环境改动（后端启动方式、server/.env 字段、clouds/*.env、
> comfyui-cloud-tunnel 隧道、nginx 配置）见 `env-info/00-环境变更说明.md`，原文归档于 `env-info/`。

## 重新采集某节点环境
```bash
bash scripts/collect_node.sh > nodeXX/env.txt
```

> 节点快照采集时间：2026-09-14；补丁与环境变更归档：2026-09-17 / 2026-09-26。

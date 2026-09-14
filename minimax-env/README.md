# MiniMax H3 运行环境变更包（git 管理）

本仓库整理 MiniMax H3 视频生成集群（3 生成节点 + 1 超分节点）的运行环境变更，
产出 **代码 patch + 环境文档**，纳入 git 管理。

## 目录
```
.
├── README.md                            本文件
├── patches/
│   └── minimax-ui-local-changes.patch   minimax-ui 本地修改（vs origin/main，可直接 git apply）
├── docs/
│   ├── README.md                        交付说明
│   ├── MiniMax运行环境-总览.md          集群拓扑 / 版本 / 参数 汇总
│   ├── minimax-ui-本地修改说明.md       patch 逐文件说明
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
git apply --check ../minimax-env/patches/minimax-ui-local-changes.patch
git apply         ../minimax-env/patches/minimax-ui-local-changes.patch
```

## 重新采集某节点环境
```bash
bash scripts/collect_node.sh > nodeXX/env.txt
```

> 采集时间：2026-09-14；节点清单：45 / 51 / 246（生成） + 205（超分）。

# MiniMax H3 运行环境变更包

本目录是 **MiniMax H3 视频生成集群运行环境变更**的整理产物，纳入 git 管理。

## 交付物
| 路径 | 内容 |
|---|---|
| `patches/minimax-ui-local-changes.patch` | minimax-ui 代码本地修改（相对 GitHub `origin/main`），可直接 `git apply` |
| `node45/` `node51/` `node246/` `node205/` | 各节点环境快照：启动脚本、ComfyUI 版本、模型清单、custom_nodes、端口/参数 |
| `docs/` | 节点环境文档（版本/参数/启动脚本），Markdown |
| `scripts/collect_node.sh` | 只读采集脚本，任何节点上执行即可重新生成 `nodeXX/env.txt` |

## 应用 patch
```bash
cd /data/workspace/minimax-ui
git fetch origin
git apply --check patches/minimax-ui-local-changes.patch   # 试运行
git apply        patches/minimax-ui-local-changes.patch   # 应用
```

## 重新采集环境
```bash
# 在目标节点执行
bash scripts/collect_node.sh > nodeXX/env.txt
```

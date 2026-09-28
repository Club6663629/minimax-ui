# MiniMax H3 运行环境变更包

本目录是 **MiniMax H3 视频生成集群运行环境变更**的整理产物，纳入 git 管理。

## 交付物
| 路径 | 内容 |
|---|---|
| `patches/minimax-ui-local-changes-20260917.patch` | minimax-ui 代码本地修改（相对 GitHub `origin/main`，2026-09-17，23 文件），可直接 `git apply` |
| `patches/minimax-ui-local-changes-20260926.patch` | minimax-ui 代码本地修改（相对 GitHub `origin/main`，2026-09-26，14 文件），可直接 `git apply` |
| `../env-info/` | patch 覆盖不到的环境变更归档：环境变更说明、`server/.env` 脱敏副本、comfyui-cloud-tunnel 隧道单元、gpu05 nginx 配置 |
| `node45/` `node51/` `node246/` `node205/` | 各节点环境快照：启动脚本、ComfyUI 版本、模型清单、custom_nodes、端口/参数 |
| `docs/` | 节点环境文档（版本/参数/启动脚本）与补丁说明，Markdown |
| `scripts/collect_node.sh` | 只读采集脚本，任何节点上执行即可重新生成 `nodeXX/env.txt` |

## 应用 patch
```bash
cd /data/workspace/minimax-ui
git fetch origin
git apply --check ../minimax-env/patches/minimax-ui-local-changes-20260926.patch   # 试运行
git apply        ../minimax-env/patches/minimax-ui-local-changes-20260926.patch   # 应用
```

## 重新采集环境
```bash
# 在目标节点执行
bash scripts/collect_node.sh > nodeXX/env.txt
```

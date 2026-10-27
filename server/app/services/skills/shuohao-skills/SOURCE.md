# shuohao-skills（vendored）

本目录是从 `eternityspring/shuohao-skills` 完整 vendored 进来的 **AI 短剧制作 skill 集合**
（角色设定、改编大纲、场景/道具美术设定、剧本、分镜），保留上游仓库的原始目录结构，
便于单独拷走或在 agent 侧直接识别。

- 上游：https://github.com/eternityspring/shuohao-skills
- 版本：`5612d2a0bd5f19d978c3ee19d1e42840335e3b4b`（main，2026-10-26 提交）
- 许可证：Apache License 2.0，见本目录 `LICENSE` 与 `NOTICE`
  （Copyright 2026 烁皓）
- 拉取方式（可复现；本机 `github.com:443` 直连不通时可用 codeload 归档）：

```bash
curl -L -o /tmp/shuohao-skills.tar.gz \
  https://codeload.github.com/eternityspring/shuohao-skills/tar.gz/5612d2a0bd5f19d978c3ee19d1e42840335e3b4b
tar -xzf /tmp/shuohao-skills.tar.gz -C /tmp
# 将 shuohao-skills-<sha>/ 的全部内容拷入本目录
```

## 收录文件（原样保留）

- `skills/`：7 个 skill，各自含 `SKILL.md`、`README.md`/`README.en.md`、`references/`、
  `examples/`（同时作为自测夹具）与 `scripts/`（Node `.mjs` 实现 + 自测）：
  `character-refs`、`novel-outline`、`novel-characters`、`novel-art`、`novel-script`、
  `novel-storyboard`、`novel-staging`。
- `README.md` / `README.zh.md` / `AGENTS.md` / `CHANGELOG.md`：上游说明与仓库行为规则。
- `assets/`：README 引用的示意图（约 4.5 MB）。
- `docs/`、`scripts/`：上游的迁移方案归档与报告脚本（`report.mjs` 等）。

## 说明

- 上游 skill 的 scripts 依赖 Node.js 运行（非 Python 运行时依赖），本项目后端代码目前不加载
  这些文件；纳入版本管理是为了保留完整原文与可复现的出处。
- 上游自带 `.gitignore` / `.gitattributes`（`* text=auto eol=lf`），仅作用于本目录及其子目录。
- 随上游更新时可整体覆盖本目录（保留本 `SOURCE.md` 并更新版本号）。

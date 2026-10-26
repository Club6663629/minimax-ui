# shuohao-skills（vendored）

剧集工坊的创作内核：剧本 / 角色 / 美术 / 站位 / 分镜五份 JSON 的 schema、质量门与报告，
全部来自这个仓库，本目录只做 vendoring，不改动上游逻辑。

## 来源

- 仓库：https://github.com/eternityspring/shuohao-skills
- 提交：`d28a9c539b40523d081794b25ff9113016bd98e0`（2026-10-15）
- 许可：Apache License 2.0（见同目录 `LICENSE` / `NOTICE`）

## 保留内容

```
skills/<name>/SKILL.md              工作流说明（人读，也是 agent 提示词）
skills/<name>/references/*.md       schema、报告约定、分镜规则
skills/<name>/scripts/*.mjs         门与报告实现
scripts/report.mjs                  五份报告拼多屏壳
```

未 vendor：`assets/` 与 `examples/` 里的图片（不影响门与报告）。
`examples/*.txt` 必须保留：`novel-characters`、`character-refs` 的 selftest 要读原文。

## 本仓库新增

- `studio_bridge.mjs`：给 Python 侧的 JSON 桥。`gates` / `stats` / `render` / `seed` 四个命令，
  读 stdin 的 `{doc, script, cast, art, outline}`，输出 `{ok, gates, problems, stats}`。
  不复制任何门逻辑，只 import 上游导出函数。
- `studio_seed.mjs`：把 staging / storyboard 的确定性生成包成 `seed` 命令。
- `upgrade.sh`：同步上游 + 回填提交号 + 跑自测；升级首选入口。

升级只 rsync 上游的 `skills/` 与 `scripts/`，上述文件不会被覆盖。

## 升级方式

日常升级直接用脚本（自动钉版本、覆盖、回填提交号、跑自测）：

```bash
./server/app/services/skills/shuohao/upgrade.sh <tag-or-sha> [--with-smoke]
```

`--with-smoke` 会额外跑平台侧三个 smoke（需要 `server/venv` 与本地剧本 fixture）。
脚本默认从 GitHub 拉取；镜像/离线复现时覆盖仓库地址：

```bash
SHUOHAO_REPO=file:///path/to/shuohao-skills ./upgrade.sh <tag-or-sha>
```

手工步骤（与脚本一致，便于排查）：先把目标版本钉成 `REF`（优先 release tag；没有 tag 用 commit sha）：

```bash
REF=<tag-or-sha>
REPO=https://github.com/eternityspring/shuohao-skills
WORK=$(mktemp -d)

git init -q "$WORK"
git -C "$WORK" remote add origin "$REPO"
git -C "$WORK" fetch --depth 1 origin "$REF"
git -C "$WORK" checkout -q FETCH_HEAD

# 只同步门/报告/schema 与自测所需文本，图片资产不 vendor
rsync -a --include='*/' --include='*.md' --include='*.mjs' --include='*.json' \
  --include='*.txt' --exclude='*' \
  "$WORK/skills/" server/app/services/skills/shuohao/skills/
rsync -a --include='*/' --include='*.mjs' --exclude='*' \
  "$WORK/scripts/" server/app/services/skills/shuohao/scripts/
cp "$WORK/LICENSE" "$WORK/NOTICE" server/app/services/skills/shuohao/

git -C "$WORK" rev-parse HEAD   # 回填到上面「来源」的提交号与日期
rm -rf "$WORK"
```

> `*.txt` 不能漏：漏了 `novel-characters` 的 selftest 会以
> `ENOENT .../examples/渡口.txt` 失败，而运行时门不受影响，问题很容易被漏到线上。

升级后逐项自测（全部应输出「✓ … 项自测全部通过」）：

```bash
cd server/app/services/skills/shuohao
for s in novel-outline novel-script novel-characters novel-art novel-staging novel-storyboard character-refs; do
  node "skills/$s/scripts/selftest.mjs" || break
done
node scripts/report-selftest.mjs
```

再跑平台侧回归（桥 + 关键帧 + 段视频 + 拼接，不连生成节点）：

```bash
cd server
./venv/bin/python ../tmp/studio_keyframe_smoke.py
./venv/bin/python ../tmp/studio_clip_smoke.py
./venv/bin/python ../tmp/studio_render_smoke.py
```

## 契约敏感点

上游改 schema 时，下面几处 Python 侧要跟着看（门只能揭出一部分）：

- `server/app/studio/intake.py`：剧本/美术 Markdown → 六份 JSON 的字段解析
- `server/app/studio/generator.py`：seed 的输入装配、`ep` 映射
- `server/app/studio/prompts.py`：四视图名（`front-full` / `face-front` / `side-full` / `back-full`）与视图规则
- `server/app/services/studio_image.py`：`QWEN_SIZES` 与出图工作流
- `server/app/studio/gates.py`：平台门常量（`MAX_CUTS`、`MAX_SEGMENT_SECONDS`）

改完把本目录随仓库一起提交：`.gitignore` 没有排除它，新环境缺了它就没有门引擎。

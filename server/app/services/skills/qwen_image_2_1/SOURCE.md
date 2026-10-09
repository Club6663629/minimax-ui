# qwen-image-2.1 prompter skill（vendored）

本目录是从上游仓库 vendored 进来的 **Qwen-Image-2.1 提示词改写 skill**，仅用作后端
DeepSeek 提示词增强 agent（`server/app/services/advllm.py`）的 system prompt 知识底座。

- 上游：https://github.com/iamyoki/qwen-image-2.1-skill （子目录 `skills/qwen-image-2-1-prompter`）
- 许可证：Apache License 2.0（见同目录 `LICENSE`）；官方提示词规则版权归 Alibaba Qwen Team。
- 拉取方式（可复现，遵循项目约定用 git sparse-checkout 而非逐文件 raw）：

```bash
git clone --filter=blob:none --sparse https://github.com/iamyoki/qwen-image-2.1-skill /tmp/_qskill
cd /tmp/_qskill && git sparse-checkout set skills/qwen-image-2-1-prompter
# 拷贝 SKILL.md、references/{edit_rules.md,t2i_rules.md,cheat_sheet.md}、LICENSE 到本目录
```

## 收录文件
- `SKILL.md`：意图路由（T2I / Edit 双轨）。
- `references/edit_rules.md`：图像编辑 & 多图合成规则（本项目主用轨：商品参考图 → 广告图）。
- `references/t2i_rules.md`：文生图 8 步框架（兜底知识）。
- `references/cheat_sheet.md`：风格/材质/画幅词表。

## 未收录 / 不执行
- 上游 `scripts/validate_prompt.py`：仅供上游开发者离线/CI 校验，本项目不 vendor、不执行。

## 说明
- 本项目运行时**只加载 Edit 轨**（`SKILL.md` + `references/edit_rules.md` + `references/cheat_sheet.md`），
  并在其前面叠加不可被覆盖的电商商品保真 system 约束（见 `advllm.FIDELITY_SYSTEM`）。
- skill 内容为第三方提示词工程规范，随上游更新可重新拉取覆盖。

# MiniMax-H3 官方 skills（vendored）

本目录是从 MiniMax 官方仓库 vendored 进来的 **H3 提示词/风格 skills**，仅用作后端
视频提示词增强 agent（`server/app/services/videollm.py`）的 system prompt 知识底座。

- 上游：https://github.com/MiniMax-AI/MiniMax-H3 （子目录 `skills/`）
- 版权：官方提示词规范版权归 MiniMax 所有；上游仓库当前**未附带独立 LICENSE 文件**
  （截至 vendor 时仓库根目录与各 skill 目录均无 LICENSE/COPYING），随上游更新可重新拉取覆盖。
- 拉取方式（可复现，遵循项目约定用 git sparse-checkout 而非逐文件 raw）：

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/MiniMax-AI/MiniMax-H3 /tmp/_h3skill
cd /tmp/_h3skill && git sparse-checkout set skills
# 拷贝下列文件到本目录（目录名把中划线改为下划线，作为 Python 包内资源）
```

## 收录文件（scene → skill 映射见 videollm._SCENE_SKILL）
- `h3_prompt_writing/SKILL.md` + `references/base-en.txt` + `references/ref-en.txt`：
  **核心结构 skill**，所有场景都加载。base-en = T2VA/I2VA/FL2VA/L2VA；ref-en = Ref2VA（全参考）。
  定义 H3 输出结构：`integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music`。
- `minimalist_product_ad_generator/SKILL.md`：电商场景创作方向引导。
- `3d_animation_short_generator/SKILL.md`：短剧（叙事动画）场景创作方向引导。
- `music_video_subtitle_generator/SKILL.md`：音乐 MV 场景创作方向引导。

## 未收录 / 不执行
- 各 skill 的 `SKILL.cn.md`、`meta.yaml`、`agents/openai.yaml` 及风格 skill 的 `references/`：
  运行时不需要（中文版仅文档、meta/agents 为 CLI 元数据、风格 skill references 为多阶段制作管线，
  加载会过度约束单次提示词改写）。上游 `skills/` 下其余风格 skill（brand-promo、papercraft、
  co-op-game、paper-collage、handdrawn-live）本项目未接入。

## 说明
- 运行时以 `h3-prompt-writing` 为**权威输出格式**，风格 skill 仅作创作方向引导；单文件/总量均设截断上限
  （见 `videollm._FILE_CAP/_SCENE_CAP/_TOTAL_CAP`）防 token 膨胀。
- 广告场景（ecommerce / advideo）在其前叠加不可被覆盖的系统级商品保真约束
  （见 `videollm.FIDELITY_SYSTEM_VIDEO`）。

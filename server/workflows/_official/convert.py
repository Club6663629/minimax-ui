#!/usr/bin/env python3
"""把 ComfyUI 官方模板（UI 图格式、含子图）转换为本项目使用的 API 格式。

官方来源：Comfy-Org/workflow_templates（main 分支，见同级目录模板 JSON）
  video_minimax_h3_t2v.json                  -> ../t2v_api.json          文生视频
  video_minimax_h3_i2v.json                  -> ../flf2v_api.json        首/尾帧生视频
  video_minimax_h3_r2v.json                  -> ../r2v_api.json          全能参考生视频
  utility_seedvr2_3b_int8_upscale_video.json -> ../upscale_api.json      SeedVR2 3B INT8 视频超分
  utility_seedvr2_video_upscale.json         -> ../upscale_7b_api.json   SeedVR2 7B FP16 视频超分（原生节点）
  utility_seedvr2_7b_int8_upscale_image.json -> ../upscale_7b_image_api.json   图像版（评估对照）
  utility_seedvr2_3b_int8_upscale_image.json -> ../upscale_3b_image_api.json   图像版（评估对照）

转换规则（与 ComfyUI 前端 workflowToPrompt 行为对齐）：
- 部件值取 widgets_values_named，丢弃 upload 等仅 UI 部件
- 插槽存在连线时，同名部件值被连线引用 [源节点ID, 槽位] 覆盖
- 子图实例内联展开：-10 为子图输入节点、-20 为输出节点；
  子图输入按「实例外连线 > 实例部件值」取值，子图输出回填到实例的外连线
- Note 类节点与静音节点不进入 API 格式

旧格式模板（节点用位置式 widgets_values 数组、无 widgets_values_named，如 SeedVR2 超分）：
- 部件名按节点类约定表映射；子图实例按 proxyWidgets 顺序对齐部件值；
  实例无 proxyWidgets 时，退回「未被连线占用的子图输入槽 ↔ 部件值尾段按序对齐」

用法：python3 convert.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent

SKIP_TYPES = {"MarkdownNote", "Note"}
UI_ONLY_WIDGETS = {"upload", "choose file to upload"}

TARGETS = {
    "video_minimax_h3_t2v.json": "t2v_api.json",
    "video_minimax_h3_i2v.json": "flf2v_api.json",
    "video_minimax_h3_r2v.json": "r2v_api.json",
}

LEGACY_TARGETS = {
    "utility_seedvr2_3b_int8_upscale_video.json": "upscale_api.json",
    "utility_seedvr2_video_upscale.json": "upscale_7b_api.json",
    "utility_seedvr2_7b_int8_upscale_image.json": "upscale_7b_image_api.json",
    "utility_seedvr2_3b_int8_upscale_image.json": "upscale_3b_image_api.json",
}

# 旧格式模板各节点类的部件名（与 widgets_values 数组顺序一致）
LEGACY_WIDGET_NAMES = {
    "LoadVideo": ["video", "upload"],
    "LoadImage": ["image", "upload"],
    "SaveVideo": ["filename_prefix", "format", "codec"],
    "SaveImage": ["filename_prefix"],
    "KSampler": ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "VAEEncodeTiled": ["tile_size", "overlap", "temp_size", "temp_overlap"],
    "VAEDecodeTiled": ["tile_size", "overlap", "temp_size", "temp_overlap"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "VAELoader": ["vae_name"],
    "ResizeImageMaskNode": ["resize_type", "resize_type.multiplier", "method"],
    "SeedVR2PostProcessing": ["color_correction_method"],
    "CreateVideo": ["fps", "bit_depth"],
    "Video Slice": ["start_time", "duration", "enabled"],
    "ComfySwitchNode": ["switch"],
    "PrimitiveBoolean": ["value"],
    "SeedVR2TemporalChunk": ["frame_batch_size", "mode"],
    "ImageScale": ["upscale_method", "width", "height", "crop"],
    "ImageFromBatch": ["batch_index", "length"],
    # ---- SeedVR2 7B 原生节点（utility_seedvr2_video_upscale 模板）----
    "SeedVR2VideoUpscaler": [
        "seed", "control_after_generate", "resolution", "sampling_steps", "overlap_size",
        "adv_encode", "color_correction", "pad_left", "pad_right", "pad_top", "pad_bottom",
        "offload_device", "randomize_seed",
    ],
    "SeedVR2LoadDiTModel": [
        "dit_name", "load_device", "block_swap", "enable_compile", "sage_block", "offload", "attn",
    ],
    "SeedVR2LoadVAEModel": [
        "vae_name", "load_device", "enable_tiling", "tile_height", "tile_overlap",
        "temporal_tiling", "temporal_tile_size", "temporal_tile_overlap",
        "tile_parallelism", "color_fix", "offload",
    ],
}


def norm_links(links):
    """兼容新（对象）旧（数组）两种 links 格式，统一为 (origin, oslot, target, tslot)。"""
    out = []
    for l in links or []:
        if isinstance(l, dict):
            out.append((l["origin_id"], l["origin_slot"], l["target_id"], l["target_slot"]))
        else:
            out.append((l[1], l[2], l[3], l[4]))
    return out


class Converter:
    def __init__(self, wf: dict):
        self.wf = wf
        self.subs = {s["id"]: s for s in (wf.get("definitions") or {}).get("subgraphs", [])}
        self.api: dict = {}
        self._next = 9000
        self._out_src: dict = {}  # 子图实例ID -> {输出槽: (内部源ID, 槽)}

    def new_id(self) -> str:
        self._next += 1
        return str(self._next)

    @staticmethod
    def _active(node: dict) -> bool:
        return node["type"] not in SKIP_TYPES and node.get("mode", 0) in (0, 4)

    def widgets_of(self, node: dict) -> dict:
        w = dict(node.get("widgets_values_named") or {})
        for k in UI_ONLY_WIDGETS:
            w.pop(k, None)
        return w

    def build_inputs(self, node: dict, conns) -> dict:
        inputs = self.widgets_of(node)
        for name, value in conns:  # 连线覆盖同名部件值
            inputs[name] = value
        return inputs

    def resolve_origin(self, origin: int, oslot: int):
        if origin in self._out_src:  # 源是另一个子图实例的输出
            return self._out_src[origin][oslot]
        return [str(origin), oslot]

    def run(self) -> dict:
        by_target = {}
        for o, os_, t, ts in norm_links(self.wf.get("links")):
            by_target.setdefault(t, []).append((ts, o, os_))

        # ① 子图实例内联（先处理，供顶层连线的源解析）
        for node in self.wf["nodes"]:
            if node["type"] in self.subs:
                # 实例外连线的槽位按「实例显示插座表」编号，需按名称映射到子图输入槽
                inst_inputs = {
                    node["inputs"][ts]["name"]: (o, os_)
                    for ts, o, os_ in by_target.get(node["id"], [])
                }
                self.inline_subgraph(node, inst_inputs)

        # ② 顶层普通节点
        for node in self.wf["nodes"]:
            if node["type"] in self.subs or not self._active(node):
                continue
            conns = [
                (node["inputs"][ts]["name"], self.resolve_origin(o, os_))
                for ts, o, os_ in by_target.get(node["id"], [])
            ]
            self.api[str(node["id"])] = {
                "class_type": node["type"],
                "inputs": self.build_inputs(node, conns),
                "_meta": {"title": node.get("title", node["type"])},
            }
        return self.api

    def inline_subgraph(self, inst: dict, inst_inputs: dict) -> None:
        sub = self.subs[inst["type"]]
        remap = {n["id"]: self.new_id() for n in sub["nodes"] if self._active(n)}

        # 子图每个输入槽的取值：实例外连线（按名称对齐） > 实例部件值
        widgets = inst.get("widgets_values_named") or {}
        supply = {}
        for i, sin in enumerate(sub["inputs"]):
            if sin["name"] in inst_inputs:
                o, os_ = inst_inputs[sin["name"]]
                supply[i] = self.resolve_origin(o, os_)
            elif sin["name"] in widgets:
                supply[i] = widgets[sin["name"]]

        links = norm_links(sub.get("links"))
        # 子图输出回填：实例输出槽 -> 内部源
        self._out_src[inst["id"]] = {
            ts: (remap[o], os_) for o, os_, t, ts in links if t == -20 and o in remap
        }

        by_target = {}
        for o, os_, t, ts in links:
            by_target.setdefault(t, []).append((ts, o, os_))
        sockets_of = {n["id"]: [s["name"] for s in n.get("inputs", [])] for n in sub["nodes"]}

        for n in sub["nodes"]:
            if not self._active(n):
                continue
            conns = []
            for ts, o, os_ in by_target.get(n["id"], []):
                name = sockets_of[n["id"]][ts]
                if o == -10:  # 来自子图输入
                    if os_ not in supply:
                        continue  # 未提供的可选输入，回退节点默认值
                    conns.append((name, supply[os_]))
                elif o in remap:
                    conns.append((name, [remap[o], os_]))
            self.api[remap[n["id"]]] = {
                "class_type": n["type"],
                "inputs": self.build_inputs(n, conns),
                "_meta": {"title": n.get("title", n["type"])},
            }


class LegacyConverter:
    """旧格式模板（位置式 widgets_values、数组式 links）的转换器，如 SeedVR2 超分。"""

    def __init__(self, wf: dict):
        self.wf = wf
        self.subs = {s["id"]: s for s in (wf.get("definitions") or {}).get("subgraphs", [])}
        self.api: dict = {}
        self._next = 9000
        self._out_src: dict = {}

    def new_id(self) -> str:
        self._next += 1
        return str(self._next)

    @staticmethod
    def _active(node: dict) -> bool:
        return node["type"] not in SKIP_TYPES and node.get("mode", 0) in (0, 4)

    def widgets_of(self, node: dict) -> dict:
        names = LEGACY_WIDGET_NAMES.get(node["type"], [])
        values = node.get("widgets_values") or []
        if len(values) > len(names):
            raise ValueError(f"节点类 {node['type']} 的 widgets_values 超出约定表，请补充 LEGACY_WIDGET_NAMES")
        w = dict(zip(names, values))
        for k in UI_ONLY_WIDGETS:
            w.pop(k, None)
        return w

    def build_inputs(self, node: dict, conns) -> dict:
        inputs = self.widgets_of(node)
        for name, value in conns:  # 连线覆盖同名部件值（如 UNETLoader.unet_name）
            inputs[name] = value
        return inputs

    def resolve_origin(self, origin: int, oslot: int):
        if origin in self._out_src:
            return self._out_src[origin][oslot]
        return [str(origin), oslot]

    def run(self) -> dict:
        by_target = {}
        for l in self.wf.get("links") or []:
            by_target.setdefault(l[3], []).append((l[4], l[1], l[2]))

        for node in self.wf["nodes"]:
            if node["type"] in self.subs and self._active(node):
                inst_inputs = {
                    node["inputs"][ts]["name"]: (o, os_)
                    for ts, o, os_ in by_target.get(node["id"], [])
                }
                self.inline_subgraph(node, inst_inputs)

        for node in self.wf["nodes"]:
            if node["type"] in self.subs or not self._active(node):
                continue
            conns = [
                (node["inputs"][ts]["name"], self.resolve_origin(o, os_))
                for ts, o, os_ in by_target.get(node["id"], [])
            ]
            self.api[str(node["id"])] = {
                "class_type": node["type"],
                "inputs": self.build_inputs(node, conns),
                "_meta": {"title": node.get("title", node["type"])},
            }
        return self.api

    def inline_subgraph(self, inst: dict, inst_inputs: dict) -> None:
        sub = self.subs[inst["type"]]
        remap = {n["id"]: self.new_id() for n in sub["nodes"] if self._active(n)}

        # 子图输入槽取值：实例外连线 > 实例部件值（按 proxyWidgets 顺序对齐）
        supply = {}
        for i, sin in enumerate(sub["inputs"]):
            if sin["name"] in inst_inputs:
                o, os_ = inst_inputs[sin["name"]]
                supply[i] = self.resolve_origin(o, os_)
        values = inst.get("widgets_values") or []
        proxies = inst.get("properties", {}).get("proxyWidgets") or []
        if proxies:
            for j, proxy in enumerate(proxies):
                if j < len(values):
                    nid, wname = proxy
                    for k, sin in enumerate(sub["inputs"]):
                        if sin["name"] == wname and k not in supply:
                            supply[k] = values[j]
        elif values:
            # 无 proxyWidgets（如 7B FP16 模板）：未被连线占用的输入槽与部件值尾段按序对齐
            free_slots = [k for k in range(len(sub["inputs"])) if k not in supply]
            for k, v in zip(free_slots, values[-len(free_slots):]):
                supply[k] = v

        links = sub.get("links") or []
        self._out_src[inst["id"]] = {
            l["target_slot"]: (remap[l["origin_id"]], l["origin_slot"])
            for l in links
            if l["target_id"] == -20 and l["origin_id"] in remap
        }

        by_target = {}
        for l in links:
            by_target.setdefault(l["target_id"], []).append((l["target_slot"], l["origin_id"], l["origin_slot"]))
        sockets_of = {n["id"]: [s["name"] for s in n.get("inputs", [])] for n in sub["nodes"]}

        for n in sub["nodes"]:
            if not self._active(n):
                continue
            conns = []
            for ts, o, os_ in by_target.get(n["id"], []):
                name = sockets_of[n["id"]][ts]
                if o == -10:
                    if os_ not in supply:
                        continue  # 未提供的可选输入，回退节点默认值
                    conns.append((name, supply[os_]))
                elif o in remap:
                    conns.append((name, [remap[o], os_]))
            self.api[remap[n["id"]]] = {
                "class_type": n["type"],
                "inputs": self.build_inputs(n, conns),
                "_meta": {"title": n.get("title", n["type"])},
            }


def validate(api: dict) -> None:
    """所有连线引用的源节点必须存在。"""
    for nid, node in api.items():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in api, f"节点 {nid} 的 {key} 引用了不存在的节点 {value[0]}"


def main() -> None:
    for src, dst in TARGETS.items():
        wf = json.loads((HERE / src).read_text(encoding="utf-8"))
        api = Converter(wf).run()
        validate(api)
        (OUT / dst).write_text(
            json.dumps(api, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        classes = sorted({n["class_type"] for n in api.values()})
        print(f"{dst}: {len(api)} nodes -> {classes}")

    for src, dst in LEGACY_TARGETS.items():
        wf = json.loads((HERE / src).read_text(encoding="utf-8"))
        api = LegacyConverter(wf).run()
        validate(api)
        (OUT / dst).write_text(
            json.dumps(api, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        classes = sorted({n["class_type"] for n in api.values()})
        print(f"{dst}: {len(api)} nodes -> {classes}")


if __name__ == "__main__":
    main()

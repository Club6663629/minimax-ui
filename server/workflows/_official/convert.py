#!/usr/bin/env python3
"""把 ComfyUI 官方模板（UI 图格式、含子图）转换为本项目使用的 API 格式。

官方来源：Comfy-Org/workflow_templates（main 分支，见同级目录视频模板 JSON）
  video_minimax_h3_t2v.json -> ../t2v_api.json   文生视频
  video_minimax_h3_i2v.json -> ../flf2v_api.json 首/尾帧生视频
  video_minimax_h3_r2v.json -> ../r2v_api.json   全能参考生视频

转换规则（与 ComfyUI 前端 workflowToPrompt 行为对齐）：
- 部件值取 widgets_values_named，丢弃 upload 等仅 UI 部件
- 插槽存在连线时，同名部件值被连线引用 [源节点ID, 槽位] 覆盖
- 子图实例内联展开：-10 为子图输入节点、-20 为输出节点；
  子图输入按「实例外连线 > 实例部件值」取值，子图输出回填到实例的外连线
- Note 类节点与静音节点不进入 API 格式

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


if __name__ == "__main__":
    main()

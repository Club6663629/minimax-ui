"""HiBTV 交接合同：内容版本、文件依赖与当前集清单，不生成创作内容。"""
import hashlib
import json
from pathlib import Path


CONTRACT_VERSION = "hibtv-v2"
COMPOSITION_FIELDS = ("lens", "cameraPosition", "composition", "eyeline", "focus", "stability")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def design(data):
    """人工确认、展示字段不改变图片生产内容。"""
    return {k: v for k, v in (data or {}).items()
            if k not in ("imageConfirmed", "confirmedHash", "hints", "refsPrompts")}


def asset_input(data):
    return digest({"design": design(data), "hints": (data or {}).get("hints", {})})


def stage_data(kind, data):
    # 参考图生产清单另属像素层，不改变角色表本身的创作内容版本。
    if kind == "cast":
        return {k: v for k, v in data.items() if k not in ("refs", "refsPrompts", "imageTasks")}
    return data


def document_inputs(db, project, kind, ep):
    from .hibtv_codex import UPSTREAM, doc_ep
    from .hibtv_jobs import get_doc, jloads

    return {up: digest(stage_data(up, jloads(doc.data, {}))) if doc is not None else "missing"
            for _, up in UPSTREAM.get(kind, [])
            for doc in [get_doc(db, project.id, up, doc_ep(up, ep))]}


def project_input(project):
    return {"episodes": project.episodes, "minutesMin": project.minutes_min,
            "minutesMax": project.minutes_max, "aspectRatio": project.aspect_ratio,
            "genre": getattr(project, "genre", ""), "keeps": project.keeps,
            "source": digest(getattr(project, "novel_text", "") or "")}


def require_document(db, project, kind, ep=0, *, checked=False):
    from .hibtv_jobs import get_doc, jloads
    from .hibtv_codex import doc_ep

    doc = get_doc(db, project.id, kind, doc_ep(kind, ep))
    if doc is None or doc.status != "done":
        raise RuntimeError(f"{kind} 没有当前有效文档，请先完成该阶段")
    gates = jloads(doc.gates, {})
    if gates.get("status") == "fail" or (checked and gates.get("status") != "ok"):
        raise RuntimeError(f"{kind} 的当前版本尚未通过网关校验，请重跑该阶段")
    meta = jloads(doc.meta, {})
    expected = meta.get("inputs")
    if expected is not None and expected != document_inputs(db, project, kind, ep):
        raise RuntimeError(f"{kind} 的上游内容已改变，请重新生成并校验")
    if kind == "staging" and meta.get("sceneImagesHash") is not None:
        from .hibtv_codex import scene_image_materials
        if meta["sceneImagesHash"] != digest(scene_image_materials(db, project, publish=False)):
            raise RuntimeError("场景实图已改变，请重新认图并核验站位")
    if checked and meta.get("contentHash") != digest(jloads(doc.data, {})):
        raise RuntimeError(f"{kind} 的当前内容没有匹配的校验记录，请在网关重新校验")
    if meta.get("projectInput") is not None and meta["projectInput"] != project_input(project):
        raise RuntimeError(f"{kind} 的项目参数或原文已改变，请重新生成")
    return doc


def require_ancestors(db, project, kind, ep):
    from .hibtv_codex import UPSTREAM
    for _, upstream in UPSTREAM.get(kind, []):
        require_document(db, project, upstream, ep)
        require_ancestors(db, project, upstream, ep)


def current_segments(db, project, ep, *, checked=False):
    from .hibtv_jobs import jloads

    doc = require_document(db, project, "storyboard", ep, checked=checked)
    if checked:
        require_ancestors(db, project, "storyboard", ep)
    rows = next((e.get("segments") or [] for e in jloads(doc.data, {}).get("episodes", [])
                 if int(e.get("ep") or 0) == int(ep)), [])
    if not rows:
        raise RuntimeError(f"第 {ep} 集没有当前分镜段")
    return rows


def segment_input(seg):
    """只取创作输入；导入计数、展示标识不参与内容版本。"""
    return {k: v for k, v in seg.items() if k not in (
        "promptSource", "promptSha8", "frameCount", "pictureCount", "manifestSeconds",
        "oversize", "timingMismatch", "active")}


def frame_plan(seg, cut, ratio, lighting=""):
    return {"cut": cut, "blocking": seg.get("blocking", ""),
            "sceneIndex": seg.get("sceneIndex"), "lighting": lighting,
            "aspectRatio": ratio}


def dependencies_valid(items):
    for item in items:
        path = Path(item["path"])
        if not path.is_file() or file_hash(path) != item["sha256"]:
            return False
    return True

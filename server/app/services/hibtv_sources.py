"""定稿剧本与美术设定输入；沿用文档表保存原文，无需表结构迁移。"""
from .hibtv_contracts import digest


def get_sources(db, project):
    from .hibtv_jobs import get_doc, jloads
    doc = get_doc(db, project.id, "inputs", 0)
    return jloads(doc.data, {}) if doc else {}


def save_sources(db, project, script_name, script_text, art_name, art_text):
    from .hibtv_jobs import save_doc, jloads, jdumps
    from .hibtv_codex import write_source
    sources = {"script": {"name": script_name, "text": script_text},
               "art": {"name": art_name, "text": art_text}}
    save_doc(db, project.id, "inputs", 0, sources)
    # 旧列继续承载剧本，避免迁移破坏历史项目；新接口不接收小说。
    project.novel_name, project.novel_text = script_name, script_text
    project.novel_path = write_source(project.id, script_text)
    detected = jloads(project.detected, {})
    detected["sources"] = {key: {"name": row["name"], "sha256": digest(row["text"])}
                           for key, row in sources.items()}
    detected["inputMode"] = "script-art"
    project.detected = jdumps(detected)
    return sources

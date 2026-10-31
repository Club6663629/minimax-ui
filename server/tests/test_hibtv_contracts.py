"""交接回归：当前文档、图片映射、依赖失效与真实路由装配（无外部模型调用）。"""
import asyncio
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import HibtvProject, HibtvAsset, HibtvFrame, HibtvSegment, Task, User
from app.schemas import HibtvDocPatch, HibtvSegmentGenerateIn
from app.routers import hibtv as api
from app.services import hibtv_codex as gateway, hibtv_stages as stages
from app.services import hibtv_assets as assets, hibtv_frames as frames, hibtv_jobs as jobs
from app.services.hibtv_contracts import digest, file_hash, asset_input, segment_input


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def segment(seg_id="E01-01", count=2):
    # 124 帧 = 5.166667秒，先定合法预算，再分配两个镜头。
    cuts = [{"seconds": 2.5 if i == 0 else 2.666667,
             "beats": [i+1, i+1], "size": "close", "camera": "Static Shot",
             "characters": ["C01"], "props": [], "frame": f"特写，阿宁在桌旁，第{i+1}镜",
             "lens": "85mm", "cameraPosition": "阿宁 + 正面", "composition": "三分法",
             "eyeline": "门外", "focus": "面部", "stability": "stable"}
            for i in range(count)]
    h3 = ("How the reference pictures align with the target video — Picture 1 at 0.00; Picture 2 at 2.50.\n"
          "integrated_multimodal_description:\n[Shot 1] Static shot.\n"
          "[Shot 2] At 00:02.500, the camera cuts to <Picture 2>: Static shot; "
          "a woman says in an off-screen voiceover, lips closed <d>[Chinese] 等船的人回来了。</d>.\n"
          "overall_soundscape: Rain.\nnon_diegetic_music: N/A")
    if count == 1:
        cuts[0]["seconds"] = 5.166667
        h3 = ("How the reference pictures align with the target video — Picture 1 at 0.00.\n"
              "integrated_multimodal_description:\n[Shot 1] Static shot.\n"
              "overall_soundscape: Rain.\nnon_diegetic_music: N/A")
    return {"id": seg_id, "sceneIndex": 1, "generationFrames": 124,
            "blocking": "阿宁在桌南侧，门口有六名群演", "cuts": cuts, "h3Prompt": h3}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(jobs, "HIBTV_DIR", self.root / "projects"),
                        patch.object(gateway, "HIBTV_DIR", self.root / "projects"),
                        patch.object(gateway, "GW_MATERIALS_HOST", self.root / "host"),
                        patch.object(gateway, "GW_MATERIALS_MOUNT", self.root / "mount")]
        for item in self.patches:
            item.start()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(email="test@example.test", username="test", password_hash="unused", credits=100000)
        self.db.add(self.user)
        self.db.flush()
        self.project = HibtvProject(user_id=self.user.id, name="等船的人", novel_text="阿宁等到船归来。",
                                    episodes=1, minutes_min=1, minutes_max=2, aspect_ratio="9:16", keeps="")
        self.db.add(self.project)
        self.db.flush()
        self.pid = self.project.id
        from app.services.hibtv_sources import save_sources
        save_sources(self.db, self.project, "剧本.md", self.project.novel_text, "美术.md", "阿宁：蓝色外套；码头：木质栈桥。")
        self.save("outline", {"params": {"episodes": 1, "minutesPerEpisode": 1.5}})
        self.save("cast", {"characters": [{"id": "C01", "name": "阿宁", "image": {"prompt": "character sheet"}}]})
        self.save("art", {"scenes": [{"id": "S01", "name": "码头"}], "props": []})
        self.save("script", {"episodes": [{"ep": 1, "scenes": [{"sceneId": "S01", "lighting": "",
                    "flow": [{"speaker": "VO", "line": "等船的人回来了。"}]}]}]}, 1)
        self.save("staging", {"locations": [], "scenes": []}, 1)
        self.board = {"aspectRatio": "9:16", "promptLang": "en",
                      "episodes": [{"ep": 1, "segments": [segment()]}]}
        self.install_board(self.board)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def save(self, kind, data, ep=0):
        return jobs.save_doc(self.db, self.pid, kind, ep, data,
                             gates={"status": "ok"}, meta={"contentHash": digest(data)})

    def install_board(self, data):
        jobs.sync_segments(self.db, self.project, 1, copy.deepcopy(data))
        stages.sync_frames(self.db, self.project, 1, {}, data["episodes"][0]["segments"])
        self.save("storyboard", data, 1)
        self.db.flush()

    def write_artifact(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(data))
        return path

    def current_seg(self):
        return self.db.query(HibtvSegment).filter_by(seg_id="E01-01").one()

    def uploaded_frames(self):
        for frame in self.db.query(HibtvFrame).filter_by(ep=1).all():
            if jobs.jloads(frame.meta, {}).get("active") is False:
                continue
            image = io.BytesIO()
            Image.new("RGB", (8, 8), "blue").save(image, "PNG")
            asyncio.run(api.upload_frame(self.pid, frame.id,
                        UploadFile(filename="frame.png", file=io.BytesIO(image.getvalue())),
                        user=self.user, db=self.db))

    def asset(self, kind, code, *, data=None, views=()):
        data = data or {"image": {"prompt": "one clean image"}}
        asset = HibtvAsset(project_id=self.pid, kind=kind, code=code, name=code,
                           data=dump(data), views="{}", hidden=False)
        self.db.add(asset)
        self.db.flush()
        path = jobs.project_dir(self.pid) / f"assets/{code}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(path)
        assets.import_image(self.db, self.project, asset, path,
                            path.relative_to(jobs.project_dir(self.pid)).as_posix())
        assets.confirm_image(self.db, self.project, asset)
        for view in views:
            p = path.with_name(f"{code}-{view}.png")
            Image.new("RGB", (8, 8), "green").save(p)
            assets.import_image(self.db, self.project, asset, p,
                                p.relative_to(jobs.project_dir(self.pid)).as_posix(), view)
            assets.confirm_image(self.db, self.project, asset, view)
        self.db.flush()
        return asset

    def test_edited_staging_is_published_as_new_immutable_snapshot(self):
        first = gateway.upstream_refs(self.db, self.project, "storyboard", 1)["上游站位"]
        doc = jobs.get_doc(self.db, self.pid, "staging", 1)
        doc.data = dump({"blocking": "阿宁改到桌北侧"})
        self.db.flush()
        second = gateway.upstream_refs(self.db, self.project, "storyboard", 1)["上游站位"]
        self.assertNotEqual(first, second)
        def local(remote):
            return gateway.GW_MATERIALS_HOST / Path(remote).relative_to(gateway.GW_MATERIALS_MOUNT)
        self.assertNotIn("桌北侧", local(first).read_text())
        self.assertIn("桌北侧", local(second).read_text())

    def test_cache_changes_with_parameters_and_document_content(self):
        old = gateway.fingerprint(self.project, "storyboard", 1,
                                  upstream_key=gateway.upstream_key(self.db, self.project, "storyboard", 1))
        self.project.aspect_ratio = "16:9"
        new = gateway.fingerprint(self.project, "storyboard", 1,
                                  upstream_key=gateway.upstream_key(self.db, self.project, "storyboard", 1))
        self.assertNotEqual(old, new)
        doc = jobs.get_doc(self.db, self.pid, "staging", 1)
        doc.data = dump({"changed": True})
        self.db.flush()
        self.assertNotEqual(new, gateway.fingerprint(self.project, "storyboard", 1,
                           upstream_key=gateway.upstream_key(self.db, self.project, "storyboard", 1)))

    def test_cast_receives_original_and_script_keeps_ninety_seconds(self):
        self.assertIn("定稿剧本原文：", gateway.task_for(self.db, self.project, "cast", 0))
        self.assertIn("美术设定原文：", gateway.task_for(self.db, self.project, "cast", 0))
        self.assertIn("目标 90 秒", gateway.task_for(self.db, self.project, "script", 1))

    def test_voiceover_import_preserves_original_prompt(self):
        original = self.board["episodes"][0]["segments"][0]["h3Prompt"]
        path = self.write_artifact("voice-storyboard.json", self.board)
        stages.apply(self.db, self.project, "storyboard", 1, {path.name: path})
        actual = jobs.jloads(self.current_seg().data, {})["h3Prompt"]
        self.assertEqual(original, actual)
        self.assertNotIn("hibtv_identity", actual)
        self.assertNotIn("阿宁", actual)

    def test_chinese_prompt_protocol_is_accepted(self):
        board = copy.deepcopy(self.board)
        board["promptLang"] = "zh"
        seg = board["episodes"][0]["segments"][0]
        seg["h3Prompt"] = ("参考图与目标视频的对齐——参考图1对应0.00秒。\n整体视听描述：\n"
                           "[镜头 1] 固定镜头。\n[镜头 2] 于 00:02.500，切换参考图2。\n整体声景：雨。\n非叙事配乐：无。")
        path = self.write_artifact("zh-storyboard.json", board)
        stages.apply(self.db, self.project, "storyboard", 1, {path.name: path})
        self.assertEqual(jobs.jloads(self.current_seg().data, {})["h3Prompt"], seg["h3Prompt"])

    def test_missing_frame_never_falls_back_to_assets_or_partial_frames(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01")
        self.uploaded_frames()
        frame = self.db.query(HibtvFrame).filter_by(cut_index=2).one()
        frame.path = ""
        self.db.flush()
        with self.assertRaisesRegex(RuntimeError, "f2"):
            frames.segment_video_refs(self.db, self.project, self.current_seg())
        self.assertEqual(self.db.query(Task).count(), 0)

    def test_route_submits_complete_frames_in_order_and_exact_budget(self):
        self.uploaded_frames()
        result = api.generate_segment(self.pid, "E01-01", HibtvSegmentGenerateIn(),
                                      user=self.user, db=self.db)
        task = self.db.get(Task, result["taskId"])
        rows = self.db.query(HibtvFrame).order_by(HibtvFrame.cut_index).all()
        self.assertEqual(json.loads(task.ref_image_ids), [r.upload_id for r in rows])
        self.assertEqual((task.mode, task.frames, task.enhance), ("r2v", 124, False))
        self.assertEqual(task.prompt, self.board["episodes"][0]["segments"][0]["h3Prompt"])
        self.assertTrue(jobs.jloads(self.current_seg().versions, [])[0]["inputHash"])

    def test_route_rejects_prompt_override_and_illegal_timing_before_task(self):
        with self.assertRaises(HTTPException):
            api.generate_segment(self.pid, "E01-01", HibtvSegmentGenerateIn(prompt="override"),
                                 user=self.user, db=self.db)
        changed = copy.deepcopy(self.board)
        changed["episodes"][0]["segments"][0]["cuts"][-1]["seconds"] = 2.5
        self.install_board(changed)
        self.uploaded_frames()
        with self.assertRaisesRegex(HTTPException, "合法帧预算"):
            api.generate_segment(self.pid, "E01-01", HibtvSegmentGenerateIn(), user=self.user, db=self.db)
        self.assertEqual(self.db.query(Task).count(), 0)

    def test_changed_frame_or_upstream_cannot_reuse_old_pixels(self):
        self.uploaded_frames()
        self.assertTrue(all(r["ready"] for r in frames.plan(self.db, self.project, "E01-01", 1)))
        api.patch_doc(self.pid, "storyboard", HibtvDocPatch(
            ep=1, path="episodes.0.segments.0.cuts.0.composition", value="中心构图"),
            user=self.user, db=self.db)
        self.assertFalse(frames.plan(self.db, self.project, "E01-01", 1)[0]["ready"])
        self.assertTrue(frames.plan(self.db, self.project, "E01-01", 1)[1]["ready"])

    def test_deleted_frames_and_segments_are_archived_not_consumed(self):
        self.uploaded_frames()
        expanded = copy.deepcopy(self.board)
        expanded["episodes"][0]["segments"].append(segment("E01-02"))
        self.install_board(expanded)
        self.uploaded_frames()
        reduced = copy.deepcopy(self.board)
        reduced["episodes"][0]["segments"] = [segment(count=1)]
        self.install_board(reduced)
        rows = frames.plan(self.db, self.project, "E01-01", 1)
        self.assertEqual([r["cutIndex"] for r in rows], [1])
        self.assertEqual(frames.plan(self.db, self.project, "E01-02", 1), [])
        self.assertEqual(jobs.image_steps(self.db, self.project, {"mode": "frames", "ep": 1}), 1)

    def test_main_regeneration_requires_confirmation_and_invalidates_face(self):
        asset = self.asset("cast", "C01", views=("face-front",))
        self.assertTrue(assets.view_current(self.project, asset, "face-front"))
        data = jobs.jloads(asset.data, {})
        data["image"]["prompt"] = "different costume"
        asset.data = dump(data)
        self.assertFalse(assets.main_current(self.project, asset))
        self.assertFalse(assets.view_current(self.project, asset, "face-front"))
        with self.assertRaises(RuntimeError):
            assets.confirm_image(self.db, self.project, asset)

    def test_required_props_have_priority_over_optional_face_images(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01", views=("face-front",))
        self.asset("cast", "C02", views=("face-front",))
        self.asset("prop", "P01")
        self.asset("prop", "P02")
        frame = self.db.query(HibtvFrame).filter_by(cut_index=1).one()
        meta = jobs.jloads(frame.meta, {})
        meta.update({"characters": ["C01", "C02"], "props": ["P01", "P02"]})
        meta["cut"].update({"characters": meta["characters"], "props": meta["props"]})
        frame.meta = dump(meta)
        refs = frames.ref_specs_for(self.db, self.project, frame, self.current_seg())
        self.assertEqual(len(refs), 6)
        self.assertTrue({"P01", "P02"}.issubset({r["code"] for r in refs}))

    def test_excess_required_references_block_instead_of_truncating(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01")
        for i in range(1, 6):
            self.asset("prop", f"P{i:02d}")
        frame = self.db.query(HibtvFrame).first()
        meta = jobs.jloads(frame.meta, {})
        meta["props"] = [f"P{i:02d}" for i in range(1, 6)]
        meta["cut"]["props"] = meta["props"]
        frame.meta = dump(meta)
        with self.assertRaisesRegex(RuntimeError, "不能裁掉"):
            frames.ref_specs_for(self.db, self.project, frame, self.current_seg())

    def test_side_view_is_actually_selected_for_side_shot(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01", views=("side-full",))
        frame = self.db.query(HibtvFrame).first()
        meta = jobs.jloads(frame.meta, {})
        meta["cut"]["cameraPosition"] = "阿宁 + 侧面"
        frame.meta = dump(meta)
        refs = frames.ref_specs_for(self.db, self.project, frame, self.current_seg())
        self.assertIn("side-full", str(refs[1]["path"]))

    def test_light_and_prop_state_prompts_generate_real_selectable_views(self):
        data = {"image": {"prompt": "closed book"},
                "states": [{"state": "展开", "prompt": "The same book open on white background."}]}
        prop = self.asset("prop", "P01", data=data)
        spec = next(s for s in assets.view_defs("prop", data) if s.get("state") == "展开")
        self.assertEqual(assets.derive_prompt("prop", spec["id"], data), data["states"][0]["prompt"])
        self.assertIsNotNone(assets.view_spec("prop", spec["id"], data))
        self.assertFalse(assets.view_current(self.project, prop, spec["id"]))

    def test_generated_frame_receives_six_composition_fields_and_blocking(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01")
        captured = {}
        async def generate(**kwargs):
            captured.update(kwargs)
            kwargs["out_path"].parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(kwargs["out_path"])
            return {"seed": 1}
        frame = self.db.query(HibtvFrame).filter_by(cut_index=1).one()
        base = frame.prompt
        with patch.object(frames.hibtv_image, "generate_image", side_effect=generate):
            asyncio.run(frames.draw(self.db, self.project, frame))
        for key in ("lens", "cameraPosition", "composition", "eyeline", "focus", "stability"):
            self.assertIn(key + ":", captured["prompt"])
        self.assertIn("六名群演", captured["prompt"])
        self.assertNotIn("恰好有 1", captured["prompt"])
        self.assertEqual(frame.prompt, base)
        self.assertTrue(frames.frame_current(self.db, self.project, frame))

    def test_failed_gateway_validation_never_updates_current_document(self):
        old = jobs.get_doc(self.db, self.pid, "storyboard", 1).data
        path = self.write_artifact("bad-storyboard.json", self.board)
        validation = self.write_artifact("validation.json", {"status": "fail",
            "checkedFiles": {path.name: file_hash(path)}, "problems": ["dialogue mismatch"]})
        with self.assertRaisesRegex(stages.StageError, "dialogue mismatch"):
            stages.apply(self.db, self.project, "storyboard", 1,
                         {path.name: path, validation.name: validation})
        self.assertEqual(jobs.get_doc(self.db, self.pid, "storyboard", 1).data, old)

    def test_validation_for_unrelated_json_is_rejected(self):
        path = self.write_artifact("bad-storyboard.json", self.board)
        other = self.write_artifact("unrelated.json", {"ok": True})
        validation = self.write_artifact("validation.json", {"status": "ok",
                    "checkedFiles": {other.name: file_hash(other)}})
        with self.assertRaisesRegex(stages.StageError, "主产物"):
            stages.apply(self.db, self.project, "storyboard", 1,
                         {p.name: p for p in (path, other, validation)})

    def test_composition_rejects_old_video_after_new_frame_upload(self):
        self.uploaded_frames()
        result = api.generate_segment(self.pid, "E01-01", HibtvSegmentGenerateIn(), user=self.user, db=self.db)
        version = jobs.jloads(self.current_seg().versions, [])[0]
        old = version["inputHash"]
        self.uploaded_frames()
        self.assertNotEqual(old, frames.video_input_hash(self.db, self.project, self.current_seg()))

    def test_reference_stage_tasks_drive_anchor_and_preserve_character_inputs(self):
        cast_before = gateway.upstream_key(self.db, self.project, "art", 0)
        jobs.sync_assets(self.db, self.project, "cast", jobs.jloads(jobs.get_doc(self.db, self.pid, "cast").data, {})["characters"])
        asset_file = self.write_artifact("阿宁_asset.json", {"name": "阿宁", "layers": {"identity": "young woman"}})
        tasks = [{"code": "C01", "view": view, "prompt": f"SKILL {view}: square front, same person, empty hands.",
                  "negative": "text, props", "ratio": "4:5" if view == "face-front" else "2:3",
                  "refs": [] if view == "front-full" else ["front-full"]}
                 for view in ("front-full", "face-front", "side-full", "back-full")]
        task_file = self.write_artifact("refs-prompts.json", {"version": 1, "tasks": tasks})
        stages.apply(self.db, self.project, "refs", 1, {p.name: p for p in (asset_file, task_file)})
        self.assertEqual(cast_before, gateway.upstream_key(self.db, self.project, "art", 0))
        asset = self.db.query(HibtvAsset).filter_by(code="C01").one()
        captured = {}
        async def generate(**kwargs):
            captured.update(kwargs)
            kwargs["out_path"].parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(kwargs["out_path"])
            return {}
        with patch.object(assets.hibtv_image, "generate_image", side_effect=generate):
            asyncio.run(assets.draw(self.db, self.project, asset))
        self.assertEqual(captured["prompt"], tasks[0]["prompt"])
        self.assertEqual((captured["width"], captured["height"]), (832, 1248))
        self.assertIsNone(captured["ref_path"])
        self.assertFalse(assets.main_current(self.project, asset))
        assets.confirm_image(self.db, self.project, asset)
        self.assertTrue(assets.main_current(self.project, asset))

    def test_character_edit_invalidates_old_reference_production_prompts(self):
        row = {"id": "C01", "name": "阿宁", "image": {"prompt": "old costume"}}
        jobs.sync_assets(self.db, self.project, "cast", [row])
        asset = self.db.query(HibtvAsset).filter_by(code="C01").one()
        data = jobs.jloads(asset.data, {})
        data["imageTasks"] = {"front-full": {"prompt": "old derived production"}}
        asset.data = dump(data)
        self.db.flush()
        jobs.sync_assets(self.db, self.project, "cast", [{**row, "image": {"prompt": "new costume"}}])
        self.assertNotIn("imageTasks", jobs.jloads(asset.data, {}))

    def test_legacy_exported_markdown_prompts_are_consumed_without_rewriting(self):
        path = self.root / "refs-prompts.md"
        path.write_text("## 生产环境接入方式\n\n这里是说明，不是角色。\n\n" + "\n".join(f"## 阿宁 / `{view}`\n\n正向提示词：\n```text\nEXACT {view}\n```\n"
                         f"反向词：\n```text\nNO TEXT\n```\n- 比例：`{'4:5' if view == 'face-front' else '2:3'}`\n"
                         for view in ("front-full", "face-front", "side-full", "back-full")))
        result = stages._reference_tasks({path.name: path}, {"阿宁": "C01"})
        self.assertEqual(result["C01"]["face-front"]["prompt"], "EXACT face-front")
        self.assertEqual(result["C01"]["back-full"]["refs"], ["front-full"])

    def test_state_image_is_generated_and_selected_by_storyboard_state(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01")
        data = {"image": {"prompt": "closed book"}, "states": [
            {"state": "合拢", "prompt": "A closed book on white."},
            {"state": "展开", "prompt": "The same book open on white."}]}
        asset = self.asset("prop", "P01", data=data)
        spec = next(v for v in assets.view_defs("prop", data) if v.get("state") == "展开")
        async def generate(**kwargs):
            kwargs["out_path"].parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(kwargs["out_path"])
            self.assertEqual(kwargs["prompt"], data["states"][1]["prompt"])
            self.assertEqual(len(kwargs["ref_paths"]), 1)
            return {}
        with patch.object(assets.hibtv_image, "generate_image", side_effect=generate):
            asyncio.run(assets.draw_view(self.db, self.project, asset, spec["id"]))
        assets.confirm_image(self.db, self.project, asset, spec["id"])
        frame = self.db.query(HibtvFrame).first()
        meta = jobs.jloads(frame.meta, {})
        meta["props"] = ["P01"]
        meta["cut"].update({"props": ["P01"], "propStates": {"P01": "展开"}})
        frame.meta = dump(meta)
        refs = frames.ref_specs_for(self.db, self.project, frame, self.current_seg())
        self.assertEqual(str(refs[-1]["path"]), str(jobs.abs_path(self.pid, jobs.jloads(asset.views, {})[spec["id"]]["path"])))

    def test_scene_variant_references_parent_and_expires_when_parent_changes(self):
        parent = self.asset("scene", "S00")
        child = HibtvAsset(project_id=self.pid, kind="scene", code="S01", name="码头内厅",
                  data=dump({"variantOf": "S00", "image": {"prompt": "same pier with an indoor desk"}}), views="{}", hidden=False)
        self.db.add(child)
        self.db.flush()
        async def generate(**kwargs):
            self.assertEqual(kwargs["ref_path"], jobs.abs_path(self.pid, parent.main_path))
            kwargs["out_path"].parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(kwargs["out_path"])
            return {}
        with patch.object(assets.hibtv_image, "generate_image", side_effect=generate):
            asyncio.run(assets.draw(self.db, self.project, child))
        assets.confirm_image(self.db, self.project, child)
        self.assertTrue(assets.main_current(self.project, child))
        path = jobs.project_dir(self.pid) / "new-parent.png"
        Image.new("RGB", (8, 8), "black").save(path)
        assets.import_image(self.db, self.project, parent, path, "new-parent.png")
        assets.confirm_image(self.db, self.project, parent)
        self.assertFalse(assets.main_current(self.project, child))

    def test_generation_race_cannot_attach_pixels_to_changed_frame(self):
        self.asset("scene", "S01")
        self.asset("cast", "C01")
        frame = self.db.query(HibtvFrame).first()
        async def generate(**kwargs):
            meta = jobs.jloads(frame.meta, {})
            meta["planHash"] = "new-plan"
            frame.meta = dump(meta)
            self.db.flush()
            kwargs["out_path"].parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(kwargs["out_path"])
            return {}
        with patch.object(frames.hibtv_image, "generate_image", side_effect=generate):
            with self.assertRaisesRegex(RuntimeError, "生成期间"):
                asyncio.run(frames.draw(self.db, self.project, frame))
        self.assertFalse(frame.path)

    def test_segment_detail_frame_shape_supports_frontend_actions(self):
        detail = api._segment_detail(self.db, self.project, self.current_seg())
        for row in detail["frames"]:
            self.assertEqual(row["id"], row["frameId"])
            self.assertIn("ep", row)
            self.assertIn("prompt", row)
            self.assertIn("meta", row)
            self.assertIn("ready", row)

    def test_compose_uses_current_manifest_and_rejects_old_video(self):
        from app.models import HibtvJob
        from app.services import hibtv_compose
        self.uploaded_frames()
        api.generate_segment(self.pid, "E01-01", HibtvSegmentGenerateIn(), user=self.user, db=self.db)
        seg = self.current_seg()
        versions = jobs.jloads(seg.versions, [])
        versions[0]["path"] = "segments/current.mp4"
        path = jobs.project_dir(self.pid) / versions[0]["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"current-video")
        versions[0]["sha256"] = file_hash(path)
        seg.versions = dump(versions)
        seg.current_version = 1
        self.db.add(HibtvSegment(project_id=self.pid, ep=1, seg_id="E01-99", idx=99,
                    data=dump({"active": False}), versions=dump([{"v": 1, "path": "old.mp4"}])))
        job = HibtvJob(project_id=self.pid, kind="compose", ep=1, payload="{}")
        self.db.add(job)
        self.db.flush()
        captured = {}
        def compose(**kwargs):
            captured.update(kwargs)
            out = jobs.project_dir(self.pid) / "render/current.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fixture-video")
            return {"path": str(out), "duration_sec": 124/24, "size": 13, "cutlist": "[]", "version": 1}
        with patch.object(hibtv_compose, "render_episode", side_effect=compose):
            asyncio.run(jobs._job_compose(self.db, self.project, job))
        self.assertEqual(len(captured["segment_paths"]), 1)
        self.assertIn("current.mp4", captured["segment_paths"][0])
        self.uploaded_frames()
        with self.assertRaisesRegex(RuntimeError, "旧输入"):
            asyncio.run(jobs._job_compose(self.db, self.project, job))

    def test_staging_reads_confirmed_scene_views_and_expires_after_image_update(self):
        from app.services.hibtv_contracts import require_document
        scene = self.asset("scene", "S01", views=("top",))
        material = gateway.scene_image_materials(self.db, self.project)
        self.assertEqual({r["view"] for r in material}, {"front", "top"})
        path = self.write_artifact("staging.json", {"scenes": [{"ep": 1}], "locations": []})
        stages.apply(self.db, self.project, "staging", 1, {path.name: path})
        self.assertIsNotNone(require_document(self.db, self.project, "staging", 1))
        image = jobs.project_dir(self.pid) / "updated-scene.png"
        Image.new("RGB", (8, 8), "black").save(image)
        assets.import_image(self.db, self.project, scene, image, "updated-scene.png")
        assets.confirm_image(self.db, self.project, scene)
        with self.assertRaisesRegex(RuntimeError, "重新认图"):
            require_document(self.db, self.project, "staging", 1)

    def test_gateway_run_imports_validated_output_and_reuses_identical_input(self):
        from app.models import HibtvJob
        script = {"episodes": [{"ep": 1, "targetSeconds": 90, "scenes": [{"sceneId": "S01", "flow": []}]}]}
        submit = AsyncMock(return_value={"id": "remote-job-1"})
        async def download(jid, directory, **kwargs):
            output = directory / "novel-script.json"
            output.write_text(dump(script))
            validation = directory / "validation.json"
            validation.write_text(dump({"status": "ok", "checkedFiles": {output.name: file_hash(output)}, "problems": []}))
            return [{"path": p.name, "bytes": p.stat().st_size} for p in (output, validation)]
        async def wait(*args, **kwargs):
            return {"status": "done", "artifacts": []}
        job = HibtvJob(project_id=self.pid, kind="script", ep=1, status="running", payload="{}")
        self.db.add(job)
        self.db.commit()
        with patch.object(gateway.gw, "status", return_value={"available": True}), \
             patch.object(gateway.gw, "submit", submit), \
             patch.object(gateway, "_wait", side_effect=wait), \
             patch.object(gateway.gw, "download_all", side_effect=download):
            asyncio.run(gateway.run(self.db, self.project, job))
            job.status = "done"
            self.db.commit()
            next_job = HibtvJob(project_id=self.pid, kind="script", ep=1, status="running", payload="{}")
            self.db.add(next_job)
            self.db.commit()
            asyncio.run(gateway.run(self.db, self.project, next_job))
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(jobs.jloads(jobs.get_doc(self.db, self.pid, "script", 1).data, {}), script)
        text = submit.call_args.kwargs["input"]
        self.assertIn("上游角色表：", text)
        self.assertIn("目标 90 秒", text)
        self.assertIn("snapshots/", text)
        self.assertTrue(jobs.jloads(next_job.usage, {})["reused"])

    def test_cast_prompt_cannot_be_edited_into_an_unused_asset_field(self):
        from app.schemas import HibtvAssetPatch
        cast = self.asset("cast", "C01")
        original = cast.data
        with self.assertRaises(HTTPException) as ctx:
            api.patch_asset(self.pid, cast.id,
                            HibtvAssetPatch(data={"image": {"prompt": "new costume"}}),
                            self.user, self.db)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("角色文档", ctx.exception.detail)
        self.assertEqual(cast.data, original)


    def test_create_requires_both_production_documents(self):
        args = dict(script=UploadFile(filename="剧本.md",file=io.BytesIO(b"script")), art=None,
                    name="new",genre="",episodes=1,minutes_min=2,minutes_max=5,ratio="9:16",style="写实",keeps="",
                    user=self.user,db=self.db)
        with self.assertRaises(HTTPException) as ctx:
            api.create_project(**args)
        self.assertEqual(ctx.exception.status_code,400)
        self.assertEqual(self.db.query(HibtvProject).count(),1)

    def test_create_persists_two_sources_and_gateway_consumes_them(self):
        from app.services.hibtv_sources import get_sources
        result=api.create_project(script=UploadFile(filename="剧本.md",file=io.BytesIO("第一集\n阿宁：等船。".encode())),
            art=UploadFile(filename="美术.md",file=io.BytesIO("阿宁：红色外套".encode())),
            name="新剧",genre="",episodes=1,minutes_min=2,minutes_max=5,ratio="9:16",style="写实",keeps="",user=self.user,db=self.db)
        project=self.db.get(HibtvProject,result["id"])
        self.assertTrue(result["hasSources"])
        self.assertEqual(result["artName"],"美术.md")
        self.assertEqual(result["style"],"写实")
        self.assertEqual(get_sources(self.db,project)["art"]["text"],"阿宁：红色外套")
        text=gateway.task_for(self.db,project,"outline",0)
        self.assertIn("不新增剧情",text)
        self.assertIn("input-art.txt",text)
        self.assertIn("画风锁定",text)
        self.assertIn("写实",text)
        self.assertNotIn("把这部小说改编",text)

    def test_art_only_source_replacement_invalidates_existing_production(self):
        from app.services.hibtv_sources import get_sources,save_sources
        from app.services.hibtv_contracts import project_input,require_document
        doc=jobs.get_doc(self.db,self.pid,"storyboard",1)
        meta=jobs.jloads(doc.meta,{})
        meta["projectInput"]=project_input(self.project);doc.meta=dump(meta)
        old=get_sources(self.db,self.project)
        save_sources(self.db,self.project,old["script"]["name"],old["script"]["text"],"美术.md","新服装")
        with self.assertRaisesRegex(RuntimeError,"原文已改变"):
            require_document(self.db,self.project,"storyboard",1)

    def test_prompt_editor_changes_consumed_cast_task_and_invalidates_images(self):
        from app.schemas import HibtvAssetPatch
        cast=self.asset("cast","C01")
        data=jobs.jloads(cast.data,{})
        data["imageTasks"]={"front-full":{"prompt":"old","negative":"bad","ratio":"2:3","refs":[]}}
        cast.data=dump(data)
        api.patch_asset(self.pid,cast.id,HibtvAssetPatch(data={"mainPrompt":"new blue coat"}),self.user,self.db)
        data=jobs.jloads(cast.data,{})
        self.assertEqual(assets.prompt_of(data),"new blue coat")
        self.assertFalse(data["imageConfirmed"])

    def test_manual_addition_updates_source_document_with_unique_code(self):
        from app.schemas import HibtvAssetPatch
        added=api.add_asset(self.pid,HibtvAssetPatch(name="银钥匙",data={"image":{"prompt":"silver key"}}),"prop",self.user,self.db)
        self.assertEqual(added["code"],"P01")
        doc=jobs.get_doc(self.db,self.pid,"art",0)
        self.assertEqual(jobs.jloads(doc.data,{})["props"][0]["name"],"银钥匙")
        self.assertEqual(jobs.jloads(doc.gates,{})["status"],"stale")

    def test_word_input_extracts_paragraphs_and_table_order(self):
        import zipfile
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w") as z:
            z.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>第一集</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>阿宁：等船。</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        stream.seek(0)
        _,text=api._read_text(UploadFile(filename="剧本.docx",file=stream),"剧本")
        self.assertEqual(text,"第一集\n阿宁：等船。")

    def test_script_import_rejects_rewritten_dialogue(self):
        doc={"episodes":[{"ep":1,"scenes":[{"sceneId":"S01","flow":[{"speaker":"C01","line":"凭空新增的台词"}]}]}]}
        path=self.write_artifact("novel-script.json",doc)
        with self.assertRaisesRegex(stages.StageError,"禁止改写"):
            stages._apply_script(self.db,self.project,1,{path.name:path})
        doc["episodes"][0]["scenes"][0]["flow"][0]["line"]=self.project.novel_text
        path=self.write_artifact("novel-script.json",doc)
        self.assertEqual(stages._apply_script(self.db,self.project,1,{path.name:path})["data"],doc)

    def test_prompt_change_exposes_stale_asset_status(self):
        a=self.asset("scene","S01")
        data=jobs.jloads(a.data,{});data["image"]={"prompt":"changed sky"};a.data=dump(data)
        self.assertEqual(jobs.asset_payload(self.pid,a)["status"],"stale")


if __name__ == "__main__":
    unittest.main()

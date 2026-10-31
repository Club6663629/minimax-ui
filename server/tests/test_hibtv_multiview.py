"""多视图/派生图与关键帧参考图的纯函数校验（不依赖 DB）。

覆盖：VIEW_DEFS 视图口径、派生视图比例、derive_prompt 身份锁/variant、
mark_stale 过期传播、frame_tail 随画幅、prompt_for 参考图用途声明。
"""
import json
import unittest
from types import SimpleNamespace

from app.services import hibtv_assets, hibtv_frames, hibtv_image, hibtv_jobs


class AssetViewDefsTest(unittest.TestCase):
    def test_hibtv_has_its_own_image_workflow(self):
        self.assertEqual(hibtv_image.TEMPLATE_PATH.name, "hibtv_image_api.json")
        self.assertNotIn("advideo", str(hibtv_image.TEMPLATE_PATH))
        workflow = hibtv_image._inject(
            "Use <image1> as scene and <image2> as the same person.",
            ["scene.png", "cast.png"],
            width=768,
            height=1344,
            seed=7,
            prefix="frame",
            negative="no collage",
            text_only=False,
        )
        self.assertEqual(workflow["474"]["inputs"]["images.image_1"], ["470", 0])
        self.assertEqual(workflow["474"]["inputs"]["images.image_2"], ["620", 0])
        self.assertNotIn("product", workflow["491"]["inputs"]["value"].lower())

    def test_cast_view_ids_match_character_refs(self):
        ids = [v["id"] for v in hibtv_assets.view_defs("cast")]
        self.assertEqual(ids, ["front-full", "face-front", "side-full", "back-full"])
        self.assertTrue(hibtv_assets.view_spec("cast", "front-full")["anchor"])
        self.assertFalse(hibtv_assets.view_spec("cast", "face-front")["anchor"])
        self.assertEqual(hibtv_assets.anchor_id("cast"), "front-full")
        self.assertEqual(hibtv_assets.anchor_id("scene"), "front")
        self.assertEqual(hibtv_assets.anchor_id("prop"), "front")

    def test_derived_view_sizes_follow_ratio(self):
        self.assertEqual(hibtv_assets.size_for("cast", "face-front"), hibtv_assets.RATIO_SIZES["4:5"])
        self.assertEqual(hibtv_assets.size_for("cast", "side-full"), hibtv_assets.RATIO_SIZES["2:3"])
        self.assertEqual(hibtv_assets.size_for("scene", "top"), hibtv_assets.RATIO_SIZES["16:9"])
        self.assertEqual(hibtv_assets.size_for("prop", "back"), hibtv_assets.RATIO_SIZES["1:1"])

    def test_anchor_size_matches_type_default(self):
        self.assertEqual(hibtv_assets.size_for("cast", "front-full"), hibtv_assets.size_for("cast"))
        self.assertEqual(hibtv_assets.size_for("scene", "front"), hibtv_assets.size_for("scene"))

    def test_derive_prompt_cast_locks_identity(self):
        task = {"prompt": "Skill-exported exact identity and close-up framing."}
        data = {"imageTasks": {"face-front": task}}
        self.assertEqual(hibtv_assets.derive_prompt("cast", "face-front", data), task["prompt"])
        with self.assertRaisesRegex(RuntimeError, "character-refs"):
            hibtv_assets.derive_prompt("cast", "side-full", data)

    def test_derive_prompt_scene_variant(self):
        p = hibtv_assets.derive_prompt("scene", "top", {})
        self.assertIn("keep structure", p.lower())
        self.assertIn("top-down", p.lower())
        self.assertIn("No people", p)

    def test_derive_prompt_prop_white_bg_no_hands(self):
        p = hibtv_assets.derive_prompt("prop", "back", {})
        self.assertIn("pure white background", p.lower())
        self.assertIn("no hands", p.lower())


class MarkStaleTest(unittest.TestCase):
    def _asset(self, views):
        return SimpleNamespace(kind="cast", views=json.dumps(views))

    def test_anchor_regen_stales_all_derived(self):
        asset = self._asset({
            "face-front": {"stale": False},
            "side-full": {"stale": False},
            "back-full": {"stale": False},
        })
        hibtv_assets.mark_stale(asset, "__anchor")
        views = json.loads(asset.views)
        self.assertTrue(all(v["stale"] for v in views.values()))

    def test_anchor_id_also_stales_all(self):
        asset = self._asset({"face-front": {"stale": False}, "side-full": {"stale": False}})
        hibtv_assets.mark_stale(asset, "front-full")
        views = json.loads(asset.views)
        self.assertTrue(all(v["stale"] for v in views.values()))

    def test_unrelated_view_change_keeps_others_fresh(self):
        asset = self._asset({"face-front": {"stale": False}, "side-full": {"stale": False}})
        hibtv_assets.mark_stale(asset, "side-full")
        views = json.loads(asset.views)
        self.assertFalse(views["face-front"]["stale"])
        self.assertFalse(views["side-full"]["stale"])

    def test_no_views_is_noop(self):
        asset = self._asset({})
        hibtv_assets.mark_stale(asset, "__anchor")
        self.assertEqual(json.loads(asset.views), {})


class FrameTailTest(unittest.TestCase):
    def test_tail_follows_aspect_ratio(self):
        h = hibtv_frames.frame_tail(SimpleNamespace(aspect_ratio="16:9"))
        self.assertIn("横屏构图（16:9）", h)
        self.assertIn(hibtv_frames.CLEAN_TAIL, h)
        v = hibtv_frames.frame_tail(SimpleNamespace(aspect_ratio="9:16"))
        self.assertIn("竖屏构图（9:16）", v)
        self.assertIn(hibtv_frames.CLEAN_TAIL, v)

    def test_defaults_to_vertical_when_missing(self):
        t = hibtv_frames.frame_tail(SimpleNamespace(aspect_ratio=""))
        self.assertIn("竖屏构图（9:16）", t)


class PromptForTest(unittest.TestCase):
    def _frame(self, meta, prompt="叶辰站在圆桌南侧"):
        return SimpleNamespace(prompt=prompt, meta=json.dumps(meta))

    def test_prompt_declares_each_ref_role(self):
        refs = [
            {"path": "/s.png", "role": "scene", "code": "S01", "name": "宴会厅"},
            {"path": "/c.png", "role": "cast-anchor", "code": "叶辰", "name": "叶辰",
             "gender": "男", "identity": "主角"},
            {"path": "/f.png", "role": "cast-face", "code": "叶辰", "name": "叶辰"},
            {"path": "/p.png", "role": "prop", "code": "P01", "name": "红酒杯"},
        ]
        chars = [{"code": "叶辰", "name": "叶辰", "gender": "男", "identity": "主角"}]
        p = hibtv_frames.prompt_for(self._frame({"characters": ["叶辰"]}), "夜景",
                                    characters=chars, refs=refs, tail="TAIL")
        self.assertIn("<image1>", p)
        self.assertIn("environment reference for S01", p)
        self.assertIn("<image3>", p)
        self.assertIn("face reference", p)
        self.assertIn("prop reference for P01", p)
        self.assertIn("Show each exactly once", p)
        self.assertTrue(p.endswith("TAIL"))

    def test_prompt_without_character_forbids_people(self):
        p = hibtv_frames.prompt_for(self._frame({"extras": 0}), "", characters=[], refs=[], tail="TAIL")
        self.assertIn("Exact on-screen person count: 0", p)
        self.assertIn("Do not add background people", p)
        self.assertTrue(p.endswith("TAIL"))

    def test_ref_without_path_is_dropped(self):
        refs = [{"path": "", "role": "scene", "code": "S01", "name": "宴会厅"}]
        p = hibtv_frames.prompt_for(self._frame({}), "", characters=[], refs=refs, tail="TAIL")
        self.assertNotIn("environment reference for S01", p)


class AssetStatusTest(unittest.TestCase):
    """锁住 fix：派生出图不再把已确认锚点打回 review（chip 不回跳）。"""

    def _asset(self, main_path, confirmed):
        data = {"imageConfirmed": True} if confirmed else {}
        return SimpleNamespace(main_path=main_path, data=json.dumps(data))

    def test_no_main_is_empty(self):
        self.assertEqual(hibtv_jobs.asset_status(self._asset("", False)), "empty")

    def test_main_unconfirmed_is_review(self):
        self.assertEqual(hibtv_jobs.asset_status(self._asset("assets/cast/C01/main.png", False)), "review")

    def test_main_confirmed_is_done(self):
        self.assertEqual(hibtv_jobs.asset_status(self._asset("assets/cast/C01/main.png", True)), "done")


if __name__ == "__main__":
    unittest.main()

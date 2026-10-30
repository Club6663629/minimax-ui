"""后期装配回归：检查时长、音轨和淡化参数，不调用模型。"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import hibtv_compose as compose


class ComposeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = [self.root / "first.mp4", self.root / "second.mp4"]
        for p in self.files:
            p.write_bytes(b"fixture")

    def tearDown(self):
        self.tmp.cleanup()

    def test_audio_boundaries_keep_video_copy_and_do_not_overlap_timeline(self):
        commands = []
        def run(cmd, **kwargs):
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"prepared")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with patch.object(compose, "_has_audio", side_effect=[True, False]), patch.object(compose, "_run", side_effect=run):
            out = compose._prepare_audio("ffmpeg", self.files, [5.166667, 5.166667], self.root, 0.025)
        self.assertEqual(len(out), 2)
        self.assertEqual(commands[0][commands[0].index("-c:v")+1], "copy")
        first_filter = commands[0][commands[0].index("-af")+1]
        last_filter = commands[1][commands[1].index("-af")+1]
        self.assertIn("afade=t=out:st=5.141667", first_filter)
        self.assertNotIn("afade=t=in", first_filter)
        self.assertIn("afade=t=in:st=0", last_filter)
        self.assertNotIn("afade=t=out", last_filter)
        self.assertIn("anullsrc=r=48000:cl=stereo", commands[1])
        self.assertTrue(all("acrossfade" not in str(cmd) for cmd in commands))
        self.assertEqual(commands[0][commands[0].index("-t")+1], "5.166667")

    def test_real_duration_mismatch_blocks_before_ffmpeg(self):
        with patch.object(compose, "ffmpeg_bin", return_value="ffmpeg"), \
             patch.object(compose, "HIBTV_DIR", self.root), \
             patch.object(compose, "probe_seconds", return_value=4.5), \
             patch.object(compose, "_run") as run:
            with self.assertRaisesRegex(compose.HibtvComposeError, "计划"):
                compose.render_episode(project_id=1, ep=1, segment_paths=[str(p) for p in self.files], planned=[5.166667, 5.166667])
            run.assert_not_called()

    def test_render_keeps_permanent_source_manifest_and_actual_cutlist(self):
        commands = []
        def run(cmd, **kwargs):
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"video")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        def seconds(path):
            return 10.333334 if "E01-v1.mp4" in str(path) else 5.166667
        with patch.object(compose, "ffmpeg_bin", return_value="ffmpeg"), \
             patch.object(compose, "HIBTV_DIR", self.root), \
             patch.object(compose, "probe_seconds", side_effect=seconds), \
             patch.object(compose, "_has_audio", return_value=True), \
             patch.object(compose, "_run", side_effect=run):
            result = compose.render_episode(project_id=1, ep=1, segment_paths=[str(p) for p in self.files], planned=[5.166667, 5.166667])
        manifest = (self.root / "1/render/E01-v1.txt").read_text()
        self.assertIn(str(self.files[0]), manifest)
        self.assertNotIn("hibtv-audio-", manifest)
        self.assertEqual(result["audioFadeSeconds"], 0.025)
        self.assertEqual(len(commands), 3)
        self.assertTrue(Path(result["path"]).exists())


if __name__ == "__main__":
    unittest.main()

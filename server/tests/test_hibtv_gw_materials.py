import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services import hibtv_codex as hibtv


class GatewayMaterialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hibtv-gw-materials-test-")
        self.root = Path(self.tmp.name)
        self.old_hibtv_dir = hibtv.HIBTV_DIR
        self.old_host = hibtv.GW_MATERIALS_HOST
        self.old_mount = hibtv.GW_MATERIALS_MOUNT
        hibtv.HIBTV_DIR = self.root / "local"
        hibtv.GW_MATERIALS_HOST = self.root / "host"
        hibtv.GW_MATERIALS_MOUNT = self.root / "mount"

    def tearDown(self):
        hibtv.HIBTV_DIR = self.old_hibtv_dir
        hibtv.GW_MATERIALS_HOST = self.old_host
        hibtv.GW_MATERIALS_MOUNT = self.old_mount
        self.tmp.cleanup()

    def test_write_source_publishes_identical_gateway_copy(self):
        local = Path(hibtv.write_source(37, "最后一班地铁\n"))
        staged = hibtv.GW_MATERIALS_HOST / "37" / "source.txt"

        self.assertEqual(local.read_bytes(), staged.read_bytes())
        self.assertEqual(str(hibtv.gateway_source_path(37)),
                         str(self.root / "mount" / "37" / "source.txt"))

    def test_outline_uses_container_path_not_platform_path(self):
        hibtv.write_source(37, "最后一班地铁\n")
        project = SimpleNamespace(
            id=37,
            novel_path="/data/minimax-ui/hibtv/37/source.txt",
            minutes_min=1,
            aspect_ratio="9:16",
            episodes=1,
            keeps="",
        )

        task = hibtv.task_for(None, project, "outline", 0)

        expected = hibtv.publish_material(37, "source.txt", "最后一班地铁\n".encode())
        self.assertIn(f"小说正文：{expected}", task)
        self.assertNotIn(project.novel_path, task)

    def test_publish_failure_is_loud(self):
        blocker = self.root / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        hibtv.GW_MATERIALS_HOST = blocker

        with self.assertRaisesRegex(RuntimeError, "网关素材中转失败"):
            hibtv.write_source(37, "must fail")


if __name__ == "__main__":
    unittest.main()

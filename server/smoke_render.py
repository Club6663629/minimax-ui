"""生成链路冒烟：段提交 → 后处理 → 段成片 → 链式提交 → 集成片 → 返修 → 交付。

媒体层全部 mock（不依赖 ffmpeg/GPU）：./venv/bin/python smoke_render.py
"""
import json
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./data/smoke_render.db"
os.environ["DRAMA_LLM_PROVIDER"] = "off"
os.environ["DRAMA_ENABLED"] = "true"
os.environ["DIRECTOR_ENABLED"] = "true"

from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    DramaArtifact, DramaCharacter, DramaEpisode, DramaProject, DramaRun, DramaScene,
    DramaShot, Task, User,
)
from app.services import drama_composer as composer  # noqa: E402
from app.services import drama_runner as runner  # noqa: E402

assert settings.drama_enabled and settings.director_enabled


class FakeMedia:
    """最小媒体桩：只建占位文件，不做真实编解码。"""

    MediaError = runner.media.MediaError

    def __init__(self):
        self.calls = []

    def probe_duration(self, path):
        return 1.0

    def probe_has_audio(self, path):
        return False

    def slice_frames(self, master, out, start_frame, end_frame, fps=24, has_audio=False):
        self.calls.append(("slice", start_frame, end_frame))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"clip")
        return Path(out)

    def concat_clips(self, paths, out, fps=24):
        self.calls.append(("concat", len(paths)))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"concat")
        return Path(out)

    def seam_metrics(self, prev_clip, prev_frames, next_clip):
        return {"ssim": 0.95, "d_y": 1.2, "audio_db": -20.0, "level": "ok"}

    def make_sheet(self, clip_paths, out, cols=4):
        return None

    def extract_frame(self, clip, frame_index, out):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"png")
        return Path(out)


def build(db):
    db.query(DramaArtifact).delete()
    db.query(DramaRun).delete()
    db.query(Task).delete()
    db.query(DramaShot).delete()
    db.query(DramaScene).delete()
    db.query(DramaCharacter).delete()
    db.query(DramaEpisode).delete()
    db.query(DramaProject).delete()
    db.query(User).delete()
    db.commit()

    user = User(email="render@local", username="render", password_hash="x", credits=100000)
    db.add(user)
    db.flush()
    project = DramaProject(user_id=user.id, title="渲染剧", aspect_ratio="16:9")
    db.add(project)
    db.flush()
    episode = DramaEpisode(
        project_id=project.id, episode_no=1, title="第1集", status="shots_ready",
        beats=json.dumps([{"id": "b1", "title": "开场", "summary": "", "mood": "",
                           "location": "", "characters": [], "target_seconds": 140}]),
    )
    db.add(episode)
    db.flush()
    hero = DramaCharacter(
        project_id=project.id, name="林川", role="main", appearance="短发黑风衣",
        costume="黑风衣", locked=True, card_upload_id=1,
    )
    db.add(hero)
    for scene_no, name in ((1, "街头"), (2, "天台")):
        db.add(DramaScene(episode_id=episode.id, scene_no=scene_no, name=name,
                          location=name, time_of_day="夜"))
    db.flush()
    shot_no = 1
    # 场1 13 镜 × 10s → 段1(12镜) + 段2(1镜，同场承接)；场2 3 镜 × 5s → 段3(hard cut)
    plan = [(1, 10.0)] * 13 + [(2, 5.0)] * 3
    for scene_no, duration in plan:
        db.add(DramaShot(
            episode_id=episode.id, shot_no=shot_no, scene_no=scene_no, beat_id="b1",
            prompt=f"第{shot_no}镜 人物在{scene_no}场行动", duration_s=duration,
            shot_size="中景", camera="平视", movement="固定",
            character_ids=json.dumps([hero.id]), ref_upload_ids="[]",
        ))
        shot_no += 1
    db.flush()
    return user, project, episode


def fake_task(db, run: DramaRun, index: int, cost: int = 70) -> Path:
    """模拟 worker 完成：写 master 占位文件 + task 终态。"""
    task = db.get(Task, run.task_id)
    master = Path("../data/smoke_render") / f"master_{index}.mp4"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"master")
    task.status = "done"
    task.video_path = str(master)
    task.cost = cost
    db.commit()
    return master


def run_once(db, run_id: int, media: FakeMedia) -> None:
    runner.media = media  # 让 runner 内部所有媒体调用走桩
    runner.postprocess(run_id)


def main() -> int:
    db_file = Path("./data/smoke_render.db")
    if db_file.exists():
        db_file.unlink()
    Base.metadata.create_all(bind=engine)
    media = FakeMedia()
    with SessionLocal() as db:
        user, project, episode = build(db)
        composer.refresh_episode_spec(db, episode)
        segments = composer.plan_segments(db, episode)
        db.commit()
        assert len(segments) == 3, len(segments)
        assert segments[1].entry_mode == "continue", segments[1].entry_mode
        assert segments[2].entry_mode == "cut", segments[2].entry_mode
        print("分段：", [(s.segment_no, s.entry_mode, s.target_seconds) for s in segments])

        # 1) 提交集（顺序链式）：只提交第 1 段
        result = runner.submit_episode(db, episode, user, chain=True)
        assert result["reused"] is False and result["segment_no"] == 1, result
        assert result["remaining"] == 2, result
        first_run = db.get(DramaRun, result["run_id"])
        assert first_run.mode == "segment"
        assert json.loads(first_run.plan)["chain"] is True
        ep = db.get(DramaEpisode, episode.id)
        assert ep.status == "rendering" and ep.active_run_id == first_run.id
        print(f"提交段1 run#{first_run.id} 成本 {result['cost']} 待生成段数 {result['remaining']}")

        # 2) 段1 完成 → 段成片 + 链式提交段2
        fake_task(db, first_run, 1)
        run_once(db, first_run.id, media)
        db.expire_all()
        seg1 = db.get(type(segments[0]), segments[0].id)
        cut1 = runner.current_segment_cut(db, seg1)
        assert cut1 is not None and Path(cut1.path).exists(), "段1 应有段成片"
        assert cut1.spec_hash == seg1.spec_hash
        assert seg1.status == "review", seg1.status
        assert seg1.active_run_id is None
        ep = db.get(DramaEpisode, episode.id)
        assert ep.active_run_id is not None, "应已链式提交段2"
        second = db.get(DramaRun, ep.active_run_id)
        assert second.segment_id == seg1.id or second.segment_id != first_run.segment_id
        seg2 = db.get(type(segments[0]), second.segment_id)
        assert seg2.segment_no == 2, seg2.segment_no
        assert seg2.bridge_upload_id, "承接段应自动建立桥接帧"
        print(f"段1 完成，段成片 {cut1.duration:.2f}s；已链式提交段2 run#{second.id}，桥接帧 OK")

        # 3) 段2 完成 → 链式提交段3
        fake_task(db, second, 2)
        run_once(db, second.id, media)
        db.expire_all()
        ep = db.get(DramaEpisode, episode.id)
        third = db.get(DramaRun, ep.active_run_id)
        assert db.get(type(segments[0]), third.segment_id).segment_no == 3
        print(f"段2 完成；已链式提交段3 run#{third.id}")

        # 4) 段3 完成 → 集成片生成、集进入 review
        fake_task(db, third, 3)
        run_once(db, third.id, media)
        db.expire_all()
        ep = db.get(DramaEpisode, episode.id)
        final = runner.current_final(db, ep)
        assert final is not None and Path(final.path).exists(), "全部段就绪后应有集成片"
        assert final.spec_hash == ep.spec_hash
        assert ep.status == "review", ep.status
        assert ep.active_run_id is None
        print(f"集成片就绪 {final.duration:.2f}s，集状态 {ep.status}")

        # 5) 交付门
        runner.finish_episode(db, ep)
        db.expire_all()
        ep = db.get(DramaEpisode, episode.id)
        assert ep.status == "done", ep.status
        assert all(s.status == "approved" for s in db.query(DramaShot).all())
        print("交付 OK")

        # 6) 重新编辑后单镜返修：只重拼所属段 + 集
        runner.reopen_episode(db, ep)
        db.expire_all()
        ep = db.get(DramaEpisode, episode.id)
        assert ep.status == "shots_ready", ep.status
        seg3 = db.query(type(segments[0])).filter_by(episode_id=ep.id, segment_no=3).one()
        assert seg3.status == "review", seg3.status
        target = db.query(DramaShot).filter_by(episode_id=ep.id, scene_no=2).first()
        patch = runner.submit_patch(db, ep, target, "人物表情再收一点", anchor=True)
        patch_run = db.get(DramaRun, patch["run_id"])
        assert patch_run.mode == "patch" and patch_run.segment_id == seg3.id
        print(f"返修 run#{patch_run.id} 成本 {patch['cost']} 窗口 {patch['window_frames']} 帧")
        fake_task(db, patch_run, 4, cost=20)
        run_once(db, patch_run.id, media)
        db.expire_all()
        patched = db.get(DramaShot, target.id)
        assert patched.repair_count == 1, patched.repair_count
        assert runner.current_final(db, ep) is not None
        print("返修后段成片与集成片已重拼")

        # 7) 回滚段成片：指针切换 + 0 积分重拼集
        arts = (
            db.query(DramaArtifact)
            .filter(DramaArtifact.segment_id == seg3.id, DramaArtifact.kind == "segment")
            .order_by(DramaArtifact.id)
            .all()
        )
        assert len(arts) >= 2, len(arts)
        runner.rollback(db, ep, arts[0].id)
        db.expire_all()
        assert runner.current_segment_cut(db, seg3).id == arts[0].id
        print(f"段成片回滚到 v{arts[0].version_no} OK")
        db.rollback()
    print("RENDER SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

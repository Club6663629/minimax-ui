"""分段冒烟：临时库建项目→分镜→自动分段→段级排版/指纹/就绪检查。

只用内存 sqlite，不碰业务库；直接运行：./venv/bin/python smoke_segments.py
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./data/smoke_segments.db"
os.environ["DRAMA_LLM_PROVIDER"] = "off"

from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    DramaCharacter, DramaEpisode, DramaProject, DramaScene, DramaShot, User,
)
from app.services import drama_composer as composer  # noqa: E402


def build(db):
    db.query(DramaShot).delete()
    db.query(DramaScene).delete()
    db.query(DramaCharacter).delete()
    db.query(DramaEpisode).delete()
    db.query(DramaProject).delete()
    db.query(User).delete()
    db.commit()

    user = User(email="smoke@local", username="smoke", password_hash="x", credits=10000)
    db.add(user)
    db.flush()
    project = DramaProject(
        user_id=user.id, title="冒烟剧", style_bible="冷色调", aspect_ratio="16:9",
    )
    db.add(project)
    db.flush()
    episode = DramaEpisode(project_id=project.id, episode_no=1, title="第1集", status="shots_ready")
    db.add(episode)
    db.flush()
    hero = DramaCharacter(
        project_id=project.id, name="林川", role="main",
        appearance="短发，黑色风衣", costume="黑色风衣", locked=True,
    )
    db.add(hero)
    db.flush()
    for scene_no in (1, 2, 3):
        db.add(DramaScene(
            episode_id=episode.id, scene_no=scene_no, name=f"场景{scene_no}",
            location="室内", time_of_day="夜", description="",
        ))
    shot_no = 1
    # 3 场 × 8 镜 × 4s = 96s，按场切成 3 段
    for scene_no in (1, 2, 3):
        for i in range(8):
            db.add(DramaShot(
                episode_id=episode.id, shot_no=shot_no, scene_no=scene_no,
                beat_id=f"b{scene_no}", prompt=f"场景{scene_no} 第{i + 1} 个画面，人物走动",
                duration_s=4.0, shot_size="中景", camera="平视", movement="固定",
                character_ids="[%d]" % hero.id, ref_upload_ids="[]",
            ))
            shot_no += 1
    db.flush()
    return user, project, episode


def main() -> int:
    if os.path.exists("./data/smoke_segments.db"):
        os.remove("./data/smoke_segments.db")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user, project, episode = build(db)
        composer.refresh_episode_spec(db, episode)
        db.commit()

        segments = composer.plan_segments(db, episode)
        db.commit()
        assert len(segments) == 3, f"期望 3 段，实际 {len(segments)}"
        for segment in segments:
            shots = composer.segment_shots(db, segment)
            assert shots, "段内不能为空"
            assert len(shots) <= settings.drama_segment_max_shots
            total = sum(s.duration_s for s in shots)
            assert total <= settings.drama_segment_max_seconds, total
            print(f"段{segment.segment_no} {segment.entry_mode} {len(shots)}镜 {total:g}s "
                  f"hash={segment.spec_hash[:8]}")
        assert segments[0].entry_mode == "cut"
        assert segments[1].entry_mode == "cut", "跨场应为硬切"

        plan = composer.build_segment_plan(db, segments[1])
        assert plan["mode"] == "segment"
        assert plan["segment_no"] == 2
        assert sum(w["frames"] for w in plan["windows"]) == plan["total_frames"]
        print(f"段2 排版 {plan['total_frames']} 帧 / 成本 {composer.estimate_cost(plan)} 积分")

        episode_hash = composer.compute_spec_hash(db, episode)
        assert episode_hash != segments[0].spec_hash
        print(f"单段 hash 列表: {[s.spec_hash[:6] for s in segments]} 集 hash={episode_hash[:6]}")

        # 改第 2 段某镜 → 只有第 2 段指纹变
        before = {s.segment_no: s.spec_hash for s in segments}
        edited = segments[1].segment_no
        shot = composer.segment_shots(db, segments[1])[0]
        shot.prompt = shot.prompt + "（改）"
        composer.refresh_episode_spec(db, episode)
        db.commit()
        after = {s.segment_no: s.spec_hash for s in segments}
        assert before[edited] != after[edited], "被改段指纹应变化"
        for no in before:
            if no != edited:
                assert before[no] == after[no], f"第 {no} 段指纹不应变化"
        print("段级指纹隔离 OK")

        check = composer.validate_episode(db, episode, user)
        assert check["segments"] == 3, check
        print("就绪检查:", check["ready"], "issues=", [i["code"] for i in check["issues"]],
              "warnings=", [w["code"] for w in check["warnings"]])

        # 保存手改分段：把段 3 的两镜并入段 2 会超镜数，这里只验证合法保存
        items = []
        for segment in segments:
            items.append(type("I", (), {
                "segment_no": segment.segment_no,
                "title": segment.title,
                "shot_ids": [s.id for s in composer.segment_shots(db, segment)],
            })())
        composer.save_segments(db, episode, items)
        db.commit()
        assert len(composer.episode_segments(db, episode)) == 3
        print("手动保存分段 OK")

        # 未分段集应报 SEGMENT_MISSING
        empty = DramaEpisode(project_id=project.id, episode_no=2, status="shots_ready")
        db.add(empty)
        db.flush()
        db.add(DramaShot(
            episode_id=empty.id, shot_no=1, scene_no=1, beat_id="b1",
            prompt="空集唯一镜头，测试用", duration_s=5.0, character_ids="[]",
            ref_upload_ids="[]",
        ))
        db.flush()
        composer.refresh_episode_spec(db, empty)
        check2 = composer.validate_episode(db, empty, user)
        assert any(i["code"] == "SEGMENT_MISSING" for i in check2["issues"]), check2["issues"]
        print("未分段集提示 OK")
        db.rollback()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

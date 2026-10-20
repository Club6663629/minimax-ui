"""HTTP 契约冒烟：路由 + 序列化 + 段生成/审片/交付（媒体层用桩）。

前端依赖的字段与端点按《短剧分段生产改造》实现，本脚本走真实 FastAPI 路由：
分段计划 → 手动保存 → 生成缺口段 → 链式续段 → 段复用 → 段通过/驳回 → 交付 → 重新编辑 → 三级下载。

直接运行：./venv/bin/python smoke_api.py
"""
import json
import os
import shutil
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./data/smoke_api.db"
os.environ["DATA_DIR"] = "./data/smoke_api"
os.environ["DRAMA_LLM_PROVIDER"] = "off"
os.environ["DRAMA_ENABLED"] = "true"
os.environ["DIRECTOR_ENABLED"] = "true"
os.environ["DRAMA_ADMIN_ONLY"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import create_token, get_current_user  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    DramaArtifact, DramaCharacter, DramaEpisode, DramaProject, DramaRun, DramaScene,
    DramaShot, Task, Upload, User,
)
from app.services import drama_composer as composer  # noqa: E402
from app.services import drama_runner as runner  # noqa: E402
from app.services.pool import pool  # noqa: E402

USER: User  # 认证桩返回的用户


class FakeMedia:
    """最小媒体桩：只落占位文件，不做编解码（同 smoke_render）。"""

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


def seed(db):
    db.query(DramaArtifact).delete()
    db.query(DramaRun).delete()
    db.query(Task).delete()
    db.query(DramaShot).delete()
    db.query(DramaScene).delete()
    db.query(DramaCharacter).delete()
    db.query(DramaEpisode).delete()
    db.query(DramaProject).delete()
    db.query(Upload).delete()
    db.query(User).delete()
    db.commit()

    user = User(email="api@local", username="api", password_hash="x", credits=100000,
                role="admin")
    db.add(user)
    db.flush()
    # 先落真实定妆图上传行，避免桥接帧创建 Upload 时占用 card_upload_id，导致段指纹漂移
    card = Upload(user_id=user.id, slot="image", filename="card.png",
                  path="card.png", md5="card-md5", size=1)
    db.add(card)
    db.flush()
    project = DramaProject(user_id=user.id, title="契约剧", aspect_ratio="16:9")
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
        costume="黑风衣", locked=True, card_upload_id=card.id,
    )
    db.add(hero)
    for scene_no, name in ((1, "街头"), (2, "天台")):
        db.add(DramaScene(episode_id=episode.id, scene_no=scene_no, name=name,
                          location=name, time_of_day="夜"))
    db.flush()
    shot_no = 1
    # 场1 13 镜 × 10s → 段1(12 镜) + 段2(1 镜，承接)；场2 3 镜 × 5s → 段3(硬切)
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
    composer.refresh_episode_spec(db, episode)
    db.commit()
    return user, project, episode


def expect_keys(obj: dict, keys, label: str) -> None:
    missing = [key for key in keys if key not in obj]
    assert not missing, f"{label} 缺字段 {missing}"


def episode_of(client: TestClient, eid: int) -> dict:
    response = client.get(f"/api/drama/episodes/{eid}")
    assert response.status_code == 200, response.text
    return response.json()["episode"]


def complete_task(db, run_id: int, index: int, cost: int = 70) -> None:
    """模拟 worker 完成：写 master 占位文件 + task 终态。"""
    run = db.get(DramaRun, run_id)
    task = db.get(Task, run.task_id)
    master = Path(os.environ["DATA_DIR"]) / f"master_{index}.mp4"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"master")
    task.status = "done"
    task.video_path = str(master)
    task.cost = cost
    db.commit()


def settle(client: TestClient, run_id: int) -> dict:
    """GET /runs 触发 BackgroundTasks 后处理，再取一次拿到终态。"""
    first = client.get(f"/api/drama/runs/{run_id}")
    assert first.status_code == 200, first.text
    second = client.get(f"/api/drama/runs/{run_id}")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] == "done", body
    return body


def main() -> int:
    db_file = Path("./data/smoke_api.db")
    if db_file.exists():
        db_file.unlink()
    data_dir = Path(os.environ["DATA_DIR"])
    if data_dir.exists():
        shutil.rmtree(data_dir)
    Base.metadata.create_all(bind=engine)

    global USER
    with SessionLocal() as db:
        USER, project, episode = seed(db)
        user_id, pid, eid = USER.id, project.id, episode.id

    app.dependency_overrides[get_current_user] = lambda: USER
    pool.has_director = lambda: True  # 路由只做存在性闸门，不实际派发任务
    runner.media = FakeMedia()

    client = TestClient(app)  # 不进 lifespan，避免后台 worker 线程干扰
    try:
        # 1) /status 暴露段相关上限
        status_body = client.get("/api/drama/status").json()
        expect_keys(status_body["limits"], (
            "segment_target_seconds", "segment_max_seconds", "segment_max_shots",
        ), "status.limits")
        print("status 段上限 OK：", {k: status_body["limits"][k] for k in (
            "segment_target_seconds", "segment_max_seconds", "segment_max_shots")})

        # 2) 集详情：未分段时 shots.segment_no 为 null、segments 为空
        episode = episode_of(client, eid)
        expect_keys(episode, ("segments", "shots", "stats", "active_run"), "episode")
        assert episode["segments"] == []
        assert all(shot["segment_no"] is None for shot in episode["shots"])
        print("未分段序列化 OK")

        # 3) 自动分段 → 3 段，段 2 承接、段 3 硬切
        response = client.post(f"/api/drama/episodes/{eid}/segments/plan", json={})
        assert response.status_code == 200, response.text
        segments = response.json()["segments"]
        assert [s["segment_no"] for s in segments] == [1, 2, 3], segments
        assert [s["entry_mode"] for s in segments] == ["cut", "continue", "cut"]
        for segment in segments:
            expect_keys(segment, (
                "id", "segment_no", "title", "scene_nos", "entry_mode", "status",
                "target_seconds", "duration", "shot_count", "shot_ids", "estimate_cost",
                "cut_url", "bridge_url", "bridge_note", "stale", "active_run", "last_error",
            ), f"segment{segment['segment_no']}")
            assert segment["stale"] is True and segment["status"] == "ready"
        print("自动分段 OK：", [(s["segment_no"], s["entry_mode"], s["shot_count"])
                             for s in segments])

        # 4) 未 force 再次分段应 400
        conflict = client.post(f"/api/drama/episodes/{eid}/segments/plan", json={})
        assert conflict.status_code == 400, conflict.text
        print("重复分段保护 OK：", conflict.json()["detail"])

        # 5) 手动保存（原样保存）
        payload = {"segments": [{"segment_no": s["segment_no"], "title": s["title"],
                                 "shot_ids": s["shot_ids"]} for s in segments]}
        saved = client.put(f"/api/drama/episodes/{eid}/segments", json=payload)
        assert saved.status_code == 200, saved.text
        assert [s["shot_count"] for s in saved.json()["segments"]] == [12, 1, 3]
        print("手动保存分段 OK")

        # 6) 就绪检查：段数 + 计划行带 segment_no + max_segment_frames
        checklist = client.get(f"/api/drama/episodes/{eid}/checklist").json()
        assert checklist["segments"] == 3
        plan = checklist["plan"]
        assert plan and plan["max_segment_frames"] > 0
        assert {row["segment_no"] for row in plan["segments"]} == {1, 2, 3}
        print("就绪检查 OK：", checklist["segments"], "段 / 最大段帧",
              plan["max_segment_frames"])

        # 7) 审片接口在生成前也能返回 segments
        review = client.get(f"/api/drama/episodes/{eid}/review").json()
        assert len(review["segments"]) == 3
        expect_keys(review["stats"], ("generation_cost", "segments_ready", "repair_cost_total"),
                    "review.stats")
        print("审片序列化 OK：段成片", review["stats"]["segments_ready"], "/ 3")

        # 8) 提交整集 → 只提交第 1 段，remaining=2
        response = client.post(f"/api/drama/episodes/{eid}/render",
                               json={"mode": "missing", "force": False, "chain": True})
        assert response.status_code == 200, response.text
        rendered = response.json()
        assert rendered["reused"] is False and rendered["run"] is not None
        assert rendered["run"]["mode"] == "segment"
        assert rendered["run"]["segment_no"] == 1
        assert rendered["segment_no"] == 1 and rendered["remaining"] == 2
        expect_keys(rendered["run"], ("id", "mode", "status", "phase", "segment_id",
                                      "segment_no"), "render.run")
        print(f"提交段1 run#{rendered['run']['id']} 成本 {rendered['cost']} 剩余 {rendered['remaining']} 段")

        # 9) 依次完成 3 段，验证链式续段与桥接
        run_id = rendered["run"]["id"]
        for index in (1, 2, 3):
            with SessionLocal() as db:
                complete_task(db, run_id, index)
            run_body = settle(client, run_id)
            assert run_body["mode"] == "segment"
            episode = episode_of(client, eid)
            if index < 3:
                assert episode["active_run"] is not None, "应已自动提交下一段"
                run_id = episode["active_run"]["id"]
            else:
                assert episode["active_run"] is None
            print(f"段{index} 完成（run#{run_body['id']}），"
                  f"{'已链式提交下一段' if index < 3 else '集级后处理完成'}")

        episode = episode_of(client, eid)
        assert episode["status"] == "review", episode["status"]
        assert episode["final_url"], "全部段就绪后应有集成片"
        assert episode["stats"]["segments_ready"] == 3
        assert episode["stats"]["generation_cost"] > 0
        assert all(not s["stale"] and s["cut_url"] for s in episode["segments"])
        assert episode["segments"][1]["bridge_url"], "承接段应已建立桥接帧"
        print("集成片与统计 OK：", episode["final_url"],
              "生成成本", episode["stats"]["generation_cost"])

        # 10) 已 fresh 的段再提交 → 0 积分复用
        seg1 = episode["segments"][0]
        reuse = client.post(f"/api/drama/segments/{seg1['id']}/render", json={})
        assert reuse.status_code == 200, reuse.text
        assert reuse.json()["reused"] is True and reuse.json()["cost"] == 0
        print("段复用 OK：0 积分")

        # 11) 段通过 / 驳回 / 再通过
        approved = client.post(f"/api/drama/segments/{seg1['id']}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["segments"][0]["status"] == "approved"
        rejected = client.post(f"/api/drama/segments/{seg1['id']}/reject")
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["segments"][0]["status"] == "review"
        again = client.post(f"/api/drama/segments/{seg1['id']}/approve")
        assert again.status_code == 200, again.text
        print("段通过 → 驳回 → 再通过 OK")

        # 12) 三级下载：final / segment / clip 均为 307 → /files/drama/{id}
        with SessionLocal() as db:
            cuts = db.query(DramaArtifact).filter_by(episode_id=eid, kind="segment").all()
            clips = db.query(DramaArtifact).filter_by(episode_id=eid, kind="clip").all()
            final = db.query(DramaArtifact).filter_by(episode_id=eid, kind="final").first()
            assert cuts and clips and final is not None
            cut_id, clip_id = cuts[0].id, clips[0].id
        token = create_token(user_id)
        for kind, artifact_id in (("final", None), ("segment", cut_id), ("clip", clip_id)):
            query = f"?kind={kind}&download=1&token={token}"
            if artifact_id:
                query += f"&artifact_id={artifact_id}"
            download = client.get(f"/api/drama/episodes/{eid}/download{query}",
                                  follow_redirects=False)
            assert download.status_code == 307, download.text
            location = download.headers["location"]
            assert location.startswith("/files/drama/") and "download=1" in location
        print("三级下载跳转 OK")

        # 13) 交付 → done；重新编辑 → shots_ready，段退回 review
        finished = client.post(f"/api/drama/episodes/{eid}/finish")
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "done"
        reopened = client.post(f"/api/drama/episodes/{eid}/reopen")
        assert reopened.status_code == 200, reopened.text
        body = reopened.json()["episode"]
        assert body["status"] == "shots_ready"
        assert all(s["status"] == "review" for s in body["segments"])
        print("交付 → 重新编辑 OK")

        # 14) 强制重新分段 → 段成片全部变旧版
        forced = client.post(f"/api/drama/episodes/{eid}/segments/plan", json={"force": True})
        assert forced.status_code == 200, forced.text
        assert all(s["stale"] for s in forced.json()["segments"])
        print("强制重新分段 OK：段成片标记旧版")
    finally:
        app.dependency_overrides.clear()

    print("API SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

/** 变更：每一次就地编辑 / 改图 / 生成都记在这里，可逐条撤销。 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, Loading, PageHead } from "../components/ui";
import { errText, useHibtv } from "../state";
import type { ChangeItem } from "../types";

function brief(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 48 ? `${text.slice(0, 48)}…` : text;
}

export default function ChangesPage() {
  const { project, toast, refreshProject } = useHibtv();
  const [rows, setRows] = useState<ChangeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [onlyLive, setOnlyLive] = useState(false);

  const load = useCallback(async () => {
    if (!project) {
      setRows([]);
      return;
    }
    setLoading(true);
    try {
      setRows(await hibtvApi.listChanges(project.id));
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const undo = async (id: number) => {
    if (!project) return;
    try {
      await hibtvApi.undoChange(project.id, id);
      toast("已撤销 1 处改动");
      await load();
      void refreshProject();
    } catch (err) {
      toast(errText(err));
    }
  };

  const undoAll = async () => {
    if (!project) return;
    const live = rows.filter((r) => !r.reverted);
    if (!live.length) {
      toast("没有可撤销的改动");
      return;
    }
    let ok = 0;
    for (const row of live) {
      try {
        await hibtvApi.undoChange(project.id, row.id);
        ok += 1;
      } catch {
        /* 逐条撤销，失败的留在列表里 */
      }
    }
    toast(`已撤销 ${ok} 处改动`);
    await load();
    void refreshProject();
  };

  const shown = onlyLive ? rows.filter((r) => !r.reverted) : rows;
  const liveCount = rows.filter((r) => !r.reverted).length;

  if (!project) {
    return (
      <Stage>
        <Empty glyph="剧" title="还没有项目" sub="先在首页上传定稿剧本与美术设定。">
          <Link className="btn gold" to="/hibtv/home">
            去建项目
          </Link>
        </Empty>
      </Stage>
    );
  }

  return (
    <Stage>
      <PageHead
        eyebrow="审计 · 谁改的 · CHANGES"
        title={
          <>
            变更{" "}
            <span className="chip" style={{ verticalAlign: "middle" }}>
              {liveCount} 条可撤销
            </span>
          </>
        }
        lede="每一次双击编辑、每一张改图、每一版重新生成都记在这里：谁、什么时候、从什么改成了什么。能撤的都会给撤销键。"
        right={
          <>
            <button className="btn" onClick={() => setOnlyLive((v) => !v)}>
              {onlyLive ? "看全部" : "只看可撤销"}
            </button>
            <button className="btn gold" onClick={() => void undoAll()} disabled={!liveCount}>
              全部撤销
            </button>
          </>
        }
      />

      {loading && !rows.length ? <Loading /> : null}

      {shown.length ? (
        <div className="card" style={{ marginTop: 16, overflow: "hidden" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>时间</th>
                <th>谁</th>
                <th>改了什么</th>
                <th>从</th>
                <th>到</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.id}>
                  <td className="mono small">{c.at}</td>
                  <td className="small">{c.who}</td>
                  <td className="small">
                    {c.what}
                    <span className="tiny dim mono" style={{ marginLeft: 6 }}>
                      {c.path}
                    </span>
                  </td>
                  <td className="small dim">{brief(c.from)}</td>
                  <td className="small">{brief(c.to)}</td>
                  <td style={{ textAlign: "right" }}>
                    {c.reverted ? (
                      <span className="chip tiny">已撤销</span>
                    ) : (
                      <button className="btn xs" onClick={() => void undo(c.id)}>
                        撤销
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ marginTop: 18 }}>
          <Empty
            glyph="变"
            title={onlyLive ? "没有可撤销的改动" : "还没有改动"}
            sub="双击任意文字就地改、在图上点图标改图、重新生成一版文档，都会在这里留一条记录。"
          >
            <Link className="btn" to="/hibtv/outline/inner">
              去大纲改一处试试
            </Link>
          </Empty>
        </div>
      )}

      <div className="tiny dim" style={{ marginTop: 10, lineHeight: 1.9 }}>
        撤销只回写字段，不自动重跑下游：受影响的镜头会在分镜页标成「待确认」，需要你点一次重新生成。
      </div>
    </Stage>
  );
}

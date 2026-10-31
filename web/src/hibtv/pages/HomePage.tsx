/** 原型首页：项目列表与剧本、美术设定双上传。 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { useCodexGate } from "../components/CodexLight";
import { Modal, PageHead } from "../components/ui";
import { errText, useHibtv } from "../state";

const empty = { script:null as File|null, art:null as File|null, name:"", genre:"", episodes:"1", min:"2", max:"5", ratio:"9:16", keeps:"" };
export default function HibtvHomePage() {
  const {projects, selectProject, refreshProjects, toast} = useHibtv();
  const navigate = useNavigate(), gate = useCodexGate();
  const [open,setOpen] = useState(false), [busy,setBusy] = useState(false), [draft,setDraft] = useState(empty);
  const ready = !!draft.script && !!draft.art;
  const openForm = () => { setDraft(empty); setOpen(true); };
  const create = async () => {
    if (!ready || busy) return;
    if (![draft.episodes,draft.min,draft.max].every(v => Number.isInteger(Number(v)) && Number(v)>0) || Number(draft.max)<Number(draft.min)) {
      toast("请填写有效的集数与单集时长范围"); return;
    }
    setBusy(true);
    try {
      const p = await hibtvApi.createProject({script:draft.script,art:draft.art,name:draft.name,genre:draft.genre,
        episodes:Number(draft.episodes),minutesMin:Number(draft.min),minutesMax:Number(draft.max),ratio:draft.ratio,keeps:draft.keeps});
      selectProject(p.id); await refreshProjects(); setOpen(false);
      try { await hibtvApi.createJob(p.id,{kind:"outline"}); toast("项目已创建，正在解析剧本与美术设定"); }
      catch(err) { toast(`项目已创建，大纲排队失败：${errText(err)}`); }
      navigate("/hibtv/outline/inner");
    } catch(err) { toast(errText(err)); } finally {setBusy(false);}
  };
  return <Stage><div className="pagewrap">
    <PageHead eyebrow="PROJECTS · 短剧项目" title="项目"
      lede="这里只做两件事：看项目、建项目。上传已经定稿的剧本与美术设定，hibtv 只把它拆成能开工的资产。"
      right={<button className="btn gold" onClick={openForm}>＋ 新建短剧项目</button>} />
    <div className="toolbar" style={{marginTop:20}}><span className="lab">共 {projects.length} 个项目</span>
      <span className="chip">按最近编辑</span><span className="sp"/><span className="tiny dim">deepseek · Qwen-Image · Minimax-h3</span></div>
    <div className="plist">{projects.map(p => {
      const labels:Record<string,string> = {storyboard:"分镜",staging:"站位",script:"剧本",art:"美术",cast:"角色",outline:"大纲"};
      const last = Object.keys(labels).find(k => Object.entries(p.docs).some(([key,d])=>key.startsWith(k+"#")&&d.status==="done"));
      return <div key={p.id} className="prow" onClick={()=>{selectProject(p.id);navigate("/hibtv/overview");}}>
        <span className="th">剧</span><span><span className="nm">{p.name}</span><span className="meta">{p.genre||"未设题材"} · {p.episodes} 集 · {p.aspectRatio} · {p.updatedAt} 编辑</span></span>
        <span className="stage">{last?`已生成到：${labels[last]}`:"已导入：待解析大纲"}</span>
        <span className="num">{p.assetCount.cast||0} 角色 · {(p.assetCount.scene||0)+(p.assetCount.prop||0)} 场景道具</span><span className="go">打开 ›</span></div>;
    })}<div className="pnew" onClick={openForm}><span className="th">＋</span><span><span className="nm">新建短剧项目</span>
      <span className="meta">上传剧本 + 美术设定 → 识别集 / 场 / 角色 / 场景 / 道具 → 先出大纲</span></span><span className="go">选择文件 ›</span></div></div>
  </div>{open&&<div className="layer open"><div className="scrim" onClick={()=>!busy&&setOpen(false)}/>
    <Modal eyebrow="NEW PROJECT · SCRIPT + ART" title="新建短剧项目" sub="上传已经定稿的剧本与美术设定。解析后先出大纲，台词与场次按原剧本保留。"
      onClose={()=>!busy&&setOpen(false)} footer={<><span className="tiny dim">解析后在「大纲 · 对齐检查」核对缺口</span><span className="sp"/>
        <button className="btn ghost" disabled={busy} onClick={()=>setOpen(false)}>取消</button>
        <button className="btn gold" disabled={!ready||busy||!gate.ok} title={gate.why} onClick={()=>void create()}>{busy?"创建中…":"初始化并创建大纲"}</button></>}>
      <div className="row2" style={{marginBottom:14}}>{(["script","art"] as const).map(key=><label key={key} className={`upbox ${draft[key]?"filled":""}`}>
        <div className="t">{key==="script"?"上传剧本":"上传美术设定"} <span className="chip gold tiny">必填</span></div>
        <div className="s">{draft[key]?`${draft[key]!.name} · ${Math.ceil(draft[key]!.size/1024)} KB · 点击更换`:key==="script"?"定稿剧本 · .md / .txt / .docx":"人物 / 服装 / 场景 / 道具 / 特效设定集"}</div>
        <input type="file" aria-label={key==="script"?"上传剧本":"上传美术设定"} accept=".md,.txt,.markdown,.docx" style={{display:"none"}}
          onChange={e=>{const file=e.target.files?.[0];if(file)setDraft(d=>({...d,[key]:file}));}}/></label>)}</div>
      <div className="tiny dim" style={{marginBottom:14}}>两个文件都上传后可开始解析集数、场次与资产条目。</div>
      <div className="row2"><label className="field"><span className="label">项目名</span><input className="input" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/></label>
        <label className="field"><span className="label">题材</span><input className="input" placeholder="都市情感 / 逆袭" value={draft.genre} onChange={e=>setDraft({...draft,genre:e.target.value})}/></label></div>
      <div className="row2"><label className="field"><span className="label">总集数</span><input className="input" type="number" min="1" value={draft.episodes} onChange={e=>setDraft({...draft,episodes:e.target.value})}/></label>
        <div className="field"><div className="label">单集时长范围</div><div className="inline"><input className="input" aria-label="最短分钟" style={{width:64}} value={draft.min} onChange={e=>setDraft({...draft,min:e.target.value})}/>—
          <input className="input" aria-label="最长分钟" style={{width:64}} value={draft.max} onChange={e=>setDraft({...draft,max:e.target.value})}/><span className="tiny dim">分钟</span></div></div></div>
      <div className="field"><div className="label">画面比例</div><div className="inline">{["9:16","16:9"].map(r=><button key={r} className={`chip i ${draft.ratio===r?"gold":""}`} onClick={()=>setDraft({...draft,ratio:r})}>{r} {r==="9:16"?"竖屏 · 短剧默认":"横屏"}</button>)}</div></div>
      <label className="field"><span className="label">必须保留的名场面 / 场次 <span className="dim">可选</span></span><textarea className="textarea" value={draft.keeps} onChange={e=>setDraft({...draft,keeps:e.target.value})}/></label>
      <div className="toolbar"><span className="lab">本次解析</span><span className="chip">剧本解析</span><span className="chip">美术设定解析</span><span className="sp"/><span className="tiny dim">deepseek · 模型服务固定，不可更改</span></div>
      {!gate.ok&&<div className="tiny red">{gate.why}</div>}
    </Modal></div>}</Stage>;
}

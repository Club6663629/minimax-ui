#!/usr/bin/env node
/**
 * studio_seed — 从 script.json 确定性生成 staging.json / storyboard.json。
 *
 * 上游两个 skill 都给了 `seedFromScript`，但它们只搭空骨架，段与切留给模型填。
 * 平台没有「编剧模型」这一步，所以这里把那一层也确定性化：
 *
 *   - 分段：一拍一切 → 合并短切 → 装段（≤15s、≤9 切）→ 段秒吸附到 5 / 10 / 15
 *   - 切字段：景别 / 运镜 / 六项构图量化字段 / 分镜图提示词（中文）/ Seedance 正文（中文）
 *   - H3 提示词：首行对齐指令、[Shot k] 切点时刻、三字段，全部用上游函数拼
 *
 * 原则：门要的结构一个字符都不自己编（对齐指令与切点走 novel-storyboard 的导出），
 * 画面描述只用剧本原文与美术设定里已有的中英文，不凭空生成事实。
 *
 *   node studio_seed.mjs staging    < payload.json
 *   node studio_seed.mjs storyboard < payload.json
 *
 * payload: { script, art, cast, outline, ep }
 * 输出：stdout 一个 JSON `{ok, kind, doc}`；失败 exit 2，错误写 stderr。
 */
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

import {
  SHOT_SIZES,
  CAMERA_MOVES,
  h3AlignmentLine,
  h3CutTime,
  cutStarts,
} from './skills/novel-storyboard/scripts/novel-storyboard.mjs'

const r1 = (n) => Math.round(n * 10) / 10
const r2 = (n) => Math.round(n * 100) / 100
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const lineChars = (s) => String(s ?? '').replace(/\s+/g, '').length

const SCRIPT_DEFAULTS = { charsPerSecond: 4.5, actionSeconds: 2.5 }
const BASE_PARAMS = {
  maxSegmentSeconds: 15,
  // 短剧里「……」这种一拍不到一秒的镜头不少；切下限放到 1.5 秒，
  // 才能把总时长压回剧本目标（1.5 仍是上游 cut-length 门的下限）。
  minCutSeconds: 1.5,
  maxCutSeconds: 5,
  maxOnScreen: 3,
  tolerance: 0.15,
}

/* ------------------------------------------------------------------ */
/* 素材：只用设定里已有的中英文                                          */
/* ------------------------------------------------------------------ */

/**
 * 英文设定句里的「不是画面」的尾巴：风格、构图、负向词。
 * 与 studio/prompts.py 的 _ANCHOR_WORDS 同一个口径，改一处要改两处。
 */
const EN_NOISE = [
  'character design',
  'chinese xianxia character',
  'neutral standing pose',
  'plain grey background',
  'full body visible',
  'head to toe',
  'cinematic costume detail',
  'empty scene',
  'no people',
  'no figures',
  'vertical cinematic establishing shot',
  'ultra detailed environment concept art',
  'no text',
  'no watermark',
]

/** 把英文提示词切成短句，去掉风格尾巴，取前 limit 段当名词短语素材。 */
function enPieces(prompt, limit = 3) {
  // 先按句号与逗号一起切：「no figures. underground sword arena」这种句号后紧跟正文的写法
  // 只按逗号切会把场景主名当成噪声一起丢掉。
  const parts = String(prompt ?? '')
    .split(/[.,]\s*/)
    .map((s) => s.trim())
    .filter(Boolean)
  const out = []
  for (const piece of parts) {
    const low = piece.toLowerCase()
    if (EN_NOISE.some((n) => low.includes(n))) continue
    out.push(piece)
    if (out.length >= limit) break
  }
  return out.slice(0, limit)
}

/** 名词短语补冠词：人物卡里写的是「lean wiry young man」，直接接 of 不成句。 */
function withArticle(phrase) {
  const s = String(phrase ?? '').trim()
  if (!s || /^(a|an|the|one|two|three)\b/i.test(s)) return s
  return `${/^[aeiou]/i.test(s) ? 'an' : 'a'} ${s}`
}

/** 人物卡：outline 给 id / 身份，cast 给外观英文句。script 用 id，出图用名字。 */
function castIndex(outline, cast) {
  const byName = new Map((outline?.characters ?? []).map((c) => [c.name, c]))
  return (cast?.characters ?? []).map((c, i) => {
    const o = byName.get(c.name) ?? {}
    return {
      id: o.id ?? `C${String(i + 1).padStart(2, '0')}`,
      name: c.name,
      identity: String(c?.persona?.identity ?? o.role ?? '人物').trim(),
      role: String(o.role ?? '').trim(),
      oneLiner: String(c.oneLiner ?? '').trim(),
      en: enPieces(c?.image?.prompt, 2),
    }
  })
}

function artIndex(art) {
  return new Map((art?.scenes ?? []).map((s) => [s.id, s]))
}

/** 场景的英文短语与光线句：光线按剧本的「夜 / 日」挑 art 里的对应档。 */
function sceneBrief(scene, artScene) {
  const pieces = enPieces(artScene?.image?.prompt, 3)
  const lighting =
    (artScene?.lighting ?? []).find((l) => l.state === scene?.lighting)?.prompt ??
    (artScene?.lighting ?? [])[0]?.prompt ??
    ''
  return { pieces, lighting, name: String(artScene?.name ?? scene?.sceneId ?? '').trim() }
}

/* ------------------------------------------------------------------ */
/* 分段：一拍一切 → 合并短切 → 装段 → 吸附整档                            */
/* ------------------------------------------------------------------ */

/** 与上游 expandScript 完全一致的拍秒数：台词按字数，动作用固定值。 */
function beatsOf(scene, params) {
  const p = { ...SCRIPT_DEFAULTS, ...(params ?? {}) }
  return (scene?.flow ?? []).map((b, j) => {
    const isLine = typeof b?.line === 'string'
    return {
      n: j + 1,
      kind: isLine ? 'line' : 'action',
      seconds: r1(isLine ? lineChars(b.line) / p.charsPerSecond : p.actionSeconds),
      speaker: isLine ? b.speaker : undefined,
      delivery: isLine ? String(b.delivery ?? '') : '',
      text: String(isLine ? b.line : b.action ?? ''),
    }
  })
}

/**
 * 一拍一切，再把「短拍」并到邻居：单切目标 2.5–4.5 秒。
 * 台词切只在合并后仍装得下台词时才合并——秒数门由上游逐字对账，不能碰运气。
 */
function cutScene(beats, { minCutSeconds: minCut, maxCutSeconds: maxCut }) {
  const raw = beats.map((b) => ({
    beats: [b.n, b.n],
    items: [b],
    seconds: clamp(r1(b.seconds), minCut, maxCut),
  }))
  const out = []
  for (const c of raw) {
    const prev = out[out.length - 1]
    const joined = prev ? r1(prev.seconds + c.seconds) : Infinity
    const dlg = c.items.concat(prev?.items ?? []).reduce((n, b) => n + (b.kind === 'line' ? b.seconds : 0), 0)
    const short = prev && (prev.seconds < minCut * 1.3 || c.seconds < minCut * 1.3)
    const fits = joined <= Math.min(maxCut, 4.5) && joined >= dlg - 0.01
    if (prev && short && fits && prev.items.length + c.items.length <= 3) {
      prev.items.push(...c.items)
      prev.beats = [prev.beats[0], c.beats[1]]
      prev.seconds = joined
    } else {
      out.push({ ...c, items: [...c.items] })
    }
  }
  return out
}

/**
 * 装段：一条生成一条段，段内切 ≤ 上限，段总秒 ≤ maxSegmentSeconds。
 * 按场均衡装——先算这一场要几段，再装到均值就收口；装满上限会把
 * 一集的时间轴拉长到超出剧本目标（上游 ep-duration 门 ±15%）。
 */
function packSegments(cuts, { maxSegmentSeconds, maxCuts }) {
  const total = r1(cuts.reduce((n, c) => n + c.seconds, 0))
  const want = Math.max(1, Math.ceil(total / maxSegmentSeconds))
  const target = r1(total / want)
  const segs = []
  let cur = []
  let acc = 0
  for (const c of cuts) {
    if (cur.length && (r1(acc + c.seconds) > maxSegmentSeconds || cur.length >= maxCuts || acc >= target)) {
      segs.push(cur)
      cur = []
      acc = 0
    }
    cur.push(c)
    acc = r1(acc + c.seconds)
  }
  if (cur.length) segs.push(cur)
  return segs
}

/** 整集时长预算：超了就把「无台词的 action 切」等比例压一压，底到 minCut 为止。 */
function fitBudget(segments, { targetSeconds, tolerance, minCutSeconds: minCut }) {
  if (!(targetSeconds > 0)) return
  const total = r1(segments.reduce((n, s) => n + s.cuts.reduce((m, c) => m + c.seconds, 0), 0))
  const hi = r1(targetSeconds * (1 + tolerance))
  if (total <= hi) return
  let need = r1(total - targetSeconds)
  const pool = segments
    .flatMap((s) => s.cuts)
    .filter((c) => c.seconds > minCut + 0.01 && c.seconds > dialogueSeconds(c) + 0.01)
    .sort((a, b) => b.seconds - a.seconds)
  for (const cut of pool) {
    if (need <= 0.01) break
    const room = r1(cut.seconds - Math.max(minCut, dialogueSeconds(cut)))
    if (room <= 0.01) continue
    const take = Math.min(room, r1(need / Math.max(1, pool.length) + 0.2))
    if (take <= 0.01) continue
    cut.seconds = r1(cut.seconds - take)
    need = r1(need - take)
  }
}

/** 一个切里台词的秒数：台词装不进去时不能再压。 */
function dialogueSeconds(cut) {
  return r1((cut.items ?? []).reduce((n, b) => n + (b.kind === 'line' ? b.seconds : 0), 0))
}

/* ------------------------------------------------------------------ */
/* 逐切字段：景别 / 运镜 / 构图 / 两种提示词                              */
/* ------------------------------------------------------------------ */

const FIGHT_WORDS = ['爪', '扇飞', '撞', '砸', '扑', '踏碎', '暴起', '骨裂', '出鞘', '劈', '刺穿', '抽出']
const CROWD_WORDS = ['观众', '喧嚣', '狂呼', '司仪', '扩音', '人群', '席']
const HIT = (text) => FIGHT_WORDS.some((w) => text.includes(w))
const CROWD = (text) => CROWD_WORDS.some((w) => text.includes(w))

const LENS = {
  'extreme-wide': '18mm',
  wide: '24mm',
  medium: '50mm',
  close: '85mm',
  'extreme-close': '100mm',
}
const CAMERA_POS = {
  'extreme-wide': '高机位俯拍，距主体约 12 米',
  wide: '略高机位，距主体约 8 米',
  medium: '与人物视线齐平，距主体约 3 米',
  close: '与面部齐平，距主体约 1 米',
  'extreme-close': '贴近面部，距主体约 0.5 米',
}

/** 景别：段首给空间，钩子给细节，其余按台词 / 动作分。 */
function sizeOf(cut, { first, hook }) {
  if (first) return 'wide'
  if (hook) return 'extreme-close'
  const lines = cut.items.filter((b) => b.kind === 'line')
  if (lines.length === cut.items.length) {
    const chars = lines.reduce((n, b) => n + lineChars(b.text), 0)
    return chars <= 4 ? 'extreme-close' : 'close'
  }
  return 'medium'
}

function cameraOf(cut, size) {
  if (size === 'wide') return 'Push In'
  if (size === 'extreme-close') return 'Zoom In'
  if (HIT(cut.items.map((b) => b.text).join(''))) return 'Shake Slightly'
  if (size === 'close') return 'Static Shot'
  return 'Static Shot'
}

/** 中文分镜图提示词：景别 + 光线 + 场景 + 这一段到底看见什么。 */
function frameOf(cut, ctx) {
  const lead = ctx.lighting ? `${ctx.lighting}，` : ''
  const head = `${SHOT_SIZES[cut.size].zh}。${lead}${ctx.sceneName}。`
  const body = cut.items
    .map((b) => {
      if (b.kind === 'action') return b.text
      const who = ctx.speakerName(b.speaker)
      if (b.speaker === 'VO') return '画外音起。'
      return b.delivery ? `${who}${b.delivery}。` : `${who}开口。`
    })
    .join('')
  const names = cut.characters.map((id) => ctx.nameOf(id)).filter(Boolean)
  const many = names.length > 1 ? `${names.join('、')}同框。` : ''
  return head + body + many
}

/** 正文里的角色名换身份代称：H3 与 Seedance 的正文都不许出现角色名。 */
function desensitize(text, ctx) {
  let out = String(text ?? '')
  for (const [name, alias] of ctx.aliasOf) {
    if (alias && alias !== name) out = out.split(name).join(alias)
  }
  return out
}

/** Seedance 镜头正文：中文、不带时间与协议符号、不出现角色名（用身份代称）。 */
function shotOf(cut, ctx) {
  const move = CAMERA_MOVES[cut.camera] ?? '固定'
  const who = cut.characters.map((id) => ctx.identityOf(id)).filter(Boolean)
  const subject = who.length ? `${who.join('与')}` : '空场'
  const body = cut.items
    .map((b) => (b.kind === 'action' ? desensitize(b.text, ctx) : `${subject}开口。`))
    .join('')
  return `${SHOT_SIZES[cut.size].zh}，${move}。${body}`
}

/** H3 画面正文：全英文，画面事实只来自美术设定与人物卡。
 *  场景与光线只在第一个 shot 交代一次；后续 shot 用 the same man 接住，
 *  否则一段四切会把同一段场景描述重复四遍。 */
function h3Body(cut, ctx, index = 0, prev = '') {
  const size = SHOT_SIZES[cut.size].phrase
  const people = cut.characters.map((id) => ctx.enOf(id)).filter((x) => x.length)
  let who
  if (people.length === 1) who = withArticle(people[0].join(', '))
  else if (people.length > 1) who = people.map((p) => `a ${p[0]}`).join(' and ')
  else who = 'the empty set'

  const key = cut.characters.join('|')
  const same = index > 0 && key === prev && people.length > 0
  const subject = same ? (people.length > 1 ? 'the same two figures' : 'the same man') : who
  const head = `${size} of ${subject}`
  if (index > 0) return `${head}.`

  const setting = ctx.scenePieces.length ? ctx.scenePieces.join(', ') : ctx.sceneName
  const light = ctx.lightingEn ? ` ${ctx.lightingEn}.` : ''
  return `${head}. Setting: ${setting}.${light}`.replace(/\s+/g, ' ').trim()
}

/** 台词的 <d> 块：H3 规范里说话人 ID 与「says」在 <d> 外，方括号里只放语言标签。 */
function dialogueOf(cut, ctx) {
  if (!ctx) return ''
  const lines = (cut.items ?? []).filter((b) => b.kind === 'line')
  return lines
    .map((b) => {
      const id = ctx.speakerId(b.speaker, b.delivery)
      const who = ctx.speakerEn(b.speaker, b.delivery)
      if (b.speaker === 'VO' || /画外|OS/i.test(b.delivery ?? '')) {
        return ` ${who} (${id}) says in an off-screen voiceover: <d>[Chinese] ${b.text}</d> while every visible person's lips remain completely closed.`
      }
      return ` ${who} (${id}) says: <d>[Chinese] ${b.text}</d>`
    })
    .join('')
}

/* ------------------------------------------------------------------ */
/* H3 提示词：骨架全部走上游导出                                        */
/* ------------------------------------------------------------------ */

function h3PromptOf(cuts, bodies, sound, music, srcCuts = [], ctx = null) {
  const align = h3AlignmentLine(cuts.map((c) => ({ seconds: c.seconds })), 'en')
  const starts = cutStarts(cuts.map((c) => ({ seconds: c.seconds })))
  const lines = cuts.map((cut, i) => {
    const head = i === 0 ? `[Shot ${i + 1}]` : `[Shot ${i + 1}] At ${h3CutTime(starts[i])},`
    const move = cut.camera.charAt(0).toUpperCase() + cut.camera.slice(1).toLowerCase()
    return `${head} ${move} — ${bodies[i]}${dialogueOf(srcCuts[i] ?? cut, ctx)}`
  })
  return [
    align,
    `integrated_multimodal_description: ${lines.join(' ')}`,
    `overall_soundscape: ${sound}`,
    `non_diegetic_music: ${music}`,
  ].join('\n')
}

function soundOf(texts) {
  const all = texts.join('')
  const parts = []
  if (HIT(all)) parts.push('Heavy flesh impacts, a cracking bone, a beast growling low')
  if (CROWD(all)) parts.push('a roaring crowd echoing off stone walls')
  if (!parts.length) parts.push('Low stone-room ambience, a faint metallic creak')
  return `${parts.join('; ')}.`
}

function musicOf(texts, level) {
  if (level === 'hook') return 'A low string drone tightening into a single struck note.'
  if (HIT(texts.join(''))) return 'A driving percussive bed under a tense string line.'
  return 'A sparse, brooding string bed.'
}

/* ------------------------------------------------------------------ */
/* staging：地点认图 + 开场站位                                          */
/* ------------------------------------------------------------------ */

/** 设定集里认出来的锚点不够 4 件时，用场景本身补——门要求每地 4–20 件。 */
const FILLER_ITEMS = ['地面', '主光位', '入口', '背景墙', '边角杂物']

function itemsOf(artScene, sceneName) {
  const names = []
  for (const a of artScene?.anchors ?? []) {
    const n = String(a?.name ?? '').trim()
    if (n && !names.includes(n)) names.push(n)
  }
  for (const f of FILLER_ITEMS) {
    if (names.length >= 4) break
    if (!names.includes(f)) names.push(f)
  }
  return names.slice(0, 20).map((name, i) => {
    const col = i % 4
    const row = Math.floor(i / 4)
    const left = r2(0.05 + col * 0.23)
    const top = r2(0.06 + row * 0.24)
    return {
      name,
      alt: '',
      // 认图是按提示词推算的：没有图上的框，也就没有 box
      on: false,
      plan: [left, top, r2(left + 0.17), r2(top + 0.18)],
    }
  })
}

function stagingOf({ script, art, outline, ep }) {
  const scenes = []
  const locationIds = []
  const artScenes = artIndex(art)
  const nameOf = new Map((outline?.characters ?? []).map((c) => [c.id, c.name]))

  for (const e of script?.episodes ?? []) {
    if (e.ep !== ep) continue
    ;(e.scenes ?? []).forEach((sc, i) => {
      if (sc.sceneId && !locationIds.includes(sc.sceneId)) locationIds.push(sc.sceneId)
      const cast = (sc.characters ?? []).map((id) => ({ id, name: nameOf.get(id) ?? id }))
      const loc = artScenes.get(sc.sceneId)
      const items = itemsOf(loc, loc?.name ?? sc.sceneId)
      const places = cast.map((c, k) => ({
        id: c.id,
        name: c.name,
        present: true,
        item: items[k % items.length].name,
        side: ['north', 'east', 'south', 'west'][k % 4],
        along: [0.3, 0.62, 0.85][k % 3],
        posture: 'stand',
        facing: ['south', 'north', 'east', 'west'][k % 4],
      }))
      scenes.push({
        ep: e.ep,
        scene: i + 1,
        sceneId: sc.sceneId,
        ...(sc.lighting ? { lighting: sc.lighting } : {}),
        cast,
        places,
        extras: 0,
        extrasWhere: '',
      })
    })
  }

  const locations = locationIds.map((id) => {
    const loc = artScenes.get(id)
    return {
      sceneId: id,
      name: String(loc?.name ?? id),
      readFrom: 'prompt',
      items: itemsOf(loc, loc?.name ?? id),
    }
  })

  return { source: script?.source ?? '', lang: 'zh', locations, scenes }
}

/* ------------------------------------------------------------------ */
/* storyboard：分段 + 逐切 + H3                                          */
/* ------------------------------------------------------------------ */

/** 项目级视觉风格：只取 look.keywords 里实心的前两句，短到能拼进出图/出视频提示词。 */
function lookTextOf(look) {
  const kws = (look?.keywords ?? [])
    .map((s) => String(s ?? '').trim())
    .filter((s) => s && !/[：:]$/.test(s))
  if (!kws.length) return ''
  return kws[0]
    .replace(/^(视觉|材质|画风|风格)关键词[：:]\s*/, '')
    .split('。')
    .filter(Boolean)
    .slice(0, 2)
    .join('。')
}

function storyboardOf({ script, art, cast, outline, look, ep }) {
  const artScenes = artIndex(art)
  const people = castIndex(outline, cast)
  const byId = new Map(people.map((p) => [p.id, p]))
  const lookText = lookTextOf(look)

  const epDoc = (script?.episodes ?? []).find((e) => e.ep === ep)
  if (!epDoc) throw new Error(`script.json 里没有第 ${ep} 集`)

  // 单拍最长秒数决定切长上限：长台词（如契灵宣告）必须放得进一刀
  const allBeats = (epDoc.scenes ?? []).flatMap((sc) => beatsOf(sc, script?.params))
  const maxCutSeconds = Math.max(BASE_PARAMS.maxCutSeconds, Math.ceil(Math.max(0, ...allBeats.map((b) => b.seconds))))
  const params = {
    ...BASE_PARAMS,
    maxCutSeconds,
    minCutSeconds: Math.min(BASE_PARAMS.minCutSeconds, maxCutSeconds),
  }

  // 出场者名字 → 身份代称：正文不许出现角色名，画面描述里换一个同义身份
  const aliasOf = new Map(people.map((p) => [p.name, p.identity || p.role || '人物']))

  // 说话人 ID 跨段稳定（H3 规范：同一说话人全程同一个 ID）
  const idOf = new Map(people.map((p, i) => [p.id, `S${i + 1}`]))
  const voIds = new Map()
  for (const sc of epDoc.scenes ?? []) {
    for (const b of sc.flow ?? []) {
      if (typeof b.line !== 'string' || b.speaker !== 'VO') continue
      const key = String(b.delivery ?? '').split(/[，,]/)[0].trim()
      if (!key || voIds.has(key)) continue
      const owner = people.find((p) => p.name === key)
      voIds.set(key, owner ? idOf.get(owner.id) : `S${people.length + voIds.size + 1}`)
    }
  }
  const voiceKey = (delivery) => String(delivery ?? '').split(/[，,]/)[0].trim()

  const label = `E${String(ep).padStart(2, '0')}`
  const segments = []
  const pending = []
  let segNo = 0

  ;(epDoc.scenes ?? []).forEach((sc, sceneIdx) => {
    const artScene = artScenes.get(sc.sceneId)
    const brief = sceneBrief(sc, artScene)
    const beats = beatsOf(sc, script?.params)
    if (!beats.length) return

    const ctx = {
      sceneName: brief.name || sc.sceneId,
      scenePieces: brief.pieces,
      lighting: sc.lighting ? `${sc.lighting}` : '',
      lightingEn: brief.lighting,
      aliasOf,
      nameOf: (id) => byId.get(id)?.name ?? id,
      identityOf: (id) => byId.get(id)?.identity ?? '',
      enOf: (id) => byId.get(id)?.en ?? [],
      speakerId: (sp, delivery) => {
        if (byId.has(sp)) return idOf.get(sp)
        return voIds.get(voiceKey(delivery)) ?? `S${people.length + 1}`
      },
      speakerEn: (sp, delivery) => {
        if (byId.has(sp)) return `The ${byId.get(sp).en[0] ?? 'figure'}`
        const key = voiceKey(delivery)
        const named = people.find((p) => p.name === key)
        if (named) return `The ${named.en[0] ?? 'figure'}`
        if (/司仪/.test(key)) return 'The arena announcer'
        if (/观众/.test(key)) return 'The crowd'
        if (/契灵|灵/.test(key)) return 'An echoing ritual voice'
        return 'An off-screen voice'
      },
      speakerName: (sp) => (byId.has(sp) ? byId.get(sp).name : sp === 'VO' ? '画外音' : String(sp ?? '有人')),
    }

    const cuts = cutScene(beats, params)
    for (const seg of packSegments(cuts, { maxSegmentSeconds: params.maxSegmentSeconds, maxCuts: 9 })) {
      segNo += 1
      const sid = `${label}-${String(segNo).padStart(2, '0')}`

      const outCuts = seg.map((c, ci) => {
        const texts = c.items.map((b) => b.text)
        // 场收尾那一拍给大特写：短剧的悬念收在细节上
        const isLastBeat = c.items.some((b) => b.n === beats.length)
        const size = sizeOf(c, { first: ci === 0, hook: isLastBeat })
        const camera = cameraOf(c, size)
        const speakers = c.items.filter((b) => b.kind === 'line').map((b) => b.speaker).filter((s) => byId.has(s))
        const picked = [...new Set([...speakers, ...(c.items.some((b) => b.kind === 'action') ? sc.characters ?? [] : [])])]
          .filter((id) => (sc.characters ?? []).includes(id))
          .slice(0, params.maxOnScreen)
        const characters = picked.length ? picked : (sc.characters ?? []).slice(0, params.maxOnScreen)
        const body = {
          beats: c.beats,
          seconds: c.seconds,
          size,
          camera,
          characters,
          props: [],
          // 平台扩展：画幅与项目级风格跟着每个切走，M3 出图/出视频直接拼
          aspectRatio: '9:16',
          look: lookText,
        }
        return {
          ...body,
          // items 只在生成阶段用于算台词秒数，输出前会删
          items: c.items,
          frame: frameOf({ ...c, size, characters }, ctx),
          shot: shotOf({ ...c, size, camera, characters }, ctx),
          lens: LENS[size],
          cameraPosition: CAMERA_POS[size],
          composition: characters.length > 1 ? '两人左右分立，各占一侧三分线，中间留出走位空间' : '人物居中，四周留白',
          eyeline: c.items.some((b) => b.kind === 'line') ? '看向画外的对话对象' : '看向画面深处',
          focus: size === 'close' || size === 'extreme-close' ? '面部清晰，背景柔和虚化' : '全景深，人物与环境都清晰',
          stability: HIT(texts.join('')) ? 'slight-shake' : 'stable',
          sfx: '',
          lighting: sc.lighting ?? '',
        }
      })

      const bodies = outCuts.map((c, ci) => h3Body(c, ctx, ci, ci ? outCuts[ci - 1].characters.join('|') : ''))
      const texts = seg.flatMap((c) => c.items.map((b) => b.text))
      const names = [...new Set(outCuts.flatMap((c) => c.characters))].map((id) => ctx.nameOf(id))
      segments.push({
        id: sid,
        sceneIndex: sceneIdx + 1,
        cuts: outCuts,
        h3Prompt: '',
        blocking:
          names.length > 1
            ? `${ctx.sceneName}内，${names.join('、')}同处一处；${names[0]}位于画面左侧偏前，其余人物在右后方，中间留出一步半的距离。`
            : `${ctx.sceneName}内，${names[0] ?? '空场'}位于画面中央，四周空出大片空间。`,
        soundscape: soundOf(texts),
        music: musicOf(texts, ''),
      })
      pending.push({ seg, bodies, texts, ctx })
    }
  })

  // 时长预算在 H3 提示词之前定稿：切点时刻由切秒数累加，改一秒就得重拼
  fitBudget(segments, {
    targetSeconds: epDoc.targetSeconds,
    tolerance: params.tolerance,
    minCutSeconds: params.minCutSeconds,
  })
  segments.forEach((s, i) => {
    const p = pending[i]
    s.h3Prompt = h3PromptOf(s.cuts, p.bodies, s.soundscape, s.music, p.seg, p.ctx)
    // items 是生成期的中间量，不进出图契约
    for (const cut of s.cuts) delete cut.items
  })

  return {
    source: script?.source ?? '',
    aspectRatio: '9:16',
    promptLang: 'en',
    ...(lookText ? { look: lookText } : {}),
    params,
    episodes: [{ ep, segments }],
  }
}

/* ------------------------------------------------------------------ */
/* 导出与 CLI                                                          */
/* ------------------------------------------------------------------ */

/** 生成一份文档：kind 只支持 staging / storyboard。 */
export function seed(kind, payload) {
  const ep = Number(payload?.ep ?? 1)
  if (!Number.isInteger(ep) || ep < 1) throw new Error(`ep 必须是正整数，收到 ${payload?.ep}`)
  if (kind === 'staging') return stagingOf({ ...payload, ep })
  if (kind === 'storyboard') return storyboardOf({ ...payload, ep })
  throw new Error(`seed 只支持 staging / storyboard，收到 ${kind}`)
}

const isMain = process.argv[1] ? import.meta.url === pathToFileURL(process.argv[1]).href : false

if (isMain) {
  try {
    const kind = process.argv[2]
    if (!['staging', 'storyboard'].includes(kind)) {
      throw new Error('用法：studio_seed.mjs <staging|storyboard> [payload.json]')
    }
    const raw = process.argv[3] ? readFileSync(process.argv[3], 'utf8') : readFileSync(0, 'utf8')
    if (!raw.trim()) throw new Error('payload 为空：需要 JSON（stdin 或文件）')
    const doc = seed(kind, JSON.parse(raw))
    process.stdout.write(JSON.stringify({ ok: true, kind, doc }))
  } catch (error) {
    process.stderr.write((error instanceof Error ? error.stack : String(error)) + '\n')
    process.exit(2)
  }
}

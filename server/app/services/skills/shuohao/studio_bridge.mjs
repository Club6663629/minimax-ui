#!/usr/bin/env node
/**
 * studio_bridge — 剧集工坊与 shuohao-skills 之间的唯一接口。
 *
 * 只做三件事：把上游导出的门/统计/渲染函数包成 JSON，别的什么都不加。
 * 门规则、schema、报告全部留在 skills/ 里，升级上游不用改这个文件。
 *
 *   node studio_bridge.mjs gates <script|cast|art|staging|storyboard|outline> [payload.json]
 *   node studio_bridge.mjs stats <script|storyboard> [payload.json]
 *   node studio_bridge.mjs render <script|cast|art|staging|storyboard|outline> [payload.json]
 *   node studio_bridge.mjs seed <staging|storyboard> [payload.json]
 *
 * payload（stdin 或文件）：
 *   { doc, script, cast, art, outline, sourceText, lang }
 *   doc 是要检查的那份；其余是上下文（上游门需要交叉对账）。
 *
 * 输出：stdout 一个 JSON `{ok, kind, gates, problems, stats}`；
 *       seed 输出 `{ok, kind, doc}`；render 直接输出 HTML 文本。失败 exit 2，错误写 stderr。
 */
import { readFileSync, existsSync } from 'node:fs'

import { seed } from './studio_seed.mjs'

import {
  gateReport as scriptGates,
  validateScript,
  computeStats as scriptStats,
  renderHtml as renderScriptHtml,
  renderMarkdown as renderScriptMd,
} from './skills/novel-script/scripts/novel-script.mjs'
import {
  validateCast,
  renderHtml as renderCastHtml,
  renderMarkdown as renderCastMd,
} from './skills/novel-characters/scripts/novel-characters.mjs'
import {
  gateReport as artGates,
  validateArt,
  castNamesOf,
  renderHtml as renderArtHtml,
  renderMarkdown as renderArtMd,
} from './skills/novel-art/scripts/novel-art.mjs'
import {
  gateReport as stagingGates,
  validateStaging,
  renderHtml as renderStagingHtml,
  renderMarkdown as renderStagingMd,
} from './skills/novel-staging/scripts/novel-staging.mjs'
import {
  gateReport as boardGates,
  validateStoryboard,
  computeStats as boardStats,
  renderHtml as renderBoardHtml,
  renderMarkdown as renderBoardMd,
} from './skills/novel-storyboard/scripts/novel-storyboard.mjs'
import {
  gateReport as outlineGates,
  validateOutline,
  renderHtml as renderOutlineHtml,
  renderMarkdown as renderOutlineMd,
} from './skills/novel-outline/scripts/novel-outline.mjs'

const KINDS = new Set(['script', 'cast', 'art', 'staging', 'storyboard', 'outline'])

function readPayload(arg) {
  const raw = arg && existsSync(arg) ? readFileSync(arg, 'utf8') : readFileSync(0, 'utf8')
  if (!raw.trim()) throw new Error('payload 为空：需要 JSON（stdin 或文件）')
  return JSON.parse(raw)
}

function ctxOf(p) {
  return {
    script: p.script ?? null,
    cast: p.cast ?? null,
    art: p.art ?? null,
    outline: p.outline ?? null,
    sourceText: p.sourceText ?? null,
    lang: p.lang ?? 'zh',
  }
}

/** 把 validate* 的字符串问题折成一道门，供上游没有 gateReport 的 kind 使用。 */
function fold(id, label, problems) {
  return [{
    id,
    label,
    ok: problems.length === 0,
    detail: problems.length ? problems.slice(0, 3).join('；') + (problems.length > 3 ? ` 等 ${problems.length} 处` : '') : '',
  }]
}

function runGates(kind, p) {
  const ctx = ctxOf(p)
  const doc = p.doc
  if (!doc || typeof doc !== 'object') throw new Error('缺少 doc')

  let gates = []
  let problems = []
  let stats = null

  if (kind === 'script') {
    gates = scriptGates(doc, ctx)
    problems = validateScript(doc, ctx)
    stats = scriptStats(doc)
  } else if (kind === 'cast') {
    const characters = Array.isArray(doc.characters) ? doc.characters : []
    problems = validateCast(characters, ctx.sourceText, ctx.lang)
    gates = fold('cast-validate', '角色卡完整', problems)
  } else if (kind === 'art') {
    const names = ctx.cast ? castNamesOf(ctx.cast) : null
    gates = artGates(doc, names)
    problems = validateArt(doc, names)
  } else if (kind === 'staging') {
    gates = stagingGates(doc, { script: ctx.script, art: ctx.art })
    problems = validateStaging(doc, { script: ctx.script, art: ctx.art })
  } else if (kind === 'storyboard') {
    gates = boardGates(doc, ctx)
    problems = validateStoryboard(doc, ctx)
    stats = boardStats(doc, ctx.script)
  } else if (kind === 'outline') {
    gates = outlineGates(doc)
    problems = validateOutline(doc, 'full')
  } else {
    throw new Error(`未知 kind：${kind}`)
  }

  return { ok: true, kind, gates, problems, stats }
}

function runStats(kind, p) {
  if (kind === 'script') return { ok: true, kind, stats: scriptStats(p.doc) }
  if (kind === 'storyboard') return { ok: true, kind, stats: boardStats(p.doc, p.script ?? null) }
  throw new Error(`stats 只支持 script / storyboard，收到 ${kind}`)
}

function runRender(kind, p) {
  const ctx = ctxOf(p)
  const doc = p.doc
  const md = p.format === 'md'
  if (kind === 'script') return md ? renderScriptMd(doc, ctx) : renderScriptHtml(doc, ctx)
  if (kind === 'cast') {
    return md
      ? renderCastMd(doc.characters ?? [], doc.source ?? '', doc.summary ?? '', ctx.lang, doc.ui ?? null)
      : renderCastHtml(doc.characters ?? [], doc.source ?? '', doc.summary ?? '', ctx.lang, doc.ui ?? null)
  }
  if (kind === 'art') return md ? renderArtMd(doc, ctx.lang) : renderArtHtml(doc, ctx.lang)
  if (kind === 'staging') return md ? renderStagingMd(doc, ctx.lang) : renderStagingHtml(doc, ctx.lang)
  if (kind === 'storyboard') return md ? renderBoardMd(doc, ctx) : renderBoardHtml(doc, ctx)
  if (kind === 'outline') return md ? renderOutlineMd(doc, ctx.lang) : renderOutlineHtml(doc, ctx.lang)
  throw new Error(`未知 kind：${kind}`)
}

function main(argv) {
  const [cmd, kind, arg] = argv
  if (!cmd) throw new Error('用法：studio_bridge.mjs <gates|stats|render|seed> <kind> [payload.json]')

  const p = readPayload(arg)
  if (cmd === 'render') {
    process.stdout.write(runRender(kind, p))
    return
  }
  if (cmd === 'seed') {
    if (!['staging', 'storyboard'].includes(kind)) {
      throw new Error(`seed 只支持 staging / storyboard，收到 ${kind}`)
    }
    process.stdout.write(JSON.stringify({ ok: true, kind, doc: seed(kind, p) }))
    return
  }
  if (!KINDS.has(kind)) throw new Error(`kind 必须是 ${[...KINDS].join(' / ')}，收到 ${kind}`)

  let out
  if (cmd === 'gates') out = runGates(kind, p)
  else if (cmd === 'stats') out = runStats(kind, p)
  else throw new Error(`未知命令：${cmd}`)

  process.stdout.write(JSON.stringify(out))
}

try {
  main(process.argv.slice(2))
} catch (error) {
  process.stderr.write((error instanceof Error ? error.message : String(error)) + '\n')
  process.exit(2)
}

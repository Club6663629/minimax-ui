#!/usr/bin/env bash
# 同步 shuohao-skills 上游到本目录（vendoring），并跑上游自测。
#
# 用法：
#   ./upgrade.sh <tag-or-sha> [--with-smoke] [--no-test]
#
#   --no-test     只同步，不跑自测（应急跳过）
#   --with-smoke  额外跑平台侧三个 smoke（需要 server/venv 与本地剧本 fixture）
#
# 环境变量：
#   SHUOHAO_REPO  覆盖仓库地址，便于镜像/离线复现，支持 file:// 本地路径
#
# 只覆盖上游的 skills/ 与 scripts/；studio_bridge.mjs / studio_seed.mjs 不受影响。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${SHUOHAO_REPO:-https://github.com/eternityspring/shuohao-skills}"
REF=""
RUN_TEST=1
RUN_SMOKE=0

usage() {
  cat <<'EOF'
用法：upgrade.sh <tag-or-sha> [--with-smoke] [--no-test]

  --no-test     只同步，不跑自测（应急跳过）
  --with-smoke  额外跑平台侧三个 smoke（需要 server/venv 与本地剧本 fixture）

环境变量：
  SHUOHAO_REPO  覆盖仓库地址，便于镜像/离线复现，支持 file:// 本地路径

只覆盖上游的 skills/ 与 scripts/；studio_bridge.mjs / studio_seed.mjs 不受影响。
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --no-test) RUN_TEST=0 ;;
    --with-smoke) RUN_SMOKE=1 ;;
    -*) echo "未知参数：$arg" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "$REF" ]]; then
        echo "只接受一个 ref，多出的：$arg" >&2
        exit 2
      fi
      REF="$arg"
      ;;
  esac
done

if [[ -z "$REF" ]]; then
  usage >&2
  exit 2
fi

for bin in git rsync node; do
  command -v "$bin" >/dev/null 2>&1 || { echo "缺少依赖：$bin" >&2; exit 1; }
done

WORK="$(mktemp -d "${TMPDIR:-/tmp}/shuohao.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo "==> 拉取 $REPO @ $REF"
git init --quiet "$WORK"
git -C "$WORK" remote add origin "$REPO"
git -C "$WORK" fetch --quiet --depth 1 origin "$REF"
git -C "$WORK" checkout --quiet FETCH_HEAD

SHA="$(git -C "$WORK" rev-parse HEAD)"
DATE="$(git -C "$WORK" log -1 --format=%cs)"
echo "    提交 ${SHA}（${DATE}）"

echo "==> 同步 skills/ 与 scripts/（图片资产不 vendor，*.txt 必须保留）"
rsync -a \
  --include='*/' --include='*.md' --include='*.mjs' --include='*.json' --include='*.txt' \
  --exclude='*' "$WORK/skills/" "$ROOT/skills/"
rsync -a \
  --include='*/' --include='*.mjs' --exclude='*' \
  "$WORK/scripts/" "$ROOT/scripts/"
cp "$WORK/LICENSE" "$WORK/NOTICE" "$ROOT/"

echo "==> 回填 SOURCE.md 的提交号与日期"
SOURCE_FILE="$ROOT/SOURCE.md" NEW_SHA="$SHA" NEW_DATE="$DATE" node - <<'NODE'
const fs = require('fs')
const file = process.env.SOURCE_FILE
const sha = process.env.NEW_SHA
const date = process.env.NEW_DATE
const text = fs.readFileSync(file, 'utf8')
const next = text.replace(/- 提交：`[0-9a-f]{7,40}`（[^）]*）/, `- 提交：\`${sha}\`（${date}）`)
if (!next.includes('`' + sha + '`')) {
  console.error('SOURCE.md 里找不到「提交：`<sha>`（<date>）」这一行，请手工回填：' + sha)
  process.exit(1)
}
if (next !== text) fs.writeFileSync(file, next)
NODE

if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "==> 变更文件"
  git -C "$ROOT" status --porcelain -- . | sed 's/^/    /'
fi

if [[ "$RUN_TEST" == "1" ]]; then
  echo "==> 上游自测"
  for skill in novel-outline novel-script novel-characters novel-art novel-staging novel-storyboard character-refs; do
    printf '    %-20s ' "$skill"
    if log="$(node "$ROOT/skills/$skill/scripts/selftest.mjs" 2>&1)"; then
      echo "OK"
    else
      echo "FAIL"
      echo "$log" | tail -20 | sed 's/^/      /'
      exit 1
    fi
  done
  printf '    %-20s ' "report"
  if log="$(node "$ROOT/scripts/report-selftest.mjs" 2>&1)"; then
    echo "OK"
  else
    echo "FAIL"
    echo "$log" | tail -20 | sed 's/^/      /'
    exit 1
  fi
fi

if [[ "$RUN_SMOKE" == "1" ]]; then
  PY="$ROOT/../../../../venv/bin/python"
  if [[ ! -x "$PY" ]]; then
    echo "==> 跳过平台 smoke：找不到 $PY" >&2
  else
    echo "==> 平台 smoke"
    for smoke in studio_keyframe_smoke studio_clip_smoke studio_render_smoke; do
      printf '    %-24s ' "$smoke"
      if log="$("$PY" "$ROOT/../../../../../tmp/$smoke.py" 2>&1 | tail -1)"; then
        echo "$log"
      else
        echo "FAIL"
        echo "$log" | sed 's/^/      /'
        exit 1
      fi
    done
  fi
fi

echo "==> 完成：${SHA}（${DATE}）"
[[ "$RUN_TEST" == "1" ]] || echo "    注意：本次跳过了自测（--no-test）"

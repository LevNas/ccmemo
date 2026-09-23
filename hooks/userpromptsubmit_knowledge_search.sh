#!/usr/bin/env bash
# ccmemo UserPromptSubmit hook: auto-search knowledge entries by prompt keywords.
#
# Reads prompt JSON from stdin, extracts Japanese nouns with mecab, searches
# .claude/knowledge/entries/ with rg, and injects the top N hits as
# additionalContext so Claude sees them before answering.
#
# Only entries whose frontmatter status is in CCMEMO_SEARCH_STATUS surface
# (default: active; entries without a status line count as active; "all"
# disables the filter). Non-active entries that are deliberately allowed
# through are annotated with their status and superseded_by target.
set -euo pipefail

MAX_RESULTS=5
MIN_WORD_LEN=2
# Each candidate also shows its frontmatter `description` (the trigger
# condition: when to open the entry), cut to this many characters, so the
# model can pick one candidate without opening several.
DESC_CHARS=${CCMEMO_SEARCH_DESC_CHARS:-80}
HIT_COUNT_LIMIT=50
ENTRIES_DIR=".claude/knowledge/entries"
ALLOWED_STATUS="${CCMEMO_SEARCH_STATUS:-active}"

# Frontmatter is read by the shared parser (hooks/lib/frontmatter.py) so the
# hook, the index and the graph CLI agree on tag forms and the status default.
FRONTMATTER_PY="$(dirname "${BASH_SOURCE[0]}")/lib/frontmatter.py"

# Silently no-op if required tools or entries dir are missing.
command -v jq >/dev/null 2>&1 || exit 0
command -v rg >/dev/null 2>&1 || exit 0
command -v mecab >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$FRONTMATTER_PY" ] || exit 0
[ -d "$ENTRIES_DIR" ] || exit 0

INPUT=$(cat)
PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // empty')
[ -n "$PROMPT" ] || exit 0

# Extract nouns (>= MIN_WORD_LEN chars), excluding 非自立/代名詞/数/接尾.
KEYWORDS=$(printf '%s' "$PROMPT" | mecab | awk -F'\t|,' -v min_len="$MIN_WORD_LEN" '
  $2 == "名詞" && $3 !~ /^(非自立|代名詞|数|接尾)$/ && length($1) >= min_len {
    print $1
  }
' | sort -u)

[ -n "$KEYWORDS" ] || exit 0

# Temporarily disable -u for associative-array usage (bash bug with empty arrays).
set +u
declare -A file_scores

while IFS= read -r word; do
  [ -n "$word" ] || continue
  mapfile -t hits < <(rg -l --fixed-strings -- "$word" "$ENTRIES_DIR" 2>/dev/null || true)
  hit_count=${#hits[@]}
  if [ "$hit_count" -eq 0 ] || [ "$hit_count" -gt "$HIT_COUNT_LIMIT" ]; then
    continue
  fi
  for f in "${hits[@]}"; do
    current=${file_scores[$f]:-0}
    file_scores[$f]=$(awk -v c="$current" -v h="$hit_count" 'BEGIN {printf "%.4f", c + 1/h}')
  done
done <<< "$KEYWORDS"

[ "${#file_scores[@]}" -gt 0 ] || exit 0
set -u

# Rank candidates, then read status / superseded_by / title for all of them
# in ONE parser call (one interpreter start, not one per file). The parser
# reads the frontmatter block only, so body text that happens to start a
# line with "status:" cannot leak in, and a missing status is "active".
mapfile -t ranked < <(
  for f in "${!file_scores[@]}"; do
    printf '%s %s\n' "${file_scores[$f]}" "$f"
  done | sort -rn | sed 's/^[^ ]* //'
)
# Separator is US (0x1f), not tab: bash `read` collapses runs of whitespace
# IFS characters, which would shift columns whenever superseded_by is empty.
declare -A fm_status fm_superseded fm_title fm_desc
while IFS=$'\x1f' read -r fpath fstatus fsuperseded ftitle fdesc; do
  [ -n "$fpath" ] || continue
  fm_status[$fpath]=$fstatus
  fm_superseded[$fpath]=$fsuperseded
  fm_title[$fpath]=$ftitle
  fm_desc[$fpath]=$fdesc
done < <(python3 "$FRONTMATTER_PY" --sep $'\x1f' --fields status,superseded_by,title,description -- "${ranked[@]}" 2>/dev/null || true)

# Walk the ranking until MAX_RESULTS allowed entries are collected, so
# entries dropped by the status filter do not consume result slots.
RESULTS=""
result_count=0
for filepath in "${ranked[@]}"; do
  [ -n "$filepath" ] || continue
  status=${fm_status[$filepath]:-active}
  superseded_by=${fm_superseded[$filepath]:-}
  if [ "$ALLOWED_STATUS" != "all" ]; then
    case ",${ALLOWED_STATUS}," in
      *",${status},"*) ;;
      *) continue ;;
    esac
  fi
  title=${fm_title[$filepath]:-}
  [ -n "$title" ] || title=$(basename "$filepath" .md)
  note=""
  if [ "$status" != "active" ]; then
    note=" [status: ${status}"
    [ -n "$superseded_by" ] && note="${note}, superseded_by: ${superseded_by}"
    note="${note}]"
  fi
  RESULTS="${RESULTS}- ${title} (${filepath})${note}"$'\n'
  desc=${fm_desc[$filepath]:-}
  if [ -n "$desc" ] && [ "$DESC_CHARS" -gt 0 ]; then
    if [ "${#desc}" -gt "$DESC_CHARS" ]; then
      desc="${desc:0:$DESC_CHARS}…"
    fi
    RESULTS="${RESULTS}  when: ${desc}"$'\n'
  fi
  result_count=$((result_count + 1))
  [ "$result_count" -lt "$MAX_RESULTS" ] || break
done

[ -n "$RESULTS" ] || exit 0

CONTEXT=$(printf '関連ナレッジ候補 (ccmemo auto-search):\n%s\n先に該当エントリを確認してから回答すること。' "$RESULTS")
printf '%s' "$CONTEXT" | jq -Rs '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: .}}'

exit 0

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
HIT_COUNT_LIMIT=50
ENTRIES_DIR=".claude/knowledge/entries"
ALLOWED_STATUS="${CCMEMO_SEARCH_STATUS:-active}"

# Silently no-op if required tools or entries dir are missing.
command -v jq >/dev/null 2>&1 || exit 0
command -v rg >/dev/null 2>&1 || exit 0
command -v mecab >/dev/null 2>&1 || exit 0
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

# Walk the ranking until MAX_RESULTS allowed entries are collected, so
# entries dropped by the status filter do not consume result slots.
RESULTS=""
result_count=0
while IFS= read -r line; do
  filepath=${line#* }
  [ -n "$filepath" ] || continue
  # Read status / superseded_by from the frontmatter block only, so body
  # text that happens to start a line with "status:" cannot leak in.
  status=""
  superseded_by=""
  IFS=$'\t' read -r status superseded_by < <(awk '
    { sub(/\r$/, "") }
    NR == 1 { if ($0 != "---") exit; next }
    $0 == "---" { exit }
    sub(/^status:[[:space:]]*/, "")        { gsub(/"/, ""); sub(/[[:space:]]+$/, ""); s = $0 }
    sub(/^superseded_by:[[:space:]]*/, "") { gsub(/"/, ""); sub(/[[:space:]]+$/, ""); sb = $0 }
    END { printf "%s\t%s\n", s, sb }
  ' "$filepath" 2>/dev/null) || true
  status=${status:-active}
  if [ "$ALLOWED_STATUS" != "all" ]; then
    case ",${ALLOWED_STATUS}," in
      *",${status},"*) ;;
      *) continue ;;
    esac
  fi
  # `|| true`: with pipefail a title-less file would otherwise abort the
  # whole hook (rg exits 1), making the basename fallback unreachable.
  title=$(rg --no-filename '^title:' "$filepath" 2>/dev/null | head -1 | sed 's/^title:[[:space:]]*//' | tr -d '"' || true)
  [ -n "$title" ] || title=$(basename "$filepath" .md)
  note=""
  if [ "$status" != "active" ]; then
    note=" [status: ${status}"
    [ -n "$superseded_by" ] && note="${note}, superseded_by: ${superseded_by}"
    note="${note}]"
  fi
  RESULTS="${RESULTS}- ${title} (${filepath})${note}"$'\n'
  result_count=$((result_count + 1))
  [ "$result_count" -lt "$MAX_RESULTS" ] || break
done < <(
  for f in "${!file_scores[@]}"; do
    printf '%s %s\n' "${file_scores[$f]}" "$f"
  done | sort -rn
)

[ -n "$RESULTS" ] || exit 0

CONTEXT=$(printf '関連ナレッジ候補 (ccmemo auto-search):\n%s\n先に該当エントリを確認してから回答すること。' "$RESULTS")
printf '%s' "$CONTEXT" | jq -Rs '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: .}}'

exit 0

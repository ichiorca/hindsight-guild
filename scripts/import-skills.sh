#!/usr/bin/env bash
# Import skills from a Claude Skills-format repo into our skills/ directory.
#
# Examples:
#   ./scripts/import-skills.sh --from ~/Downloads/marketingskills-main \
#       --skills copywriting,ab-testing,cro,customer-research,emails,onboarding
#
#   ./scripts/import-skills.sh --from ~/Downloads/claude-ads-main \
#       --skills ads-meta,ads-google,ads-linkedin,ads-creative,ads-attribution
#
# The script:
#  1. Copies each named skill directory from <from>/skills/ into ./skills/
#  2. Validates that each skill's SKILL.md parses (frontmatter has name + description)
#  3. Adds an MIT-attribution header comment if the upstream LICENSE is MIT
#  4. Reminds you to add the new skill name to agents/_skills_config.py
set -euo pipefail

FROM=""
SKILLS=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --from)   FROM="$2"; shift 2 ;;
    --skills) SKILLS="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --from <repo-path> --skills <name1,name2,...>"
      exit 0 ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$FROM"   ]] && { echo "--from required"; exit 1; }
[[ -z "$SKILLS" ]] && { echo "--skills required"; exit 1; }

# Find the actual skills root — both source repos sometimes nest under
# <repo>/<repo>/skills/ depending on how they were extracted.
SOURCE_ROOT=""
for candidate in "$FROM/skills" "$FROM"/*/skills; do
  if [[ -d "$candidate" ]]; then SOURCE_ROOT="$candidate"; break; fi
done
[[ -z "$SOURCE_ROOT" ]] && { echo "could not find skills/ under $FROM"; exit 1; }

DEST_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")/skills"
echo "Source: $SOURCE_ROOT"
echo "Dest:   $DEST_ROOT"
echo ""

# Detect upstream license for attribution
LICENSE_NOTE=""
if grep -q "MIT" "$FROM"/{LICENSE,LICENSE.md} 2>/dev/null; then
  REPO_NAME="$(basename "$FROM")"
  LICENSE_NOTE="Imported from upstream $REPO_NAME (MIT License). See upstream LICENSE."
fi

IFS=',' read -r -a SKILL_NAMES <<< "$SKILLS"
IMPORTED=()
SKIPPED=()
FAILED=()

for skill in "${SKILL_NAMES[@]}"; do
  skill="${skill// /}"   # trim whitespace
  src="$SOURCE_ROOT/$skill"
  dst="$DEST_ROOT/$skill"

  if [[ ! -d "$src" ]]; then
    echo "  ✗ $skill — not found in source"
    FAILED+=("$skill (not in source)")
    continue
  fi
  if [[ ! -f "$src/SKILL.md" ]]; then
    echo "  ✗ $skill — no SKILL.md at $src"
    FAILED+=("$skill (no SKILL.md)")
    continue
  fi

  if [[ -d "$dst" ]]; then
    echo "  ⊙ $skill — already exists locally; skipping (delete it first to re-import)"
    SKIPPED+=("$skill")
    continue
  fi

  mkdir -p "$dst"
  cp -r "$src"/. "$dst"/

  # Inject MIT attribution header if upstream is MIT
  if [[ -n "$LICENSE_NOTE" ]]; then
    skill_md="$dst/SKILL.md"
    tmp="$skill_md.tmp"
    awk -v note="<!-- $LICENSE_NOTE -->" '
      /^---$/ && !seen { seen=1; print; next }
      /^---$/ &&  seen && !injected { print; print ""; print note; print ""; injected=1; next }
      { print }
    ' "$skill_md" > "$tmp" && mv "$tmp" "$skill_md"
  fi

  # Quick sanity check — does it have YAML frontmatter with name + description?
  if ! head -20 "$dst/SKILL.md" | grep -q "^name:"; then
    echo "  ✗ $skill — SKILL.md missing 'name:' frontmatter"
    rm -rf "$dst"
    FAILED+=("$skill (no name in frontmatter)")
    continue
  fi
  if ! head -20 "$dst/SKILL.md" | grep -q "^description:"; then
    echo "  ✗ $skill — SKILL.md missing 'description:' frontmatter"
    rm -rf "$dst"
    FAILED+=("$skill (no description in frontmatter)")
    continue
  fi

  echo "  ✓ $skill"
  IMPORTED+=("$skill")
done

echo ""
echo "==> Import summary"
echo "  Imported: ${#IMPORTED[@]}"
[[ ${#IMPORTED[@]} -gt 0 ]] && printf "    - %s\n" "${IMPORTED[@]}"
echo "  Skipped:  ${#SKIPPED[@]}"
[[ ${#SKIPPED[@]} -gt 0 ]] && printf "    - %s\n" "${SKIPPED[@]}"
echo "  Failed:   ${#FAILED[@]}"
[[ ${#FAILED[@]} -gt 0 ]] && printf "    - %s\n" "${FAILED[@]}"

if [[ ${#IMPORTED[@]} -gt 0 ]]; then
  echo ""
  echo "==> Next step: add the imported skill names to the appropriate agent"
  echo "    allowlists in agents/_skills_config.py."
fi

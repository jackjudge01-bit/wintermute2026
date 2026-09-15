#!/usr/bin/env bash
# Pulls the data points needed to judge whether a HuggingFace model repo is
# a genuine release or a spam/rebrand, in one shot. Uses the public API
# directly (no `hf` CLI / auth needed) so it works anywhere curl does.
#
# This does NOT give you a verdict -- see README.md for why that judgment
# has to stay manual -- it just gathers everything you'd otherwise fetch
# by hand across several requests.
set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <org/repo-name>" >&2
    exit 1
fi

REPO="$1"

echo "=== Model info ==="
curl -s "https://huggingface.co/api/models/${REPO}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'error' in d:
    print('ERROR:', d['error'])
    sys.exit(1)
print('id:', d.get('id'))
print('author:', d.get('author'))
print('downloads:', d.get('downloads'))
print('likes:', d.get('likes'))
print('pipeline_tag:', d.get('pipeline_tag'))
print('library_name:', d.get('library_name'))
print('last_modified:', d.get('lastModified'))
print('tags:', d.get('tags'))
base_models = [t for t in (d.get('tags') or []) if t.startswith('base_model:')]
if base_models:
    print('base_model tags:', base_models)
"

echo
echo "=== Author/org info ==="
AUTHOR=$(curl -s "https://huggingface.co/api/models/${REPO}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('author',''))")
if [ -n "$AUTHOR" ]; then
    curl -s "https://huggingface.co/api/users/${AUTHOR}/overview" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('type:', d.get('type'))
    print('isEnterprise:', d.get('isEnterprise'))
except Exception:
    print('(org endpoint did not return user data -- may be an org, check https://huggingface.co/${AUTHOR} by hand)')
" 2>/dev/null || echo "(could not fetch author overview)"
fi

echo
echo "=== README (first 40 lines) ==="
curl -s "https://huggingface.co/${REPO}/raw/main/README.md" | head -40

echo
echo "=== Checklist (judge these yourself) ==="
cat <<'EOF'
[ ] Does the org/author match who you'd expect for this model family?
    (the real "Qwen" org, not a lookalike username)
[ ] Is the download count proportional to likes and to how long it's
    existed? Farmed repos often have huge downloads with near-zero
    genuine community engagement, or vice versa.
[ ] Does the README tell a coherent story -- does it reference real
    prior versions, actual benchmarks, an arxiv paper, consistent
    naming? Or is it generic/templated with no real content?
[ ] Does the model/version name match a plausible naming pattern for
    that org, or does it graft a DIFFERENT org's branding onto an
    unrelated base (e.g. "Claude"/"GPT" branding on a community merge
    with no connection to that company)?
[ ] If the version number is unfamiliar to you: don't assume it's fake
    just because you haven't heard of it -- verify the org identity and
    README coherence first. A genuinely new release from a fast-moving
    org can legitimately be newer than your own knowledge cutoff.
EOF

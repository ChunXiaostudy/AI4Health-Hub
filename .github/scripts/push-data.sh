#!/usr/bin/env bash
# Commit data/ and push to the branch the workflow runs on (main), surviving commits that landed there while this run was working.
# Rebases onto origin/<branch>; on conflicting hunks this run's data wins (`-X theirs`: during a rebase "theirs" is the
# commit being replayed, i.e. ours). If the rebase still stops, this run's data/ is laid over origin/<branch>. A JSON file left unparsable by the line merge is replaced by this run's copy.
set -u
msg="$1"; branch="${GITHUB_REF_NAME:-main}"
git config user.name "github-actions[bot]"; git config user.email "41898299+github-actions[bot]@users.noreply.github.com"
git add data
git diff --cached --quiet && { echo "no data changes"; exit 0; }
git commit -q -m "$msg"
mine=$(git rev-parse HEAD)
for i in 1 2 3 4 5; do
  git push origin "HEAD:$branch" && exit 0
  sleep $((i * 5))
  git fetch -q origin "$branch"
  if ! git rebase -X theirs "origin/$branch"; then
    # a conflict -X theirs cannot settle (e.g. file deleted upstream): put this run's data/ on top of origin/<branch> as is
    git rebase --abort; git reset -q --hard "origin/$branch"; git checkout "$mine" -- data
    git commit -q -m "$msg" || exit 0; continue
  fi
  bad=$(git diff --name-only "$mine" HEAD -- 'data/*.json' 'data/**/*.json' | while read -r f; do
          [ -f "$f" ] && ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$f" 2>/dev/null && echo "$f"; done)
  if [ -n "$bad" ]; then
    echo "invalid JSON after rebase, keeping this run's copy: $bad"
    git checkout "$mine" -- $bad && git commit -q --amend --no-edit
  fi
done
echo "push failed after 5 attempts"; exit 1

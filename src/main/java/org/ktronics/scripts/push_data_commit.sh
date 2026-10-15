#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 (--resolve-conflicts|--fail-on-conflict)" >&2
  exit 2
}

mode=
branch="${GITHUB_REF_NAME:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --resolve-conflicts|--fail-on-conflict)
      [ -z "$mode" ] || usage
      mode="${1#--}"
      ;;
    *)
      usage
      ;;
  esac
  shift
done

[ -n "$mode" ] || usage
[ -n "$branch" ] || {
  echo "::error::No target branch was supplied and GITHUB_REF_NAME is empty." >&2
  exit 1
}

max_attempts=5
stash_before=$(git rev-parse -q --verify refs/stash 2>/dev/null || true)
stash_created=false

restore_stash() {
  status=$?
  if [ "$stash_created" = true ]; then
    if ! git stash pop --index; then
      # A warning, never a failure. Everything the stash holds is scratch that
      # dies with the runner in a few minutes. Failing here after a successful
      # push turns a healthy run red and sends the "production CSVs are NOT
      # being updated" email, which is simply untrue.
      echo "::warning::Could not restore temporary working-directory changes;"\
           "they are scratch and the runner is about to be destroyed." >&2
    fi
  fi
  exit "$status"
}
trap restore_stash EXIT

if [ -n "$(git status --porcelain)" ]; then
  git stash push --include-untracked -m "workflow temporary files"
  stash_after=$(git rev-parse -q --verify refs/stash 2>/dev/null || true)
  if [ "$stash_after" != "$stash_before" ]; then
    stash_created=true
  fi
fi

resolve_regenerated_conflicts() {
  conflicts=$(git diff --name-only --diff-filter=U)
  [ -n "$conflicts" ] || return 1

  while IFS= read -r path; do
    [ -n "$path" ] || continue

    if [[ "$path" == state/*.json || "$path" == */state/*.json ]]; then
      # MERGE a cumulative map; never choose a side.
      #
      # A state JSON is one key -> record map and two runs each add and update
      # different keys, so whole-file --ours/--theirs throws away everything
      # the losing side recorded - already-emailed alarms come back as new and
      # are mailed again, for ever. Union the keys instead and take the higher
      # send_count per key.
      merged=$(mktemp)
      upstream=$(mktemp)
      mine=$(mktemp)
      # In a rebase, stage 2 is the branch being replayed onto (upstream) and
      # stage 3 is our replayed commit. A missing stage means that side added
      # or deleted the file; an empty map is the right stand-in either way.
      git show ":2:$path" > "$upstream" 2>/dev/null || : > "$upstream"
      git show ":3:$path" > "$mine" 2>/dev/null || : > "$mine"

      if python3 "$(dirname "$0")/merge_alarm_state.py" "$upstream" "$mine" > "$merged"; then
        mv "$merged" "$path"
      else
        echo "::error::Could not merge $path; refusing to resolve it by" \
             "discarding one side." >&2
        rm -f "$merged" "$upstream" "$mine"
        return 1
      fi
      rm -f "$upstream" "$mine"

    elif [[ "$path" == data/* || "$path" == alerts/* \
            || "$path" == */data/* || "$path" == */alerts/* ]]; then
      # Regenerated wholesale by the next scheduled run, so either side is
      # equally disposable and ours is the fresher of the two.
      if ! git checkout --theirs -- "$path"; then
        echo "::error::Could not take our copy of $path." >&2
        return 1
      fi

    else
      # Anything else conflicting means a human-authored file reached a bot
      # commit. Resolving that silently in the bot's favour is how source
      # changes get reverted by a data run.
      echo "::error::Unexpected conflict in $path - this script only resolves" \
           "state/, data/ and alerts/." >&2
      return 1
    fi

    git add -- "$path" || return 1
  done <<< "$conflicts"
}

for attempt in $(seq 1 "$max_attempts"); do
  echo "Push attempt $attempt/$max_attempts to $branch"

  if ! git fetch origin "$branch"; then
    echo "Could not fetch the latest $branch; retrying."
    [ "$attempt" -lt "$max_attempts" ] && sleep $((2 ** attempt))
    continue
  fi

  if ! git rebase "origin/$branch"; then
    if [ "$mode" = "fail-on-conflict" ]; then
      echo "::error::Conflict in append-only BMS data; refusing to drop a reading." >&2
      git rebase --abort || true
      exit 1
    fi

    if ! resolve_regenerated_conflicts; then
      echo "Could not resolve regenerated-file conflicts; retrying."
      git rebase --abort || true
      [ "$attempt" -lt "$max_attempts" ] && sleep $((2 ** attempt))
      continue
    fi

    if ! GIT_EDITOR=: git rebase --continue; then
      echo "Conflict resolution could not be applied; retrying."
      git rebase --abort || true
      [ "$attempt" -lt "$max_attempts" ] && sleep $((2 ** attempt))
      continue
    fi
  fi

  if git push origin "HEAD:$branch"; then
    echo "Published data commit to $branch."
    exit 0
  fi

  echo "Push was refused or lost a race; retrying."
  [ "$attempt" -lt "$max_attempts" ] && sleep $((2 ** attempt))
done

echo "::error::Could not publish the data commit after $max_attempts attempts." >&2
exit 1

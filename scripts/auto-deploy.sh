#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=${REPO_DIR:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}
DEPLOY_BRANCH=${DEPLOY_BRANCH:-main}
ALLOW_BRANCH_SWITCH=${ALLOW_BRANCH_SWITCH:-0}

cd "$REPO_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Not a git repository: $REPO_DIR" >&2
    exit 1
fi

if ! git remote get-url origin >/dev/null 2>&1; then
    echo "Git remote 'origin' is not configured; skipping auto-deploy."
    exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Local changes detected; refusing to auto-merge."
    git status --short
    exit 1
fi

git fetch --prune origin "$DEPLOY_BRANCH"

REMOTE_REF="origin/$DEPLOY_BRANCH"
LOCAL_REV=$(git rev-parse HEAD)
REMOTE_REV=$(git rev-parse "$REMOTE_REF")

if [ "$LOCAL_REV" = "$REMOTE_REV" ]; then
    echo "Already up to date: $DEPLOY_BRANCH"
    exit 0
fi

CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "$DEPLOY_BRANCH" ]; then
    if [ "$ALLOW_BRANCH_SWITCH" != "1" ]; then
        echo "Current branch is '$CURRENT_BRANCH', deploy branch is '$DEPLOY_BRANCH'; skipping auto-deploy."
        exit 0
    fi

    if git show-ref --verify --quiet "refs/heads/$DEPLOY_BRANCH"; then
        git checkout "$DEPLOY_BRANCH"
    else
        git checkout -b "$DEPLOY_BRANCH" --track "$REMOTE_REF"
    fi
fi

git merge --ff-only "$REMOTE_REF"
"$SCRIPT_DIR/docker-apply.sh"

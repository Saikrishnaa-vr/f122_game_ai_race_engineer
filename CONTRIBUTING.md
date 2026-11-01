# Branches and releases

`main` is the accepted product baseline. Its first commit contains the current local code and the two published replay fixtures. After that initial import, changes reach `main` only through a pull request from this repository's `develop` branch. The product owner reviews, verifies, and merges that release pull request.

`develop` is the active integration branch. Create each `feature/*` or `fix/*` branch from the latest `develop`, work and test there, and open a pull request back to `develop`. Merge only after reviewing a passing `validate` run for the current change. Run the game with `develop` for live feature validation when the change needs it. A passing CI run validates automated behavior and saved replay; it does not prove live UDP capture, visual fit, or game timing.

The release path is:

```text
develop → feature/* or fix/* → pull request to develop → live validation → pull request from develop to main → owner merge
```

Before a release PR, update the relevant project progress and handoff notes with what was verified and what remains to test. Do not commit runtime `recordings/`, `data/`, `logs/`, secrets, build output, or caches. The two compressed archives in `fixtures/` are the only published race captures.

## GitHub protection setup

After the initial `main` and `develop` branches exist, configure GitHub repository rulesets or branch protection:

- Protect both branches: require a pull request, require the `validate` status check, block force pushes and deletion, and prevent direct pushes.
- Restrict who can merge or otherwise update `main` to the product owner. Keep the PR and check requirements effective for that owner as well. Do not grant other users repository admin or bypass rights if owner-only control is required.
- Allow feature branches to open PRs to `develop`; require the same CI check before merge.
- Keep `main` as the default branch so it represents the accepted version. Branch new development from `develop`.

Repository settings, not this file or CI alone, enforce merge permissions. Rule availability and owner-only controls depend on the repository visibility, account plan, and whether the repository is personal or organization-owned. Confirm the effective rules in GitHub after setup by checking that a direct push and an unvalidated PR cannot update either protected branch. The CI release-source check also rejects PRs to `main` unless their head is this repository's `develop` branch.

**Current investigation:** The original rulesets were disabled on 2026-10-29 after automatic PR checks failed to appear. Their present GitHub settings must be inspected; do not infer protection from this document. The workflow now also listens for pushes to `feature/*`, `fix/*` and `docs/*` branches so a fresh branch can test whether `push` events create checks. This is a diagnostic fallback, not evidence that the missing `pull_request` trigger is fixed. A manually dispatched run does not satisfy a required PR check. Before restoring required checks, use `scripts\inspect_github_validation.ps1 -PullRequest NUMBER` and verify a fresh `pull_request` or `push` run and its `validate` check on the PR's current code. Follow [the branch-ruleset handoff](BRANCH_RULESET_HANDOFF.md) for the controlled test and recovery sequence.

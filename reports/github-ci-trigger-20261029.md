# GitHub Actions trigger investigation — 2026-10-29

Repository: `Saikrishnaa-vr/f122_game_ai_race_engineer` (public). PR [#1](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/pull/1) targets `develop` from `feature/github-branch-protection`.

## Observed evidence

- GitHub Actions is enabled with `allowed_actions: all`; `.github/workflows/validate.yml` is `active`; repository Actions policies are empty.
- The workflow exists on `main` and on the feature branch. It specifies `pull_request`, `push`, and `workflow_dispatch`. The initial PR and a later reopen produced no automatic run. Feature pushes to `b8bf65f5e19206635623aebaa8b1e0d9b3141612` and `a75985f7aa96cfea013a0bf9e3ab94f0107ded69` also produced no automatic run. The latter removed the feature-push filter and broadened `pull_request` to all base branches, so a branch filter does not explain the missing PR run.
- Manual `workflow_dispatch` succeeded on `main` (run [40884947137](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/actions/runs/40884947137)), `develop` (run [40885149402](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/actions/runs/40885149402)), and the previous feature head (run [40908267828](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/actions/runs/40908267828)). The feature run passed checkout, Python setup, dependency installation, JavaScript syntax, automated tests, fixture restore, and Grand Prix replay verification.
- The repository's Actions run list contains only those three manual runs. The latest PR head has no check run. GitHub's public repository events feed returned no matching events, but [GitHub documents a 30-second to six-hour delay](https://docs.github.com/en/rest/activity/events#list-repository-events), so that feed is inconclusive.
- [GitHub's required-check documentation](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks) says a `workflow_dispatch` job check does not count for a PR. A passing manual run therefore cannot safely unblock this branch rule.

## Next action

At the owner's request, both rulesets (`26126637`, `26126638`) were set to `disabled` on 2026-10-29. GitHub reports `protected: false` for `main` and `develop`, and PR #1 is mergeable. Disabling and re-enabling the workflow, followed by another PR reopen, had not produced an automatic run. Branch conventions and manual validation now provide the only merge discipline; GitHub does not enforce them.

Recheck Actions runs and the public events feed after the documented event-feed delay. If `push` and `pull_request` runs are still absent, report these run IDs, commit SHAs, the active workflow path, and the enabled Actions settings through [GitHub Support or Community](https://docs.github.com/en/support/contacting-github-support). The evidence narrows the failure to event-to-workflow creation; it does not identify a confirmed GitHub backend cause. Once automatic PR validation works, re-enable the rulesets and verify a protected merge to `develop` and a rejected direct push. Only the owner should promote `develop` to `main` through a separate PR.

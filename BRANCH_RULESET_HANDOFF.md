# GitHub branch rules and missing automatic Validation runs

**For:** a contributor investigating repository protection. **Repository:** `Saikrishnaa-vr/f122_game_ai_race_engineer`. **Recorded:** 2026-10-30. This is an open investigation, not a confirmed GitHub service defect.

## Intended behavior

`develop` accepts feature and fix pull requests after the `validate` GitHub Actions job passes. `main` receives only owner-reviewed release PRs from `develop`. Both branches should reject direct updates, force pushes and deletion. The workflow in `.github/workflows/validate.yml` declares `pull_request` for `develop` and `main`, `push` for those two branches, and `workflow_dispatch`. A push to a feature branch alone is **not** expected to trigger the current `push` filter. See [CONTRIBUTING.md](CONTRIBUTING.md).

## What happened

1. The first repository was private. GitHub rejected its rulesets API with HTTP 403, asking for GitHub Pro or public visibility. The owner chose a fresh public repository. [GitHub documents](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) branch rulesets for public repositories on GitHub Free, so a paid plan is not required for this public repository.
2. On 2026-10-29, the owner created ruleset **26126637**, “Only Saikrishnaa-vr may merge to main,” and **26126638**, “Require PR and validation on main and develop.” The setup reported both active. The intended configuration lives in `scripts/configure_github_protection.ps1` on the still-open [PR #1](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/pull/1); that script is **not yet in `develop`**.
3. Automatic `pull_request` Validation runs did not appear for PR #1 after opening or reopening it. The workflow was active, Actions was enabled with `allowed_actions: all`, and manual runs succeeded. Disabling/re-enabling the workflow and closing/reopening PR #1 did not produce an automatic run. The earlier [trigger investigation on PR #1's branch](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/blob/feature/github-branch-protection/reports/github-ci-trigger-20261029.md) records the original checks and run IDs.
4. With the required `validate` check unavailable to PRs, the rulesets blocked the intended merge flow. At the owner's request, both rulesets were set to `disabled` on 2026-10-29. **Their current enforcement must be checked again**; this document does not assume the remote settings stayed unchanged.
5. The pattern persisted through analytics [PR #6](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/pull/6): it targeted `develop` at head `5a2be51`, had no PR-triggered Validation run when checked, and the owner manually dispatched [run 41009074087](https://github.com/Saikrishnaa-vr/f122_game_ai_race_engineer/actions/runs/41009074087). Its `validate` job passed the tests, JavaScript check and saved Bahrain replay. PR #6 subsequently merged. This proves the workflow can execute manually; it does **not** prove the PR trigger or required-check integration works.

[GitHub's required-check guidance](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks) explicitly says a job started by `workflow_dispatch` on a PR head branch may not appear in that PR's checks or satisfy a ruleset's required status check. A successful manual run is useful evidence, but it is not a safe substitute for an automatic PR check when restoring strict protection.

## Confirmed versus unknown

| Confirmed from repository/user output | Still unproven |
| --- | --- |
| Workflow file declares the expected PR trigger and succeeds on manual dispatch. | Why GitHub did not create an automatic PR run. |
| Actions was enabled and the workflow was active when checked. | Current Actions and ruleset settings; they may have changed. |
| Both rulesets were disabled by the owner on 2026-10-29. | Whether any other protection currently applies to `main` or `develop`. |
| A manual `workflow_dispatch` check cannot satisfy a required PR workflow check under GitHub's documented rules. | Whether the `validate` required-check source/context and strict update policy will work once automatic runs exist. |
| The private-repository ruleset API was unavailable on the owner's free plan; the public repository accepted ruleset creation. | Whether the “owner-only main” ruleset actually restricts every non-owner update path as intended. |

Possible areas to test include the identity/token that created or updated PRs, a pending workflow-approval banner, commit-message skip annotations, workflow availability on the default branch, PR merge conflicts, and GitHub event delivery. [GitHub's workflow troubleshooting guide](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows) lists these trigger conditions. None is established as the root cause here. Do not change several settings at once or attribute this to GitHub infrastructure without stronger evidence.

## Contributor investigation plan

The friend can investigate using a fork or `feature/*` branch and a PR to `develop`; repository admin access is not needed to propose code or document a reproducer. The owner should retain control of `main` and apply any settings changes. Record commands and outputs without publishing tokens.

1. **Snapshot current state.** From an authenticated PowerShell session, record the output of:

   ```powershell
   $r = 'Saikrishnaa-vr/f122_game_ai_race_engineer'
   gh api "repos/$r/actions/permissions"
   gh api "repos/$r/actions/workflows/validate.yml"
   gh api "repos/$r/rulesets?includes_parents=false"
   gh api "repos/$r/branches/main/protection"
   gh api "repos/$r/branches/develop/protection"
   gh run list -R $r --workflow validate.yml --event pull_request --limit 20 --json databaseId,event,headSha,status,conclusion,url
   ```

   A 404 for a branch-protection endpoint can mean no classic protection; inspect rulesets separately. Do not paste credentials or `gh auth token` output into an issue or PR.

2. **Reproduce on a harmless PR.** Make a small documentation-only change on a fresh branch from `develop`, open a PR to `develop`, and note its creation time, head SHA, mergeability, and Actions approval banner if present. Push one more harmless commit to test `synchronize`. Compare `gh run list --event pull_request` with the PR's Checks tab. Do not treat a feature-branch `push` run as required evidence: the current workflow only filters `push` for `develop` and `main`.
3. **Isolate the trigger.** Verify the workflow file exists on the default branch; inspect PR creator authentication **type** without revealing its token; check commit messages for `[skip ci]` or equivalent; confirm no merge conflict or pending workflow approval. Try one manually opened PR through the GitHub web UI if the first test used a CLI or automation token. Record the exact variable changed and the resulting event/run.
4. **Validate protection separately.** After an automatic PR run appears, check that `validate` is attached to the PR's latest head or test-merge commit and is accepted by a temporary test ruleset. Then have the owner review the two disabled rulesets, their bypass actors, required-check source/context and branch targets before re-enabling them. Test that a failing or absent check blocks a test PR and that a passing automatic PR check allows its intended merge. Use a throwaway protected branch for push-denial experiments; do not make an unvalidated direct update to `main` or `develop`.
5. **Escalate with evidence if needed.** If the minimal PR still creates no automatic run, send GitHub Support/Community the repository URL, workflow path, PR URL, head SHA, UTC timestamps, Actions settings, and matching manual-run ID. Do not send credentials or private local logs.

## Done when

- A new PR to `develop` automatically creates a `pull_request` Validation run for its current code, and a subsequent push creates a fresh run.
- A passing PR-triggered `validate` check satisfies the required check; an absent/failing one blocks merge.
- Rules for `develop` and `main` are re-enabled only after the owner reviews their exact effect, and a controlled test confirms direct updates and deletion are blocked.
- The owner-only `main` path is verified without granting a friend admin/bypass rights; release PRs still originate from `develop`.
- The final evidence and any remaining GitHub limitation are recorded in this file or a follow-up PR. Until then, the owner must manually review PRs and CI runs; the last known disabled rulesets do not enforce the workflow.

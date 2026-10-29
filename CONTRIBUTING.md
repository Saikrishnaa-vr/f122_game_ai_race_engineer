# Branches and releases

`main` is the accepted product baseline. Its first commit contains the current local code and the two published replay fixtures. After that initial import, changes reach `main` only through a pull request from this repository's `develop` branch. The product owner reviews, verifies, and merges that release pull request.

`develop` is the active integration branch. Create each `feature/*` or `fix/*` branch from the latest `develop`, work and test there, and open a pull request back to `develop`. Run the game with `develop` for live feature validation when the change needs it. A passing CI run validates automated behavior and saved replay; it does not prove live UDP capture, visual fit, or game timing.

**Current GitHub state (2026-10-29):** Both repository rulesets are disabled while GitHub fails to start automatic `push` and `pull_request` workflow runs. `main` and `develop` are unprotected; the PR and owner-only merge process above is a team convention for now. Before merging, run the local checks in the [README](README.md) or manually dispatch Validation on the exact PR head and review its result. A manual dispatch does not satisfy GitHub's required PR check if the rulesets are re-enabled. See the [CI trigger investigation](reports/github-ci-trigger-20261029.md).

The disabled rulesets are retained as IDs `26126637` and `26126638`. When automatic PR checks work, re-enable those existing rulesets and verify their effective branch protection; the bootstrap script intentionally refuses to create duplicates.

The release path is:

```text
develop → feature/* or fix/* → pull request to develop → live validation → pull request from develop to main → owner merge
```

Before a release PR, update the relevant project progress and handoff notes with what was verified and what remains to test. Do not commit runtime `recordings/`, `data/`, `logs/`, secrets, build output, or caches. The two compressed archives in `fixtures/` are the only published race captures.

## GitHub protection setup

When automatic PR validation works again, configure GitHub repository rulesets or branch protection:

- Protect both branches: require a pull request, require the `validate` status check, block force pushes and deletion, and prevent direct pushes.
- Restrict who can merge or otherwise update `main` to the product owner. Keep the PR and check requirements effective for that owner as well. Do not grant other users repository admin or bypass rights if owner-only control is required.
- Allow feature branches to open PRs to `develop`; require the same CI check before merge.
- Keep `main` as the default branch so it represents the accepted version. Branch new development from `develop`.

Repository settings, not this file or CI alone, enforce merge permissions. Rule availability and owner-only controls depend on the repository visibility, account plan, and whether the repository is personal or organization-owned. Confirm the effective rules in GitHub after setup by checking that a direct push and an unvalidated PR cannot update either protected branch. The CI release-source check also rejects PRs to `main` unless their head is this repository's `develop` branch.

For the initial repository, the owner can run `scripts\configure_github_protection.ps1` from PowerShell. It checks ruleset access before starting CI, checks the exact branch heads and `validate` job, displays two planned rulesets, and changes nothing by default. With `-Apply`, it starts and waits for Validation on either branch if the initial push did not produce a run, then creates the rules only after both branches pass. One ruleset requires PRs and the `validate` check for both branches. A separate ruleset restricts `main` updates to the `Saikrishnaa-vr` account through PRs only. It stops if rulesets already exist, so inspect and adapt existing rules rather than stacking conflicting protections. The GitHub CLI must have repository admin access. This setup requires a GitHub plan that supports rulesets for the repository's visibility.

On GitHub Free, branch rulesets and branch protection are available for public repositories, but not private repositories. If the repository stays private on Free, the workflow still runs on PRs and pushes, but GitHub cannot require a PR or a passing check before a branch changes. Keep write access limited to the owner and follow the PR process manually; do not call that arrangement protected. Public visibility enables the planned free rules and exposes both raw race replay fixtures, their participant names and telemetry. The initial private repository contains an old commit email; the one-time `scripts\publish_public_github_repo.ps1 -Apply` keeps it private under an archive name and creates a fresh public repository at the original URL from cleaned commit history before applying the rules.

# Branches and releases

`main` is the accepted product baseline. Its first commit contains the current local code and the two published replay fixtures. After that initial import, changes reach `main` only through a pull request from this repository's `develop` branch. The product owner reviews, verifies, and merges that release pull request.

`develop` is the active integration branch. Create each `feature/*` or `fix/*` branch from the latest `develop`, work and test there, and open a pull request back to `develop`. Merge only after the `validate` GitHub Actions job passes. Run the game with `develop` for live feature validation when the change needs it. A passing CI run validates automated behavior and saved replay; it does not prove live UDP capture, visual fit, or game timing.

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

For the initial repository, the owner can run `scripts\configure_github_protection.ps1` from PowerShell. It checks ruleset access before starting CI, checks the exact branch heads and `validate` job, displays two planned rulesets, and changes nothing by default. With `-Apply`, it starts and waits for Validation on either branch if the initial push did not produce a run, then creates the rules only after both branches pass. One ruleset requires PRs and the `validate` check for both branches. A separate ruleset restricts `main` updates to the `Saikrishnaa-vr` account through PRs only. It stops if rulesets already exist, so inspect and adapt existing rules rather than stacking conflicting protections. The GitHub CLI must have repository admin access. This setup requires a GitHub plan that supports rulesets for the repository's visibility.

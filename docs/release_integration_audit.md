# Release Integration Audit

## Scope

The release integration branch is based on:

- Source branch: `codex/diamond-nv-center-v1`
- Source commit: `aca534c1bbbc287bf10ff1b6ab387046b75f339b`
- Latest stacked pull request: #143
- Target branch: `main`

GitHub dependency inspection found 129 open stacked pull requests in one
continuous chain from PR #1 through PR #143. The source commit is a descendant
of `origin/main` and contains that complete chain.

## Deliberate Exclusions

- PR #87 is a second pull request for the same
  `codex/castep-slab-dipole-correction` head branch already represented in the
  stack. It is not a separate feature input.
- PR #127 is titled `[BLOCKED] Add private 3C-SiC CASTEP Energy acceptance
  harness` and is based on a separate integration branch. It is not part of the
  reviewed production chain and is excluded from this release.
- Local uncommitted or unpushed worktrees are not release inputs.

## Integration Policy

1. Do not merge the 129 stacked pull requests individually.
2. Validate this release branch against `main` as one complete product.
3. Open one release pull request from `codex/release-integration-v1` to `main`.
4. Merge the release pull request only after complete test and protocol
   acceptance.
5. After the release merge is confirmed, close the superseded stacked pull
   requests. Preserve branches until the release commit is available from
   `main`.

## User-Facing Deliverables

- Clean project overview and quick start in `README.md`.
- Complete Chinese deployment and usage manual in
  `docs/USER_GUIDE.zh-CN.md`.
- Existing detailed operator documentation remains available under `docs/`.

## Required Acceptance

- `python -m pytest -q`
- `python -m compileall -q src`
- MCP stdio protocol smoke using `.codex/config.toml.example`
- `git diff --check`
- Release PR must target `main`

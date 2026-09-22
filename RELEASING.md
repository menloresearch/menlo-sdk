# Releasing menlo-sdk

One version number, one tag, one workflow. This page is the whole ritual.

## The number

Versions follow [PEP 440](https://peps.python.org/pep-0440/) with SemVer meaning:
`MAJOR.MINOR.PATCH`. Before 1.0, a MINOR bump may change the public API (the CHANGELOG
says so at the top); PATCH never does.

| Spelling | Meaning | Installed by a plain `pip install menlo-sdk`? |
|---|---|---|
| `0.2.0.dev0` | what `main` carries between releases: "the next release, unfinished" | never published |
| `0.2.0rc1` | release candidate: API frozen, out for testing on real robots | no (pre-release) |
| `0.2.0` | the release | yes |
| `0.2.1` | a fix on top of `0.2.0`, no API change | yes |

Notes on the spelling, because the publish workflow compares it byte for byte with the tag:

- No hyphen and no dot before `rc`: `0.2.0rc1`, not `0.2.0-rc1` or `0.2.0.rc1`. pip would
  normalise those, git tags would not, and the tag check would fail.
- A dot before `dev` and `post`: `0.2.0.dev0`, `0.2.0.post1`.
- Ordering: `0.2.0.dev0 < 0.2.0a1 < 0.2.0b1 < 0.2.0rc1 < 0.2.0 < 0.2.0.post1 < 0.2.1`.
- `a` (alpha) and `b` (beta) are valid and pip treats them like `rc`. Use them only when the
  API is still expected to move during testing; otherwise go straight to `rc`.
- pip and uv skip every pre-release (`a`, `b`, `rc`, `.dev`) unless the user asks:
  `pip install --pre menlo-sdk`, or an explicit pin `menlo-sdk==0.2.0rc1`. That is the whole
  point of an rc: people who did not opt in never see it.
- PyPI never accepts the same version twice. A bad release gets a new PATCH (or, for a
  packaging-only fix, `.post1`); a harmful one is additionally *yanked* on PyPI, which hides
  it from resolvers but keeps existing pins working.

## Where the number lives

`src/menlo/__init__.py` → `__version__` (`menlo.asimov` re-exports it). hatch reads it (`pyproject.toml`,
`[tool.hatch.version]`); nothing else declares it. `menlo --version` prints it.

## The ritual

`main` carries `X.Y.Z.dev0`, the version being worked towards. It is never tagged.

1. **Release PR.** Set `__version__` to the release (`0.2.0rc1` or `0.2.0`). In
   `CHANGELOG.md`, rename `## 0.2.0 — unreleased` to `## 0.2.0rc1 — 2026-10-03` (or the
   final). Merge.
2. **Tag the merge commit** with `v` + the exact version, and push the tag:

   ```bash
   git switch main && git pull --ff-only
   git tag v0.2.0rc1 && git push origin v0.2.0rc1
   ```

3. `publish.yml` refuses a tag that is not on `main`, builds sdist + wheel, refuses if the
   tag and the wheel's version differ, twine-checks, then waits for the `pypi`
   environment's reviewer. Approve; it uploads.
4. **Bump main.** A one-line PR setting `__version__` back to the next `.dev0`
   (`0.2.0.dev0` after an rc, `0.3.0.dev0` after a final) and opening a fresh
   `## 0.3.0 — unreleased` section in the changelog.

An rc that needs changes: fix on `main`, repeat from step 1 with `rc2`. Nothing is
force-pushed and no tag ever moves.

**Hotfix** for a release that `main` has moved past: branch from the tag
(`git switch -c hotfix/0.2.1 v0.2.0`), fix, set `0.2.1`, tag `v0.2.1` on that branch,
publish, then cherry-pick the fix to `main`.

## Why from main, and what stops a wrong release

Releases are tagged on `main`; there are no release branches. That is what most robot
SDKs do (Spot, LeRobot, MAVSDK, Kinova, Intrinsic) and it is enough while one line is
supported. Three things make a wrong release impossible rather than merely discouraged:

- a **tag ruleset** on `v*`: only maintainers may create these tags, nobody may move or
  delete one;
- the **`pypi` environment**: deployments allowed from tags `v*` only, one required
  reviewer;
- the **ancestry check** in `publish.yml`: the tagged commit must be reachable from `main`.

If a day comes when an old line needs a fix after `main` has moved on, cut
`release/v0.2` from the `v0.2.0` tag then, cherry-pick, tag `v0.2.1` there, and add
`release/*` to the ancestry check. Nothing about starting on `main` prevents that.

## What a release must satisfy

- CI green on the merge commit (lint, mypy strict, unit tests on 3.12 and 3.13, the
  vendored-bindings check and the real-edge integration job).
- The vendored protocol bindings match a *released* `asimov-protocol` tag (`make check-vendor`),
  and `menlo.asimov.robots.PROTOCOL_VERSION` matches what the edge speaks.
- A `## <version> — <date>` heading in `CHANGELOG.md`.

## Dry run

`workflow_dispatch` on `publish.yml` runs the build job only (no upload). Locally:

```bash
uv build && uvx twine check dist/*
```

## Authentication

There is no PyPI token. The publish job holds `id-token: write`; GitHub mints a short-lived
OIDC token for the run and PyPI exchanges it for a 15-minute upload token because a
*trusted publisher* rule on the project says: owner `menloresearch`, repo `menlo-sdk`,
workflow `publish.yml`, environment `pypi`. The `pypi` environment's required reviewer is
the human gate.

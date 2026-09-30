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

## Where releases come from

This repository is a mirror. The SDK is developed in `sdk/` of Menlo's internal robot
repository, and every change merged there is copied here, one commit each, by
[Copybara](https://github.com/google/copybara); the commit's `GitOrigin-RevId` line names the
source commit. Nothing is committed here directly: an edit made here is overwritten by the next
mirror run. Releases are cut in the robot repository too, and arrive here as a `v*` tag, a
GitHub Release and a PyPI upload.

## The ritual

`main` carries `X.Y.Z.dev0`, the version being worked towards. It is never tagged.

1. **Release PR** in the robot repository. Run the `prepare-release` workflow (Actions →
   prepare-release → package `sdk`, version `0.2.0rc1`), or
   `gh workflow run prepare-release.yml -R menloresearch/robot -f package=sdk -f version=0.2.0rc1`.
   It sets `__version__`, turns `## 0.2.0 — unreleased` in `CHANGELOG.md` into
   `## 0.2.0rc1 — <date>` (or drafts that section from the `sdk/` commits since the last
   release), and opens the PR. It refuses a version that is not above the current one, already
   tagged or already on PyPI. Rewrite a drafted section for users, then merge. By hand, the same
   edit is `tools/release/bump.py sdk 0.2.0rc1`.
2. **Tag the merge commit** there with `sdk/v` + the exact version, annotated:

   ```bash
   git switch main && git pull --ff-only
   git tag -a sdk/v0.2.0rc1 -m "menlo-sdk 0.2.0rc1" && git push origin sdk/v0.2.0rc1
   ```

3. The `sdk-release` workflow does the rest, stopping at the first thing that is wrong:
   - builds the sdist and wheel from the tagged `sdk/` tree alone, and refuses if the tag,
     the wheel's version and the `CHANGELOG.md` heading disagree, or the version is `.dev`;
   - runs this repository's checks (ruff, mypy `--strict`, unit tests on 3.12 and 3.13) on
     the tagged tree;
   - waits for a reviewer on its `pypi` environment, then waits for that tree to be
     mirrored here and uploads the files it built;
   - checks PyPI now holds exactly those files (SHA-256), tags the mirrored commit
     `v0.2.0rc1` here (same tree, different commit), and publishes the GitHub Release: the
     changelog section, an install line, the commits since the previous tag, the wheel, the
     sdist and `SHA256SUMS`. An `rc`, `a` or `b` is marked as a pre-release.
4. **Bump main.** A one-line PR setting `__version__` back to the next `.dev0`
   (`0.2.0.dev0` after an rc, `0.3.0.dev0` after a final) and opening a fresh
   `## 0.3.0 — unreleased` section in the changelog.

An rc that needs changes: fix on `main`, repeat from step 1 with `rc2`. Nothing is
force-pushed and no tag ever moves. The `v*` tag comes after the upload, so a rejected or failed
upload leaves nothing here. Failed jobs can be re-run: a file PyPI already has is compared rather
than uploaded again, an existing tag must already point at the mirrored commit, a draft Release is
completed and a published one is checked against `SHA256SUMS`.

## What stops a wrong release

- the **ancestry check**: the tagged commit must be a commit of `main`'s first-parent line,
  which is what the mirror copies;
- the **mirror check**: the `v*` tag goes on the commit whose tree is byte-identical to the
  tagged `sdk/` tree, and only after the checks have passed on that tree;
- the **`pypi` environment**: one required reviewer before anything reaches PyPI;
- **tag rules**: tags cannot be moved or deleted, and only the mirror creates `v*` tags here.

The release workflow refuses a fix for a release that `main` has moved past: the ancestry
check stops it. Such a fix needs a `release/v0.2` branch cut from the release commit, the fix
cherry-picked onto it, and the ancestry check and the mirror extended to that branch.

## What a release must satisfy

- The release workflow re-runs lint, mypy strict and the unit tests (3.12 and 3.13) on the tagged
  tree itself. The real-edge integration job runs in this repository's CI when the release
  commit is mirrored; the `pypi` reviewer checks it passed there before approving.
- The `asimov-protocol` floor in `pyproject.toml` names a release that is on PyPI, and
  `menlo.asimov.robots.PROTOCOL_VERSION` matches what the edge speaks.
- A `## <version> — <date>` heading in `CHANGELOG.md`.

## Dry run

```bash
uv build && uvx twine check dist/*
```

CI here builds the wheel on every push, which is the same build the release uploads.

## Authentication

There is no PyPI token. The upload job holds `id-token: write`; GitHub mints a short-lived
OIDC token for the run and PyPI exchanges it for a 15-minute upload token because a
*trusted publisher* rule on the project names the robot repository, workflow
`sdk-release.yml` and environment `pypi`. That environment's required reviewer is the human
gate. The mirror, the `v*` tag and the GitHub Release are written by a GitHub App that is
installed on the mirror repositories only; its key is held by the robot environments that
publish (`mirror`, deployable from main; `pypi`, behind its reviewer), never by the repository.

# Contributing

Thanks for looking. This SDK is small on purpose; changes that keep it small are welcome.

- Read `AGENTS.md` first: it is the rulebook (wire verbs, no guessing, tests that fail
  without the fix).
- `make sync && make check` must pass: ruff, mypy `--strict`, and the unit suite, which
  drives a fake edge over the real wire and needs no robot.
- Every behavioural change ships a test that fails on the previous code. If you change the
  wire, update `tests/conftest.py` and run `make integration` against the real connector.
- Public API changes go in `CHANGELOG.md` under the unreleased version. Cutting a release
  (version numbers, rc, tags, PyPI) is described in `RELEASING.md`.
- Commit messages describe the change and why; the body should let a reader reconstruct
  the reasoning without the pull request.
- The `asimov.io` bindings come from the `asimov-protocol` package on PyPI
  (`asimov-protocol>=1.2.1rc1,<2` in `pyproject.toml`). Move the floor when the SDK starts using
  something a newer protocol MINOR added; the `<2` bound moves only with the wire directory.

This repository is a mirror of the SDK's upstream (see `RELEASING.md`): a change is merged
upstream and then copied here. Open a pull request against `main` as usual; a maintainer
reviews it here and lands it upstream, with you as the author.

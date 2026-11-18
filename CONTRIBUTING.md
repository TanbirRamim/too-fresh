# Contributing

Thanks for wanting to help. too-fresh is small on purpose: standard library only, no runtime dependencies.

## Setup

```sh
git clone https://github.com/<you>/too-fresh && cd too-fresh
python -m venv .venv && . .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

Python 3.11+ (we use `tomllib`). Tests never hit the network; registries are faked with `FakeFetch` in `tests/test_too_fresh.py`.

## Adding a lockfile format

Most issues labeled `good first issue` are new lockfile parsers. The pattern:

1. Add a small, real-looking sample to `tests/fixtures/` (a few packages, include one entry that should be skipped, like a path or git dependency).
2. Write `parse_<format>(path) -> list[Dep]` in `src/too_fresh/lockfiles.py` and register the filename in `PARSERS`. `parse_composer_lock` is a good short example.
3. If the ecosystem is new, add `_<ecosystem>(self, name, version)` to `Registry` in `src/too_fresh/registries.py`. It returns the publish time as a `datetime` or `None` if unknown.
4. Add tests next to `test_composer_lock` (parser) and `test_each_registry` (registry, with a fake response).
5. Add a line to the supported formats table in `README.md` and to `CHANGELOG.md` under Unreleased.

## Before you open a PR

- Comment on the issue so nobody duplicates your work.
- `python -m unittest discover -s tests` passes.
- Keep it stdlib only.

Small PRs get reviewed fast, usually within a day.

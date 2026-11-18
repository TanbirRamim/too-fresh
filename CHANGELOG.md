# Changelog

## Unreleased

- Pipenv: check PyPI pins in the `default` and `develop` groups of `Pipfile.lock`.
- Config file: `.too-fresh.toml` or `[tool.too-fresh]` in `pyproject.toml` (found from the current directory up to the git root) can set `days`, `ignore`, `strict` and `jobs`. Flags override it; `--config FILE` picks one explicitly; unknown keys fail with exit 2.
- PHP and Ruby: `composer.lock` (Packagist) and `Gemfile.lock` / `gems.locked` (rubygems.org). Path repos, `dev-` branches, git and private gem sources are skipped. Platform gems are looked up for their platform.
- `--changed-only [REF]` checks only the versions that differ from a git ref (PR base in GitHub Actions, `HEAD` locally). Lockfiles that are new in the diff count as fully changed. The Action takes `changed-only: true`.

## 0.1.0 (2026-11-17)

- First release: npm, pnpm, yarn (v1 and berry), bun, uv, poetry, requirements.txt, Cargo.lock and go.sum against npm, PyPI, crates.io and the Go proxy. `--days`, `--ignore`, `--json`, `--strict`, GitHub Action.

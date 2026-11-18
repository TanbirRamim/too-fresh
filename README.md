# too-fresh

[![ci](https://github.com/TanbirRamim/too-fresh/actions/workflows/ci.yml/badge.svg)](https://github.com/TanbirRamim/too-fresh/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/too-fresh)](https://pypi.org/project/too-fresh/) [![MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Find the dependencies in your lockfiles that were published too recently to trust.

![too-fresh scanning a repo with an npm and a Python lockfile](demo/demo.gif)

Most of this year's supply chain attacks (axios, @bitwarden/cli, LiteLLM and friends) were caught and pulled within hours or a few days. If you simply don't install anything that new, you dodge almost all of them. That's what "dependency cooldowns" are, and npm, pnpm, yarn, bun, uv, pip and cargo all have a setting for it now, each with its own name and its own time unit.

The setting only protects machines where someone actually set it, on a package manager new enough to support it. The lockfile is what ships. too-fresh reads the lockfile, asks the registry when each exact version was published, and fails if anything is younger than 7 days. It works the same for JS, Python, Rust and Go, so you can drop one line into CI and stop thinking about it.

```sh
uvx too-fresh            # or: pipx run too-fresh
```

```
Scanned 2 lockfiles, 9 packages (0 ignored). Minimum age: 7 days.

  FRESH  openai@3.52.0    pypi    published 14h ago (2026-11-16 22:41 UTC)  api/requirements.txt
  FRESH  fastapi@0.163.1  pypi    published 2d ago (2026-11-15 09:51 UTC)  api/requirements.txt
  FRESH  eslint@10.15.0   npm     published 3d ago (2026-11-13 21:33 UTC)  package-lock.json
  FRESH  vite@8.3.8       npm     published 6d ago (2026-11-11 02:49 UTC)  package-lock.json

4 packages younger than 7 days. Wait it out, or allow it with --ignore NAME if you know why it's there.
```

Exit code is 1 when something is too fresh, 0 when it's all fine, 2 when it found no lockfiles.

## What it reads

| lockfile | registry |
|---|---|
| `package-lock.json`, `npm-shrinkwrap.json` (v1, v2, v3) | npm |
| `pnpm-lock.yaml` (v5 to v9) | npm |
| `yarn.lock` (classic and berry) | npm |
| `bun.lock` | npm |
| `uv.lock`, `poetry.lock`, `Pipfile.lock` (`default` and `develop`), `requirements*.txt` (pinned `==` lines) | PyPI |
| `Cargo.lock` | crates.io |
| `go.sum` | proxy.golang.org |
| `composer.lock` | Packagist (path repos and `dev-` branches skipped) |
| `Gemfile.lock`, `gems.locked` | rubygems.org (only `GEM` sections from rubygems.org; platform gems like `nokogiri 1.16.7-x86_64-linux` looked up per platform) |

It walks the directory you give it (default `.`) and skips `node_modules`, `.venv`, `target` and other usual suspects. Git, path, workspace and private-index dependencies are skipped, since there's no public publish date to check.

## Options

```
too-fresh [paths...] [--days N] [--ignore NAME]... [--json] [--strict] [--changed-only [REF]] [--config FILE]
```

- `--days 3` changes the window. Decimals work, `--days 0.5` is 12 hours.
- `--ignore '@myorg/*' --ignore openai` skips your own packages or a bump you've already looked at. `TOO_FRESH_IGNORE=a,b` does the same from the environment.
- `--json` for scripts.
- `--strict` also fails when a version can't be found on its registry (yanked, unpublished, or a typo). By default those are just listed.
- `--changed-only` only checks versions that differ from a git ref, so a PR is judged on what it adds and not on the 1,600 packages already on main. With no ref it compares against the PR base in GitHub Actions (`origin/$GITHUB_BASE_REF`) and `HEAD` locally. A lockfile that's new in the diff counts as fully changed. It's also much faster on big lockfiles.
- `TOO_FRESH_NPM_REGISTRY` points npm lookups at a mirror.

### Config file

So the whole team (and CI) uses the same settings, put them in `.too-fresh.toml` at the repo root, or under `[tool.too-fresh]` in `pyproject.toml`:

```toml
days = 3
ignore = ["@myorg/*", "openai"]  # added to any --ignore flags
strict = true
```

too-fresh looks in the current directory and its parents up to the git root. Flags win over the file, `--config FILE` picks a file explicitly, and an unknown key is an error rather than silently ignored.

## In CI

GitHub Actions:

```yaml
- uses: actions/checkout@v4
- uses: TanbirRamim/too-fresh@v0.1.0
  with:
    days: 7
    ignore: "@myorg/*"
```

Anywhere else with Python 3.11+:

```sh
pipx run too-fresh --days 7
```

On pull requests you probably only care about what the PR changes:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: TanbirRamim/too-fresh@main
  with:
    changed-only: true
```

A nice pattern is running it only on PRs that touch a lockfile, so Renovate or Dependabot bumps sit for a week before they can merge, and an urgent security fix gets an explicit `--ignore` in the PR where everyone can see it.

## What it doesn't do

It doesn't look for CVEs (use [osv-scanner](https://github.com/google/osv-scanner) for that) and it doesn't edit your package manager config (try [depsguard](https://github.com/arnica/depsguard) or [cooldowns.dev](https://cooldowns.dev)). It also won't save you from a malicious package that sits unnoticed for months. It's one cheap layer, and it's the one that catches the fast, loud attacks.

## How it works

Plain Python standard library, no dependencies, so there's not much to audit. It parses each lockfile, dedupes, then makes one request per package (npm, Packagist) or per version (PyPI, crates.io, Go proxy, RubyGems), 32 at a time. A 1,700 package pnpm monorepo takes about 40 seconds. Nothing is sent anywhere except those public registry lookups.

## Development

```sh
python3 -m unittest discover -s tests
```

MIT licensed. Issues and PRs welcome, especially lockfile formats I missed. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [good first issues](https://github.com/TanbirRamim/too-fresh/labels/good%20first%20issue).

"""Lockfile parsers. Each returns a list of Dep for packages that come from a public registry."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Dep:
    ecosystem: str  # npm, pypi, crates, go, packagist, rubygems
    name: str
    version: str
    source: str  # lockfile path it came from


SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "target", "dist", "build", "__pycache__", ".tox", "vendor"}


def _split_at_version(spec: str) -> tuple[str, str]:
    """'@scope/pkg@1.2.3' -> ('@scope/pkg', '1.2.3')."""
    i = spec.rfind("@")
    if i <= 0:
        return spec, ""
    return spec[:i], spec[i + 1 :]


def _split_spec(spec: str) -> tuple[str, str]:
    """'@scope/pkg@npm:other@^1' -> ('@scope/pkg', 'npm:other@^1'). Splits at the first @ after the name."""
    i = spec.find("@", 1)
    if i < 0:
        return spec, ""
    return spec[:i], spec[i + 1 :]


def _is_semverish(v: str) -> bool:
    return bool(re.match(r"^\d+\.\d+\.\d+[\w.+-]*$", v))


def parse_package_lock(path: Path) -> list[Dep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Dep] = []
    pkgs = data.get("packages")
    if isinstance(pkgs, dict):
        for key, meta in pkgs.items():
            if not key or "node_modules/" not in key or meta.get("link"):
                continue
            resolved = meta.get("resolved", "")
            if resolved and not resolved.startswith("http"):
                continue
            name = meta.get("name") or key.rsplit("node_modules/", 1)[1]
            version = meta.get("version", "")
            if _is_semverish(version):
                out.append(Dep("npm", name, version, str(path)))
        return out

    def walk(deps: dict) -> None:  # lockfileVersion 1
        for name, meta in deps.items():
            version = meta.get("version", "")
            if _is_semverish(version):
                out.append(Dep("npm", name, version, str(path)))
            walk(meta.get("dependencies", {}))

    walk(data.get("dependencies", {}))
    return out


def parse_pnpm_lock(path: Path) -> list[Dep]:
    out: list[Dep] = []
    in_packages = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace():
            in_packages = line.rstrip() == "packages:"
            continue
        if not in_packages:
            continue
        m = re.match(r"^  (['\"]?)(/?)(.+?)\1:\s*$", line)
        if not m:
            continue
        key = re.sub(r"\(.*$", "", m.group(3))  # drop peer suffix like (react@18.2.0)
        if m.group(2) and "@" not in key.lstrip("@") and key.count("/") >= 1:
            # lockfile v5: /name/1.2.3 or /@scope/name/1.2.3
            name, _, version = key.rpartition("/")
        else:
            name, version = _split_at_version(key)
        if name and _is_semverish(version):
            out.append(Dep("npm", name, version, str(path)))
    return out


def parse_yarn_lock(path: Path) -> list[Dep]:
    out: list[Dep] = []
    header: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(":") and not line.startswith("#"):
            header = line.rstrip()[:-1]
            continue
        if header is None:
            continue
        s = line.strip()
        m = re.match(r'^resolution:\s*"?(.+?)"?$', s)
        if m:  # yarn berry
            name, rest = _split_at_version(m.group(1))
            if rest.startswith("npm:") and _is_semverish(rest[4:]):
                out.append(Dep("npm", name, rest[4:], str(path)))
            header = None
            continue
        m = re.match(r'^version\s+"?([^"]+)"?$', s)  # yarn v1
        if m:
            first = header.split(",")[0].strip().strip('"')
            name, spec = _split_spec(first)
            if spec.startswith("npm:"):  # alias: "foo@npm:bar@^1"
                name, _ = _split_at_version(spec[4:])
            if not any(spec.startswith(p) for p in ("file:", "link:", "git", "http", "github:")) and _is_semverish(m.group(1)):
                out.append(Dep("npm", name, m.group(1), str(path)))
            header = None
    return out


def parse_bun_lock(path: Path) -> list[Dep]:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r",(\s*[}\]])", r"\1", text)  # bun.lock is JSON with trailing commas
    data = json.loads(text)
    out: list[Dep] = []
    for entry in (data.get("packages") or {}).values():
        if not isinstance(entry, list) or not entry:
            continue
        name, version = _split_at_version(entry[0])
        if _is_semverish(version):
            out.append(Dep("npm", name, version, str(path)))
    return out


def _toml_packages(path: Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])


def parse_uv_lock(path: Path) -> list[Dep]:
    out = []
    for p in _toml_packages(path):
        reg = (p.get("source") or {}).get("registry", "")
        if "pypi.org" in reg and "version" in p:
            out.append(Dep("pypi", p["name"], p["version"], str(path)))
    return out


def parse_poetry_lock(path: Path) -> list[Dep]:
    out = []
    for p in _toml_packages(path):
        if p.get("source") or "version" not in p:
            continue  # git, path, url or private index: nothing to check on PyPI
        out.append(Dep("pypi", p["name"], p["version"], str(path)))
    return out


def parse_pipfile_lock(path: Path) -> list[Dep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("_meta", {}).get("sources", [])
    default_index = sources[0]["name"] if sources else "pypi"
    # A source URL may be missing or use ${ENV_VAR} placeholders; treat those as private indexes.
    pypi_indexes = {
        source.get("name") for source in sources
        if urlsplit(source.get("url") or "").hostname in {"pypi.org", "pypi.python.org"}
    } if sources else {"pypi"}
    out = []
    for group in ("default", "develop"):
        for name, package in data.get(group, {}).items():
            if package.get("index", default_index) not in pypi_indexes:
                continue
            if any(key in package for key in ("path", "file", "git", "hg", "svn", "bzr")):
                continue
            version = package.get("version", "")
            if version.startswith("==") and len(version) > 2:
                out.append(Dep("pypi", name, version[2:], str(path)))
    return out


def parse_requirements(path: Path) -> list[Dep]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().rstrip("\\").strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*===?\s*([^\s;,]+)", line)
        if m:
            out.append(Dep("pypi", m.group(1), m.group(3), str(path)))
    return out


def parse_cargo_lock(path: Path) -> list[Dep]:
    out = []
    for p in _toml_packages(path):
        if "crates.io-index" in p.get("source", ""):
            out.append(Dep("crates", p["name"], p["version"], str(path)))
    return out


def parse_go_sum(path: Path) -> list[Dep]:
    seen = set()
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1].endswith("/go.mod"):
            continue
        key = (parts[0], parts[1])
        if key not in seen:
            seen.add(key)
            out.append(Dep("go", parts[0], parts[1], str(path)))
    return out


def parse_composer_lock(path: Path) -> list[Dep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for p in (data.get("packages") or []) + (data.get("packages-dev") or []):
        name, version = p.get("name", ""), p.get("version", "")
        if not name or "/" not in name or version.startswith("dev-") or version.endswith("-dev"):
            continue
        if (p.get("dist") or {}).get("type") == "path":
            continue
        out.append(Dep("packagist", name, version, str(path)))
    return out


def parse_gemfile_lock(path: Path) -> list[Dep]:
    out = []
    section = remote = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace():
            section, remote = line.strip(), ""
            continue
        if section != "GEM":
            continue
        m = re.match(r"^  remote: (\S+)", line)
        if m:
            remote = m.group(1)
            continue
        m = re.match(r"^    ([A-Za-z0-9_.-]+) \(([^)\s]+)\)$", line)  # 4 spaces: a gem; 6 spaces: its deps
        if m and "rubygems.org" in remote:
            version = m.group(2)
            # platform gems look like 1.15.4-x86_64-linux; gem versions never contain "-"
            out.append(Dep("rubygems", m.group(1), version, str(path)))
    return out


PARSERS = {
    "package-lock.json": parse_package_lock,
    "npm-shrinkwrap.json": parse_package_lock,
    "pnpm-lock.yaml": parse_pnpm_lock,
    "yarn.lock": parse_yarn_lock,
    "bun.lock": parse_bun_lock,
    "uv.lock": parse_uv_lock,
    "poetry.lock": parse_poetry_lock,
    "Pipfile.lock": parse_pipfile_lock,
    "Cargo.lock": parse_cargo_lock,
    "go.sum": parse_go_sum,
    "composer.lock": parse_composer_lock,
    "Gemfile.lock": parse_gemfile_lock,
    "gems.locked": parse_gemfile_lock,
}


def parser_for(path: Path):
    if path.name in PARSERS:
        return PARSERS[path.name]
    if re.match(r"^requirements.*\.txt$", path.name):
        return parse_requirements
    return None


def find_lockfiles(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if parser_for(root) else []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if parser_for(p):
                found.append(p)
    return found


def collect(paths: list[Path]) -> tuple[list[Dep], list[str]]:
    deps: list[Dep] = []
    errors: list[str] = []
    for p in paths:
        try:
            deps.extend(parser_for(p)(p))
        except Exception as e:  # a broken lockfile shouldn't hide the others
            errors.append(f"{p}: could not parse ({e})")
    return deps, errors


class GitError(Exception):
    pass


def base_versions(lockfiles: list[Path], ref: str) -> set[tuple[str, str, str]]:
    """(ecosystem, name, version) pinned by the same lockfiles at git ref `ref`.

    A lockfile that didn't exist at `ref` contributes nothing, so everything in it counts as changed.
    """
    seen: set[tuple[str, str, str]] = set()
    checked_dirs: set[Path] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(lockfiles):
            cwd = f.resolve().parent
            if cwd not in checked_dirs:
                ok = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                                    cwd=cwd, capture_output=True, check=False)
                if ok.returncode != 0:
                    raise GitError(f"can't resolve git ref '{ref}' from {cwd} (is it fetched?)")
                checked_dirs.add(cwd)
            old = subprocess.run(["git", "show", f"{ref}:./{f.name}"], cwd=cwd, capture_output=True, check=False)
            if old.returncode != 0:
                continue
            copy = Path(tmp, str(i), f.name)
            copy.parent.mkdir()
            copy.write_bytes(old.stdout)
            try:
                seen.update((d.ecosystem, d.name, d.version) for d in parser_for(copy)(copy))
            except Exception:
                pass  # unparseable old version: treat the whole file as changed
    return seen

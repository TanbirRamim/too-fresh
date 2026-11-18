import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from too_fresh import lockfiles as lf
from too_fresh.cli import main
from too_fresh.registries import Registry, parse_time

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def names(deps):
    return sorted((d.ecosystem, d.name, d.version) for d in deps)


class ParserTests(unittest.TestCase):
    def test_package_lock(self):
        self.assertEqual(names(lf.parse_package_lock(FX / "package-lock.json")), [
            ("npm", "@scope/pkg", "2.0.0"), ("npm", "axios", "1.7.9"), ("npm", "form-data", "4.0.1")])

    def test_package_lock_v1(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "package-lock.json"
            p.write_text(json.dumps({"lockfileVersion": 1, "dependencies": {
                "a": {"version": "1.0.0", "dependencies": {"b": {"version": "2.0.0"}}},
                "c": {"version": "github:x/y#abc"}}}))
            self.assertEqual(names(lf.parse_package_lock(p)), [("npm", "a", "1.0.0"), ("npm", "b", "2.0.0")])

    def test_pnpm_v9(self):
        self.assertEqual(names(lf.parse_pnpm_lock(FX / "pnpm-lock.yaml")), [
            ("npm", "@babel/core", "7.24.0"), ("npm", "react", "18.2.0"), ("npm", "react-dom", "18.2.0")])

    def test_pnpm_v5_and_v6(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pnpm-lock.yaml"
            p.write_text("lockfileVersion: 5.4\n\npackages:\n\n  /left-pad/1.3.0:\n    dev: false\n"
                         "  /@types/node/20.1.0:\n    dev: true\n  /ms@2.1.3:\n    dev: false\n")
            self.assertEqual(names(lf.parse_pnpm_lock(p)), [
                ("npm", "@types/node", "20.1.0"), ("npm", "left-pad", "1.3.0"), ("npm", "ms", "2.1.3")])

    def test_yarn_v1(self):
        self.assertEqual(names(lf.parse_yarn_lock(FX / "yarn.lock")), [
            ("npm", "@actions/core", "3.0.1"), ("npm", "left-pad", "1.3.0"), ("npm", "lodash", "4.17.21")])

    def test_yarn_berry(self):
        self.assertEqual(names(lf.parse_yarn_lock(FX / "berry" / "yarn.lock")), [("npm", "lodash", "4.17.21")])

    def test_bun(self):
        self.assertEqual(names(lf.parse_bun_lock(FX / "bun.lock")), [
            ("npm", "@types/node", "22.0.0"), ("npm", "zod", "3.23.8")])

    def test_python_locks(self):
        self.assertEqual(names(lf.parse_uv_lock(FX / "uv.lock")), [("pypi", "httpx", "0.28.1")])
        self.assertEqual(names(lf.parse_poetry_lock(FX / "poetry.lock")), [("pypi", "requests", "2.32.3")])
        self.assertEqual(names(lf.parse_requirements(FX / "requirements.txt")), [
            ("pypi", "Django", "5.1.2"), ("pypi", "uvicorn", "0.30.0")])

    def test_pipfile_lock(self):
        path = FX / "Pipfile.lock"
        deps, errors = lf.collect([path])
        self.assertEqual(errors, [])
        self.assertEqual(names(deps), [("pypi", "pytest", "8.3.3"), ("pypi", "requests", "2.32.3")])
        self.assertTrue(all(dep.source == str(path) for dep in deps))

    def test_pipfile_lock_tolerates_env_var_and_missing_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Pipfile.lock"
            path.write_text(json.dumps({
                "_meta": {"sources": [
                    {"name": "pypi", "url": "https://pypi.org/simple"},
                    {"name": "corp", "url": "https://${CORP_INDEX_HOST}/simple"},
                    {"name": "broken"},
                ]},
                "default": {
                    "attrs": {"version": "==24.2.0"},
                    "secret": {"index": "corp", "version": "==1.0.0"},
                    "other": {"index": "broken", "version": "==2.0.0"},
                },
            }), encoding="utf-8")
            self.assertEqual(names(lf.parse_pipfile_lock(path)), [("pypi", "attrs", "24.2.0")])

    def test_cargo_and_go(self):
        self.assertEqual(names(lf.parse_cargo_lock(FX / "Cargo.lock")), [("crates", "serde", "1.0.210")])
        self.assertEqual(names(lf.parse_go_sum(FX / "go.sum")), [("go", "github.com/BurntSushi/toml", "v1.4.0")])

    def test_composer_lock(self):
        self.assertEqual(names(lf.parse_composer_lock(FX / "composer.lock")), [
            ("packagist", "monolog/monolog", "3.7.0"), ("packagist", "phpunit/phpunit", "v11.4.3")])

    def test_gemfile_lock_only_rubygems_org(self):
        self.assertEqual(names(lf.parse_gemfile_lock(FX / "Gemfile.lock")), [
            ("rubygems", "nokogiri", "1.16.7-x86_64-linux"), ("rubygems", "racc", "1.8.1"), ("rubygems", "rack", "3.1.8")])
        self.assertIs(lf.parser_for(Path("gems.locked")), lf.parse_gemfile_lock)

    def test_find_lockfiles_skips_node_modules(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "node_modules" / "x").mkdir(parents=True)
            (Path(d) / "node_modules" / "x" / "package-lock.json").write_text("{}")
            (Path(d) / "app").mkdir()
            (Path(d) / "app" / "uv.lock").write_text("")
            (Path(d) / "requirements-dev.txt").write_text("")
            found = sorted(p.name for p in lf.find_lockfiles(Path(d)))
            self.assertEqual(found, ["requirements-dev.txt", "uv.lock"])

    def test_broken_lockfile_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "package-lock.json"
            p.write_text("{nope")
            deps, errors = lf.collect([p, FX / "uv.lock"])
            self.assertEqual(len(deps), 1)
            self.assertEqual(len(errors), 1)


class FakeFetch:
    """Serves canned registry responses keyed by URL."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append(url)
        if url not in self.routes:
            return None
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r


class RegistryTests(unittest.TestCase):
    def test_each_registry(self):
        fetch = FakeFetch({
            "https://registry.npmjs.org/@scope%2Fpkg": {"time": {"2.0.0": "2026-11-17T01:00:00.000Z"}},
            "https://pypi.org/pypi/httpx/0.28.1/json": {"urls": [
                {"upload_time_iso_8601": "2024-12-06T15:37:21.509072Z"},
                {"upload_time_iso_8601": "2024-12-06T15:30:00.000000Z"}]},
            "https://crates.io/api/v1/crates/serde/1.0.210": {"version": {"created_at": "2024-09-06T20:00:00.123456+00:00"}},
            "https://proxy.golang.org/github.com/!burnt!sushi/toml/@v/v1.4.0.info": {"Time": "2024-06-07T13:24:40Z"},
        })
        r = Registry(fetch)
        self.assertEqual(r.published("npm", "@scope/pkg", "2.0.0"), datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        self.assertEqual(r.published("pypi", "httpx", "0.28.1"), datetime(2024, 12, 6, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(r.published("crates", "serde", "1.0.210").year, 2024)
        self.assertEqual(r.published("go", "github.com/BurntSushi/toml", "v1.4.0").month, 6)
        self.assertIsNone(r.published("npm", "missing", "1.0.0"))

    def test_packagist_minified_metadata(self):
        fetch = FakeFetch({"https://repo.packagist.org/p2/phpunit/phpunit.json": {"minified": "composer/2.0", "packages": {"phpunit/phpunit": [
            {"name": "phpunit/phpunit", "version": "11.5.0", "time": "2026-11-15T08:00:00+00:00", "license": ["BSD-3-Clause"]},
            {"version": "11.4.3", "time": "2024-10-28T13:07:50+00:00"},
        ]}}})
        r = Registry(fetch)
        self.assertEqual(r.published("packagist", "phpunit/phpunit", "v11.4.3"), datetime(2024, 10, 28, 13, 7, 50, tzinfo=timezone.utc))
        self.assertEqual(r.published("packagist", "phpunit/phpunit", "11.5.0").day, 15)
        self.assertIsNone(r.published("packagist", "phpunit/phpunit", "9.9.9"))
        self.assertEqual(len(fetch.calls), 1)

    def test_rubygems_platform_gem(self):
        fetch = FakeFetch({
            "https://rubygems.org/api/v2/rubygems/nokogiri/versions/1.16.7.json?platform=x86_64-linux": {"version_created_at": "2024-07-27T16:44:00.000Z"},
            "https://rubygems.org/api/v2/rubygems/rack/versions/3.1.8.json": {"version_created_at": "2026-11-16T09:00:00.000Z"},
        })
        r = Registry(fetch)
        self.assertEqual(r.published("rubygems", "nokogiri", "1.16.7-x86_64-linux").year, 2024)
        self.assertEqual(r.published("rubygems", "rack", "3.1.8"), datetime(2026, 11, 16, 9, tzinfo=timezone.utc))

    def test_npm_packument_cached_per_name(self):
        fetch = FakeFetch({"https://registry.npmjs.org/a": {"time": {"1.0.0": "2020-01-01T00:00:00Z", "2.0.0": "2021-01-01T00:00:00Z"}}})
        r = Registry(fetch)
        r.published("npm", "a", "1.0.0")
        r.published("npm", "a", "2.0.0")
        self.assertEqual(len(fetch.calls), 1)

    def test_parse_time_nanoseconds(self):
        self.assertEqual(parse_time("2024-06-07T13:24:40.123456789Z").microsecond, 123456)


class CliTests(unittest.TestCase):
    def run_cli(self, argv, routes):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, registry=Registry(FakeFetch(routes)), now=NOW)
        return code, out.getvalue(), err.getvalue()

    ROUTES = {
        "https://pypi.org/pypi/httpx/0.28.1/json": {"urls": [{"upload_time_iso_8601": (NOW - timedelta(hours=14)).isoformat()}]},
        "https://crates.io/api/v1/crates/serde/1.0.210": {"version": {"created_at": "2024-09-06T20:00:00+00:00"}},
    }

    def test_flags_fresh_package_and_exits_1(self):
        code, out, _ = self.run_cli([str(FX / "uv.lock"), str(FX / "Cargo.lock")], self.ROUTES)
        self.assertEqual(code, 1)
        self.assertIn("httpx@0.28.1", out)
        self.assertIn("published 14h ago", out)
        self.assertNotIn("serde@", out)

    def test_ignore_and_days(self):
        code, out, _ = self.run_cli([str(FX / "uv.lock"), "--ignore", "http*"], self.ROUTES)
        self.assertEqual(code, 0)
        self.assertIn("1 ignored", out)
        code, _, _ = self.run_cli([str(FX / "uv.lock"), "--days", "0.5"], self.ROUTES)
        self.assertEqual(code, 0)

    def test_json_output(self):
        code, out, _ = self.run_cli([str(FX / "uv.lock"), "--json"], self.ROUTES)
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(data["fresh"][0]["name"], "httpx")
        self.assertEqual(data["fresh"][0]["age_hours"], 14.0)

    def test_unknown_only_fails_with_strict(self):
        code, out, _ = self.run_cli([str(FX / "Cargo.lock"), str(FX / "go.sum")], self.ROUTES)
        self.assertEqual(code, 0)
        self.assertIn("not found on registry", out)
        code, _, _ = self.run_cli([str(FX / "Cargo.lock"), str(FX / "go.sum"), "--strict"], self.ROUTES)
        self.assertEqual(code, 1)

    def test_network_error_is_unknown(self):
        routes = {"https://pypi.org/pypi/httpx/0.28.1/json": OSError("timed out")}
        code, out, _ = self.run_cli([str(FX / "uv.lock")], routes)
        self.assertEqual(code, 0)
        self.assertIn("timed out", out)

    def test_no_lockfiles(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_cli([d], {})
        self.assertEqual(code, 2)
        self.assertIn("no lockfiles", err)


class ConfigTests(unittest.TestCase):
    def run_in(self, d, argv, routes=CliTests.ROUTES):
        old = os.getcwd()
        os.chdir(d)
        try:
            return CliTests.run_cli(self, argv, routes)
        finally:
            os.chdir(old)

    def test_too_fresh_toml_sets_days_and_ignore(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".git").mkdir()
            Path(d, ".too-fresh.toml").write_text('days = 0.5\n')
            code, out, _ = self.run_in(d, [str(FX / "uv.lock")])
            self.assertEqual(code, 0)
            self.assertIn("Minimum age: 0.5 days", out)
            Path(d, ".too-fresh.toml").write_text('ignore = ["http*"]\n')
            code, out, _ = self.run_in(d, [str(FX / "uv.lock")])
            self.assertEqual(code, 0)
            self.assertIn("1 ignored", out)

    def test_cli_flag_beats_config_and_parent_dir_found(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".git").mkdir()
            Path(d, "pyproject.toml").write_text('[tool.too-fresh]\ndays = 0.5\n')
            sub = Path(d, "sub")
            sub.mkdir()
            code, out, _ = self.run_in(sub, [str(FX / "uv.lock")])
            self.assertEqual(code, 0)
            code, _, _ = self.run_in(sub, [str(FX / "uv.lock"), "--days", "7"])
            self.assertEqual(code, 1)

    def test_bad_config_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".git").mkdir()
            Path(d, ".too-fresh.toml").write_text('dayz = 3\n')
            code, _, err = self.run_in(d, [str(FX / "uv.lock")])
            self.assertEqual(code, 2)
            self.assertIn("unknown key 'dayz'", err)
            code, _, err = self.run_in(d, [str(FX / "uv.lock"), "--config", str(Path(d, "nope.toml"))])
            self.assertEqual(code, 2)
            self.assertIn("no such file", err)


if __name__ == "__main__":
    unittest.main()


class ChangedOnlyTests(unittest.TestCase):
    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                       cwd=self.repo, check=True, capture_output=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.git("init", "-q")
        (self.repo / "requirements.txt").write_text("httpx==0.27.0\nrich==13.0.0\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        (self.repo / "requirements.txt").write_text("httpx==0.28.1\nrich==13.0.0\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        fresh = {"urls": [{"upload_time_iso_8601": (NOW - timedelta(hours=3)).isoformat()}]}
        routes = {"https://pypi.org/pypi/httpx/0.28.1/json": fresh, "https://pypi.org/pypi/rich/13.0.0/json": fresh}
        fetch = FakeFetch(routes)
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_BASE_REF"}
        with redirect_stdout(out), redirect_stderr(err), mock.patch.dict(os.environ, env, clear=True):
            code = main(argv, registry=Registry(fetch), now=NOW)
        return code, out.getvalue(), err.getvalue()

    def test_only_changed_versions_are_checked(self):
        code, out, _ = self.run_cli([str(self.repo), "--changed-only", "--json"])
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(data["checked"], 1)
        self.assertEqual([f["name"] for f in data["fresh"]], ["httpx"])

    def test_new_lockfile_counts_as_all_changed(self):
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "requirements.txt").write_text("rich==13.0.0\n")
        code, out, _ = self.run_cli([str(self.repo / "sub"), "--changed-only", "HEAD", "--json"])
        self.assertEqual(json.loads(out)["checked"], 1)

    def test_unchanged_tree_checks_nothing(self):
        self.git("commit", "-qam", "bump")
        code, out, _ = self.run_cli([str(self.repo), "--changed-only"])
        self.assertEqual(code, 0)
        self.assertIn("0 packages", out)

    def test_bad_ref_exits_2(self):
        code, _, err = self.run_cli([str(self.repo), "--changed-only", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("nope", err)

    def test_default_ref_uses_pr_base_in_actions(self):
        with mock.patch.dict(os.environ, {"GITHUB_BASE_REF": "main"}):
            from too_fresh.cli import default_base_ref
            self.assertEqual(default_base_ref(), "origin/main")

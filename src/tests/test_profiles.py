"""Tests for multi-profile connection support.

Run from the repo root:
    .venv/bin/python -m pytest src/tests/test_profiles.py -q
"""

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import profiles as profiles_mod  # noqa: E402
from profiles import (  # noqa: E402
    LEGACY_PROFILE_NAME,
    ProfileConfigError,
    eager_profile_names,
    env_key,
    load_profiles,
    load_profiles_from_env,
    load_profiles_from_file,
)


class EnvKeyTests(unittest.TestCase):
    def test_non_alnum_becomes_underscore(self):
        self.assertEqual(env_key("reporting-prod"), "REPORTING_PROD")
        self.assertEqual(env_key("history.qa"), "HISTORY_QA")
        self.assertEqual(env_key("dev"), "DEV")


class FileProfileTests(unittest.TestCase):
    def _write(self, payload):
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_loads_profiles_and_applies_defaults(self):
        path = self._write({
            "defaults": {"port": 3306, "read_only": True, "user": "reader"},
            "profiles": {
                "dev": {"host": "test.db.example.com", "password": "pw", "database": "appdb"},
                "dev-write": {
                    "host": "test.db.example.com", "password": "pw",
                    "database": "appdb", "read_only": False, "port": 3307,
                },
            },
        })
        got = load_profiles_from_file(path)

        self.assertEqual(sorted(got), ["dev", "dev-write"])
        self.assertEqual(got["dev"].user, "reader")          # from defaults
        self.assertEqual(got["dev"].port, 3306)
        self.assertTrue(got["dev"].read_only)
        self.assertFalse(got["dev-write"].read_only)          # per-profile override
        self.assertEqual(got["dev-write"].port, 3307)

    def test_env_interpolation_keeps_secrets_out_of_the_file(self):
        path = self._write({
            "profiles": {"p": {"host": "h", "user": "u", "password": "${MY_DB_PW}"}}
        })
        with mock.patch.dict(os.environ, {"MY_DB_PW": "s3cret"}, clear=False):
            got = load_profiles_from_file(path)
        self.assertEqual(got["p"].password, "s3cret")

    def test_missing_host_is_rejected(self):
        path = self._write({"profiles": {"p": {"user": "u", "password": "pw"}}})
        with self.assertRaises(ProfileConfigError):
            load_profiles_from_file(path)

    def test_bad_profile_name_is_rejected(self):
        path = self._write({"profiles": {"bad name!": {"host": "h"}}})
        with self.assertRaises(ProfileConfigError):
            load_profiles_from_file(path)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(ProfileConfigError):
            load_profiles_from_file("/nonexistent/profiles.json")


class EnvProfileTests(unittest.TestCase):
    def test_loads_from_per_profile_env_vars(self):
        env = {
            "MARIADB_PROFILE_REPORTING_PROD_HOST": "reporting.db.example.com",
            "MARIADB_PROFILE_REPORTING_PROD_USER": "u",
            "MARIADB_PROFILE_REPORTING_PROD_PASSWORD": "pw",
            "MARIADB_PROFILE_REPORTING_PROD_DATABASE": "appdb",
            "MARIADB_PROFILE_DEV_HOST": "test.db.example.com",
            "MARIADB_PROFILE_DEV_USER": "u",
            "MARIADB_PROFILE_DEV_PASSWORD": "pw",
            "MARIADB_PROFILE_DEV_READ_ONLY": "false",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            got = load_profiles_from_env("reporting-prod, dev")

        self.assertEqual(sorted(got), ["dev", "reporting-prod"])
        self.assertTrue(got["reporting-prod"].read_only)      # default when unset
        self.assertFalse(got["dev"].read_only)
        self.assertEqual(got["reporting-prod"].host, "reporting.db.example.com")

    def test_listed_profile_without_vars_is_rejected(self):
        with self.assertRaises(ProfileConfigError):
            load_profiles_from_env("ghost")


class ResolutionOrderTests(unittest.TestCase):
    def test_file_wins_over_env_and_env_over_legacy(self):
        env = {
            "MARIADB_PROFILES": "dev",
            "MARIADB_PROFILE_DEV_HOST": "h",
            "MARIADB_PROFILE_DEV_USER": "u",
            "MARIADB_PROFILE_DEV_PASSWORD": "pw",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            got = load_profiles()
        self.assertEqual(sorted(got), ["dev"])

    def test_falls_back_to_legacy_single_connection(self):
        cleared = {k: None for k in ("MARIADB_PROFILES_FILE", "MARIADB_PROFILES")}
        with mock.patch.dict(os.environ, {k: v for k, v in cleared.items() if v}, clear=False):
            for key in cleared:
                os.environ.pop(key, None)
            got = load_profiles()
        self.assertEqual(list(got), [LEGACY_PROFILE_NAME])


class EagerProfileTests(unittest.TestCase):
    def test_legacy_single_profile_still_connects_at_startup(self):
        profs = {LEGACY_PROFILE_NAME: profiles_mod.legacy_profile()}
        os.environ.pop("MARIADB_EAGER_PROFILES", None)
        self.assertEqual(eager_profile_names(profs), [LEGACY_PROFILE_NAME])

    def test_multi_profile_defaults_to_lazy(self):
        profs = {"a": mock.Mock(), "b": mock.Mock()}
        os.environ.pop("MARIADB_EAGER_PROFILES", None)
        self.assertEqual(eager_profile_names(profs), [])

    def test_star_connects_everything(self):
        profs = {"a": mock.Mock(), "b": mock.Mock()}
        with mock.patch.dict(os.environ, {"MARIADB_EAGER_PROFILES": "*"}, clear=False):
            self.assertEqual(sorted(eager_profile_names(profs)), ["a", "b"])


class ServerProfileBindingTests(unittest.TestCase):
    """Profile selection and read-only enforcement, without touching a database."""

    def _server(self, profile_env):
        import importlib

        with mock.patch.dict(os.environ, profile_env, clear=False):
            server_mod = importlib.import_module("server")
            importlib.reload(server_mod)
            srv = server_mod.MariaDBServer("test")
        return server_mod, srv

    def setUp(self):
        self.env = {
            "MARIADB_PROFILES": "ro,rw",
            "MARIADB_PROFILE_RO_HOST": "ro.example.com",
            "MARIADB_PROFILE_RO_USER": "u",
            "MARIADB_PROFILE_RO_PASSWORD": "pw",
            "MARIADB_PROFILE_RO_DATABASE": "appdb",
            "MARIADB_PROFILE_RW_HOST": "rw.example.com",
            "MARIADB_PROFILE_RW_USER": "u",
            "MARIADB_PROFILE_RW_PASSWORD": "pw",
            "MARIADB_PROFILE_RW_DATABASE": "appdb",
            "MARIADB_PROFILE_RW_READ_ONLY": "false",
        }

    def test_unknown_profile_error_lists_valid_names(self):
        _, srv = self._server(self.env)
        with self.assertRaises(ValueError) as ctx:
            srv.resolve_profile("nope")
        self.assertIn("ro", str(ctx.exception))
        self.assertIn("rw", str(ctx.exception))

    def test_profile_required_when_several_configured(self):
        _, srv = self._server(self.env)
        with self.assertRaises(ValueError):
            srv.resolve_profile()

    def test_list_profiles_never_leaks_credentials(self):
        _, srv = self._server(self.env)
        rendered = json.dumps(srv.list_profiles())
        self.assertIn("ro.example.com", rendered)
        self.assertNotIn("pw", rendered)
        self.assertNotIn("password", rendered)

    def test_read_only_is_per_profile(self):
        server_mod, srv = self._server(self.env)

        async def scenario():
            # No pool is ever created: the read-only gate runs before acquiring one.
            srv.get_pool = mock.AsyncMock(side_effect=AssertionError("must not connect"))

            with self.assertRaises(PermissionError):
                async with srv._profile_scope("ro"):
                    await srv._execute_query("DELETE FROM t")

            # The writable profile gets past the gate and only then needs a pool.
            with self.assertRaises(AssertionError):
                async with srv._profile_scope("rw"):
                    await srv._execute_query("DELETE FROM t")

        asyncio.run(scenario())

    def test_tools_register_without_any_connection(self):
        _, srv = self._server(self.env)
        srv.register_tools()   # must not raise: pools are lazy now

        async def names():
            return sorted((await srv.mcp.get_tools()).keys())

        tool_names = asyncio.run(names())
        self.assertIn("list_profiles", tool_names)
        self.assertIn("execute_sql", tool_names)

    def test_every_db_tool_exposes_a_profile_argument(self):
        _, srv = self._server(self.env)
        srv.register_tools()

        async def schemas():
            tools = await srv.mcp.get_tools()
            return {name: tool for name, tool in tools.items()}

        tools = asyncio.run(schemas())
        for name, tool in tools.items():
            if name == "list_profiles":
                continue
            params = tool.parameters.get("properties", {})
            self.assertIn("profile", params, f"{name} is missing the profile argument")

class EnvProfileInterpolationTests(unittest.TestCase):
    """Env-declared profiles expand ${VAR} too, so the registration can hold references
    instead of passwords (secrets then live in the shell env)."""

    def test_password_can_be_an_env_reference(self):
        env = {
            "MARIADB_PROFILE_P_HOST": "h",
            "MARIADB_PROFILE_P_USER": "u",
            "MARIADB_PROFILE_P_PASSWORD": "${SOME_PW_VAR}",
            "SOME_PW_VAR": "actual-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            got = load_profiles_from_env("p")
        self.assertEqual(got["p"].password, "actual-secret")

if __name__ == "__main__":
    unittest.main()

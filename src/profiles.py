# profiles.py
"""Multi-connection profile support for the MariaDB MCP server.

The upstream server pins one connection per process (DB_HOST/DB_USER/... read once at
startup), so every endpoint needs its own registered MCP server. With 15 endpoints that
is 15 processes *per Claude session*.

This module lets one process carry many named connection profiles. Tools then take a
`profile` argument and the server keeps a lazily-created pool per profile.

Resolution order (first match wins):

1. ``MARIADB_PROFILES_FILE`` - path to a JSON file (see ``load_profiles_from_file``).
2. ``MARIADB_PROFILES`` - comma separated profile names, each configured through
   ``MARIADB_PROFILE_<NAME>_HOST`` and friends.
3. Legacy single connection built from ``DB_*`` env vars, exposed as profile ``default``.

Option 3 keeps every existing single-endpoint registration working untouched.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional

from config import (
    DB_CHARSET,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_SSL,
    DB_SSL_CA,
    DB_SSL_CERT,
    DB_SSL_KEY,
    DB_SSL_VERIFY_CERT,
    DB_SSL_VERIFY_IDENTITY,
    DB_USER,
    MCP_MAX_POOL_SIZE,
    MCP_READ_ONLY,
    logger,
)

LEGACY_PROFILE_NAME = "default"

_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ProfileConfigError(ValueError):
    """Raised when profile configuration is present but unusable."""


@dataclass(frozen=True)
class DbProfile:
    """One named database endpoint."""

    name: str
    host: str
    port: int
    user: Optional[str]
    password: Optional[str]
    database: Optional[str] = None
    charset: Optional[str] = None
    read_only: bool = True
    max_pool_size: int = 10
    ssl: bool = False
    ssl_ca: Optional[str] = None
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = None
    ssl_verify_cert: bool = True
    ssl_verify_identity: bool = False

    def describe(self) -> Dict[str, Any]:
        """Safe summary for the ``list_profiles`` tool. Never includes credentials."""
        return {
            "profile": self.name,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "read_only": self.read_only,
        }


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    logger.warning(f"Unrecognised boolean {value!r}; falling back to {default}.")
    return default


def _as_int(value: Any, default: int, field: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"Invalid integer for {field}: {value!r}. Falling back to {default}.")
        return default


def _expand_env_refs(value: Any) -> Any:
    """Expand ``${VAR}`` references so a profiles file need not hold secrets itself."""
    if not isinstance(value, str):
        return value

    missing = []

    def _sub(match: "re.Match[str]") -> str:
        var = match.group(1)
        if var not in os.environ:
            missing.append(var)
            return ""
        return os.environ[var]

    expanded = _ENV_REF_RE.sub(_sub, value)
    if missing:
        # Do not log the surrounding value - it may be a connection secret.
        logger.warning(f"Profiles file references undefined env vars: {sorted(set(missing))}")
    return expanded


def _validate_name(name: str) -> str:
    name = str(name).strip()
    if not _NAME_RE.match(name):
        raise ProfileConfigError(
            f"Invalid profile name {name!r}. Use letters, digits, '.', '-' or '_' and start with an alphanumeric."
        )
    return name


def env_key(name: str) -> str:
    """``reporting-prod`` -> ``REPORTING_PROD`` (the env var infix for that profile)."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _profile_from_mapping(name: str, raw: Dict[str, Any], defaults: Dict[str, Any]) -> DbProfile:
    merged: Dict[str, Any] = {**defaults, **raw}
    merged = {key: _expand_env_refs(value) for key, value in merged.items()}

    host = merged.get("host")
    if not host:
        raise ProfileConfigError(f"Profile '{name}' is missing 'host'.")

    return DbProfile(
        name=name,
        host=str(host),
        port=_as_int(merged.get("port"), 3306, f"{name}.port"),
        user=merged.get("user") or None,
        # An empty password is legitimate; only None means "not configured".
        password=merged.get("password") if merged.get("password") is not None else None,
        database=merged.get("database") or None,
        charset=merged.get("charset") or None,
        read_only=_as_bool(merged.get("read_only"), True),
        max_pool_size=_as_int(merged.get("max_pool_size"), MCP_MAX_POOL_SIZE, f"{name}.max_pool_size"),
        ssl=_as_bool(merged.get("ssl"), False),
        ssl_ca=merged.get("ssl_ca") or None,
        ssl_cert=merged.get("ssl_cert") or None,
        ssl_key=merged.get("ssl_key") or None,
        ssl_verify_cert=_as_bool(merged.get("ssl_verify_cert"), True),
        ssl_verify_identity=_as_bool(merged.get("ssl_verify_identity"), False),
    )


def _warn_on_loose_permissions(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        logger.warning(
            f"Profiles file {path} is readable by group/other. Consider `chmod 600 {path}`."
        )


def load_profiles_from_file(path: str) -> Dict[str, DbProfile]:
    """Load profiles from JSON.

    ``{"defaults": {...}, "profiles": {"dev": {"host": ..., "user": ..., ...}}}``

    Any string value may use ``${ENV_VAR}`` so credentials can stay in the environment.
    """
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        raise ProfileConfigError(f"MARIADB_PROFILES_FILE points at a missing file: {expanded}")

    _warn_on_loose_permissions(expanded)

    try:
        with open(expanded, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ProfileConfigError(f"Profiles file {expanded} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProfileConfigError(f"Profiles file {expanded} must contain a JSON object.")

    raw_profiles = payload.get("profiles", payload)
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ProfileConfigError(f"Profiles file {expanded} defines no profiles.")

    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ProfileConfigError(f"'defaults' in {expanded} must be an object.")

    profiles: Dict[str, DbProfile] = {}
    for raw_name, raw in raw_profiles.items():
        name = _validate_name(raw_name)
        if not isinstance(raw, dict):
            raise ProfileConfigError(f"Profile '{name}' must be an object.")
        profiles[name] = _profile_from_mapping(name, raw, defaults)

    logger.info(f"Loaded {len(profiles)} connection profile(s) from {expanded}.")
    return profiles


def load_profiles_from_env(names_csv: str) -> Dict[str, DbProfile]:
    """Load profiles named by ``MARIADB_PROFILES`` from ``MARIADB_PROFILE_<NAME>_*`` vars."""
    names = [part.strip() for part in names_csv.split(",") if part.strip()]
    if not names:
        raise ProfileConfigError("MARIADB_PROFILES is set but lists no profile names.")

    fields = {
        "host": "HOST",
        "port": "PORT",
        "user": "USER",
        "password": "PASSWORD",
        "database": "DATABASE",
        "charset": "CHARSET",
        "read_only": "READ_ONLY",
        "max_pool_size": "MAX_POOL_SIZE",
        "ssl": "SSL",
        "ssl_ca": "SSL_CA",
        "ssl_cert": "SSL_CERT",
        "ssl_key": "SSL_KEY",
        "ssl_verify_cert": "SSL_VERIFY_CERT",
        "ssl_verify_identity": "SSL_VERIFY_IDENTITY",
    }

    profiles: Dict[str, DbProfile] = {}
    for raw_name in names:
        name = _validate_name(raw_name)
        prefix = f"MARIADB_PROFILE_{env_key(name)}_"
        raw = {
            field: os.environ[prefix + suffix]
            for field, suffix in fields.items()
            if prefix + suffix in os.environ
        }
        if not raw:
            raise ProfileConfigError(
                f"Profile '{name}' is listed in MARIADB_PROFILES but has no {prefix}* variables."
            )
        profiles[name] = _profile_from_mapping(name, raw, {})

    logger.info(f"Loaded {len(profiles)} connection profile(s) from MARIADB_PROFILE_* env vars.")
    return profiles


def legacy_profile() -> DbProfile:
    """The single connection described by the historical ``DB_*`` env vars."""
    return DbProfile(
        name=LEGACY_PROFILE_NAME,
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset=DB_CHARSET,
        read_only=MCP_READ_ONLY,
        max_pool_size=MCP_MAX_POOL_SIZE,
        ssl=DB_SSL,
        ssl_ca=DB_SSL_CA,
        ssl_cert=DB_SSL_CERT,
        ssl_key=DB_SSL_KEY,
        ssl_verify_cert=DB_SSL_VERIFY_CERT,
        ssl_verify_identity=DB_SSL_VERIFY_IDENTITY,
    )


def load_profiles() -> Dict[str, DbProfile]:
    """Resolve the configured profiles. Always returns at least one entry."""
    path = os.getenv("MARIADB_PROFILES_FILE")
    if path:
        return load_profiles_from_file(path)

    names_csv = os.getenv("MARIADB_PROFILES")
    if names_csv:
        return load_profiles_from_env(names_csv)

    logger.info(
        "No MARIADB_PROFILES_FILE/MARIADB_PROFILES set; using the single legacy DB_* connection "
        f"as profile '{LEGACY_PROFILE_NAME}'."
    )
    return {LEGACY_PROFILE_NAME: legacy_profile()}


def eager_profile_names(profiles: Dict[str, DbProfile]) -> list:
    """Profiles to connect at startup (``MARIADB_EAGER_PROFILES``; default: none).

    Pools are created lazily so that carrying 15 endpoints costs 15 dicts, not 15 open
    connections. Set this when you would rather fail fast on bad credentials.
    """
    raw = os.getenv("MARIADB_EAGER_PROFILES", "").strip()
    if not raw:
        # Unconfigured single-endpoint setups keep the historical fail-fast behaviour:
        # the pool opens at startup, so bad credentials surface immediately.
        if list(profiles) == [LEGACY_PROFILE_NAME]:
            return [LEGACY_PROFILE_NAME]
        return []
    if raw == "*":
        return list(profiles)
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in wanted if name not in profiles]
    if unknown:
        logger.warning(f"MARIADB_EAGER_PROFILES names unknown profiles: {unknown}")
    return [name for name in wanted if name in profiles]


__all__ = [
    "DbProfile",
    "LEGACY_PROFILE_NAME",
    "ProfileConfigError",
    "eager_profile_names",
    "env_key",
    "legacy_profile",
    "load_profiles",
    "load_profiles_from_env",
    "load_profiles_from_file",
    "replace",
]

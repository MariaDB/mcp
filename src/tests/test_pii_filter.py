#!/usr/bin/env python3
"""Unit tests for the PII filter.

These tests use a fully synthetic blocklist (``acme_corp`` database with
made-up tables and columns) and never touch a real database. They exercise
the filter logic directly by monkeypatching the module-level enable flag
and the cached blocklist.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make ``import pii_filter`` work the same way the server does.
SRC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC_DIR))

# We need to set the env vars BEFORE importing config/pii_filter so that the
# module-level PII_FILTER_ENABLED constant in pii_filter resolves to True.
os.environ.setdefault("PII_FILTER_ENABLED", "true")
os.environ.setdefault("DB_USER", "test")  # silence config warnings

import pii_filter  # noqa: E402


SYNTHETIC_BLOCKLIST_YAML = """
acme_corp:
  customers:
    - email
    - phone
    - real_name
  payment_methods:
    - card_number
    - cvv
  audit_pii: "*"

acme_reporting:
  events:
    - ip_address
"""


class PIIFilterTestBase(unittest.TestCase):
    """Shared setup: write a synthetic blocklist to a temp file and point the
    filter at it. Each test gets a fresh blocklist cache.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._yaml_path = Path(cls._tmpdir.name) / "blocklist.yaml"
        cls._yaml_path.write_text(SYNTHETIC_BLOCKLIST_YAML)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def setUp(self):
        # Force-enable the filter and point it at the synthetic YAML.
        # We patch the module-level constants directly because they were
        # bound at import time from config.py.
        self._saved_enabled = pii_filter.PII_FILTER_ENABLED
        self._saved_path = pii_filter.PII_CONFIG_PATH
        pii_filter.PII_FILTER_ENABLED = True
        pii_filter.PII_CONFIG_PATH = str(self._yaml_path)
        pii_filter.reload_blocklist()

    def tearDown(self):
        pii_filter.PII_FILTER_ENABLED = self._saved_enabled
        pii_filter.PII_CONFIG_PATH = self._saved_path
        pii_filter.reload_blocklist()


class TestBlocklistLoading(PIIFilterTestBase):

    def test_blocked_cols_for_known_table(self):
        cols = pii_filter.blocked_cols_for("acme_corp", "customers")
        self.assertEqual(cols, {"email", "phone", "real_name"})

    def test_blocked_cols_case_insensitive(self):
        self.assertEqual(
            pii_filter.blocked_cols_for("ACME_CORP", "Customers"),
            {"email", "phone", "real_name"},
        )

    def test_blocked_cols_unknown_table_is_empty(self):
        self.assertEqual(pii_filter.blocked_cols_for("acme_corp", "products"), set())

    def test_blocked_cols_unknown_db_is_empty(self):
        self.assertEqual(pii_filter.blocked_cols_for("nonexistent", "anything"), set())

    def test_is_fully_blocked(self):
        self.assertTrue(pii_filter.is_fully_blocked("acme_corp", "audit_pii"))
        self.assertFalse(pii_filter.is_fully_blocked("acme_corp", "customers"))

    def test_all_blocked_names_in_db(self):
        names = pii_filter.all_blocked_names_in_db("acme_corp")
        # Union of customers + payment_methods (audit_pii is wildcard, names unknown).
        self.assertEqual(
            names,
            {"email", "phone", "real_name", "card_number", "cvv"},
        )


class TestSchemaFiltering(PIIFilterTestBase):

    def test_filter_schema_drops_blocked_columns(self):
        schema = {
            "id": {"type": "int"},
            "email": {"type": "varchar(255)"},
            "real_name": {"type": "varchar(255)"},
            "signup_date": {"type": "date"},
        }
        result = pii_filter.filter_schema("acme_corp", "customers", schema)
        self.assertEqual(set(result.keys()), {"id", "signup_date"})

    def test_filter_schema_fully_blocked_returns_empty(self):
        schema = {"id": {"type": "int"}, "blob": {"type": "text"}}
        result = pii_filter.filter_schema("acme_corp", "audit_pii", schema)
        self.assertEqual(result, {})

    def test_filter_schema_no_blocked_columns_passes_through(self):
        schema = {"id": {"type": "int"}, "label": {"type": "varchar(50)"}}
        result = pii_filter.filter_schema("acme_corp", "categories", schema)
        self.assertEqual(result, schema)

    def test_filter_table_list_hides_wildcard_tables(self):
        tables = ["customers", "products", "audit_pii", "orders"]
        result = pii_filter.filter_table_list("acme_corp", tables)
        # customers stays visible (partially blocked); audit_pii is hidden.
        self.assertEqual(result, ["customers", "products", "orders"])


class TestQueryPreflight(PIIFilterTestBase):

    def test_allows_safe_select(self):
        # No blocked columns in projection.
        pii_filter.check_query("SELECT id, signup_date FROM customers", "acme_corp")

    def test_blocks_blocked_column_in_projection(self):
        with self.assertRaises(pii_filter.PIIBlockedError) as cm:
            pii_filter.check_query("SELECT email FROM customers", "acme_corp")
        self.assertIn("email", str(cm.exception))

    def test_blocks_blocked_column_with_alias(self):
        # SELECT email AS x -- aliasing must not hide the underlying column.
        with self.assertRaises(pii_filter.PIIBlockedError):
            pii_filter.check_query("SELECT email AS x FROM customers", "acme_corp")

    def test_blocks_blocked_column_inside_expression(self):
        # Expressions that READ a blocked column are still refused.
        with self.assertRaises(pii_filter.PIIBlockedError):
            pii_filter.check_query(
                "SELECT SUBSTRING(email, 1, 3) FROM customers", "acme_corp"
            )

    def test_blocks_select_star_when_table_has_pii(self):
        with self.assertRaises(pii_filter.PIIBlockedError) as cm:
            pii_filter.check_query("SELECT * FROM customers", "acme_corp")
        self.assertIn("SELECT *", str(cm.exception))

    def test_allows_select_star_when_table_has_no_pii(self):
        # ``products`` is not in the blocklist -> SELECT * is fine.
        pii_filter.check_query("SELECT * FROM products", "acme_corp")

    def test_lenient_on_where_clause(self):
        # Lenient policy: a blocked column may appear in WHERE because it
        # doesn't show up in the result rows.
        pii_filter.check_query(
            "SELECT id FROM customers WHERE email = 'x@example.com'",
            "acme_corp",
        )

    def test_lenient_on_join_on(self):
        pii_filter.check_query(
            "SELECT c.id FROM customers c "
            "JOIN payment_methods p ON c.email = p.billing_email",
            "acme_corp",
        )

    def test_lenient_on_order_by_group_by(self):
        pii_filter.check_query(
            "SELECT id FROM customers GROUP BY email ORDER BY phone",
            "acme_corp",
        )

    def test_qualified_column_blocked(self):
        with self.assertRaises(pii_filter.PIIBlockedError):
            pii_filter.check_query(
                "SELECT c.email FROM customers c", "acme_corp"
            )

    def test_parse_failure_does_not_raise(self):
        # Pre-flight is best-effort: unparseable SQL must not raise here,
        # because the post-filter will still strip blocked columns.
        # This SQL has obviously broken syntax.
        pii_filter.check_query("THIS IS NOT VALID SQL ::: ;;;", "acme_corp")


class TestRowFiltering(PIIFilterTestBase):

    def test_drops_blocked_columns_from_rows(self):
        rows = [
            {"id": 1, "email": "a@example.com", "real_name": "Anil"},
            {"id": 2, "email": "b@example.com", "real_name": "Bob"},
        ]
        result = pii_filter.filter_rows(
            rows, database="acme_corp", sql="SELECT id, email, real_name FROM customers"
        )
        self.assertEqual(result, [{"id": 1}, {"id": 2}])

    def test_passes_through_when_no_blocked_columns_match(self):
        rows = [{"id": 1, "label": "x"}]
        result = pii_filter.filter_rows(
            rows, database="acme_corp", sql="SELECT id, label FROM products"
        )
        self.assertEqual(result, rows)

    def test_unparseable_sql_falls_back_to_db_wide_strip(self):
        # If we can't parse the SQL, drop any column whose name matches ANY
        # blocked column in the DB. Conservative.
        rows = [{"id": 1, "email": "a@example.com", "ip_address": "1.2.3.4"}]
        result = pii_filter.filter_rows(
            rows,
            database="acme_corp",
            sql="THIS IS NOT VALID SQL ::: ;;;",
        )
        # email is in acme_corp's blocklist; ip_address is not (it's in
        # acme_reporting, a different DB).
        self.assertEqual(result, [{"id": 1, "ip_address": "1.2.3.4"}])

    def test_no_sql_strips_db_wide(self):
        rows = [{"id": 1, "email": "a@example.com"}]
        result = pii_filter.filter_rows(rows, database="acme_corp", sql=None)
        self.assertEqual(result, [{"id": 1}])

    def test_case_insensitive_column_match(self):
        rows = [{"ID": 1, "Email": "a@example.com"}]
        result = pii_filter.filter_rows(
            rows, database="acme_corp", sql="SELECT id, email FROM customers"
        )
        # Email matches blocked "email" case-insensitively; original casing
        # is preserved for the keys we keep.
        self.assertEqual(result, [{"ID": 1}])

    def test_empty_rows_returns_empty(self):
        self.assertEqual(
            pii_filter.filter_rows([], database="acme_corp", sql="SELECT 1"),
            [],
        )


class TestDisabledFilter(unittest.TestCase):
    """When the filter is disabled, every function must short-circuit."""

    def setUp(self):
        self._saved = pii_filter.PII_FILTER_ENABLED
        pii_filter.PII_FILTER_ENABLED = False
        pii_filter.reload_blocklist()

    def tearDown(self):
        pii_filter.PII_FILTER_ENABLED = self._saved
        pii_filter.reload_blocklist()

    def test_is_enabled_false(self):
        self.assertFalse(pii_filter.is_enabled())

    def test_blocked_cols_for_returns_empty(self):
        self.assertEqual(pii_filter.blocked_cols_for("any", "any"), set())

    def test_filter_schema_passes_through(self):
        schema = {"email": {"type": "varchar"}, "id": {"type": "int"}}
        self.assertEqual(pii_filter.filter_schema("any", "any", schema), schema)

    def test_filter_rows_passes_through(self):
        rows = [{"email": "a@b.com", "id": 1}]
        self.assertEqual(
            pii_filter.filter_rows(rows, database="any", sql="SELECT *"),
            rows,
        )

    def test_check_query_never_raises(self):
        # Even queries that would obviously be blocked when enabled.
        pii_filter.check_query("SELECT email FROM customers", "acme_corp")
        pii_filter.check_query("SELECT * FROM customers", "acme_corp")


if __name__ == "__main__":
    unittest.main()

"""Smoke test: the server must start on a brand-new database (stdlib unittest, no dependencies).

    python -m unittest discover -s tests

init_db() runs SCHEMA -> migrate() -> seed() on an empty database, so any migrate() step that needs seeded reference data
(species, sites ...) has to cope with that table being empty. This caught `FOREIGN KEY constraint failed` in the SKU -> species backfill.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="kelp_fresh_")
os.environ["KELP_ERP_DB"] = os.path.join(TMP, "fresh.db")        # set BEFORE the server module is imported
os.environ["KELP_ERP_UPLOADS"] = os.path.join(TMP, "uploads")
sys.path.insert(0, os.path.dirname(HERE))
import kelp_erp_server as k  # noqa: E402


class FreshDatabase(unittest.TestCase):
    def test_boots_twice_and_links_skus_to_species(self):
        k.init_db()                      # first boot: empty database
        k.init_db()                      # restart: migrations are idempotent
        conn = sqlite3.connect(k.DB_PATH)
        links = dict(conn.execute("SELECT sku_code, group_concat(species_code) FROM fg_sku_species GROUP BY sku_code"))
        self.assertEqual(sorted(links["FIELDKELP"].split(",")), ["MT", "SL"])
        self.assertEqual(links["KELPIVEX"], "SL")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

"""A brand-new database must start (and restart).

init_db() runs SCHEMA -> migrate() -> seed() on an empty database, so any migrate() step that needs seeded reference data (species, sites ...)
has to cope with that table being empty. This caught `FOREIGN KEY constraint failed` in the SKU -> species backfill (PR #4).
"""
import sqlite3


def test_boots_twice_and_links_skus_to_species(fresh_server):
    fresh_server.restart()                      # second boot on the same database: migrations must be idempotent
    conn = sqlite3.connect(fresh_server.db_path)
    try:
        links = dict(conn.execute("SELECT sku_code, group_concat(species_code) FROM fg_sku_species GROUP BY sku_code"))
        assert sorted(links["FIELDKELP"].split(",")) == ["MT", "SL"]
        assert links["KELPIVEX"] == "SL"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    finally:
        conn.close()
    assert fresh_server.admin_client().get("/api/me").status == 200      # and it still answers after the restart

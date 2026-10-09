"""Smoke tests that use the harness: a real server on a throwaway database.

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ADMIN_EMAIL, ADMIN_PASSWORD, Server  # noqa: E402


class ApiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server().start()          # one server for the whole class: boots in about a second
        cls.admin = cls.srv.admin_client()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_serves_the_app(self):
        r = self.srv.client().get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("KelpWorks", r.text)

    def test_uses_a_throwaway_database(self):
        self.assertNotEqual(os.path.abspath(self.srv.db_path), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kelp_erp.db"))
        self.assertTrue(os.path.exists(self.srv.db_path))

    def test_api_requires_a_token(self):
        self.assertEqual(self.srv.client().get("/api/refdata").status, 401)
        self.assertEqual(self.srv.client().get("/api/refdata", token="not-a-real-token").status, 401)

    def test_login(self):
        bad = self.srv.client().login(ADMIN_EMAIL, "wrong-password")
        self.assertEqual(bad.status, 401)
        good = self.srv.client().login(ADMIN_EMAIL, ADMIN_PASSWORD)
        self.assertEqual(good.status, 200)
        self.assertEqual(good.json["user"]["role"], "admin")

    def test_me(self):
        r = self.admin.get("/api/me")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json["email"], ADMIN_EMAIL)

    def test_seed_data_is_loaded(self):
        ref = self.admin.get("/api/refdata").raise_for_status().json
        self.assertTrue(ref["species"], "species should be seeded")
        self.assertIn("SL", [s["code"] for s in ref["species"]])
        self.assertIn("KELPIVEX", [k["code"] for k in ref["skus"]])

    def test_admin_only_routes(self):
        user, _ = self.srv.make_user(role="user")
        self.assertEqual(user.get("/api/users").status, 403)
        r = self.admin.get("/api/users")
        self.assertEqual(r.status, 200)
        self.assertIn(ADMIN_EMAIL, [u["email"] for u in r.json["users"]])


if __name__ == "__main__":
    unittest.main()

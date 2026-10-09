"""pytest fixtures for KelpWorks. The machinery lives in harness.py (standard library only); this file only wires it into pytest.

Fixtures
    server      one throwaway server per test session (a real subprocess, its own temporary database, seeded like a first deploy)
    anon        a client with no token
    admin       a client logged in as the admin
    make_user   factory: make_user(role="user", isQualityManager=True ...) -> (client logged in as that new user, user dict)
    fresh_server  a function-scoped server for tests that change a lot, restart it, or must start from an empty database
"""
import os
import sys

# The tests import kelp_erp_server in-process only to read its registries. Give that import a secret so it does not write a kelp_secret.key
# next to the repository's own database (the servers the tests START get their own explicit secrets from harness.py).
os.environ.setdefault("KELP_ERP_SECRET", "in-process-import-secret-for-tests-only-0123456789")

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Server  # noqa: E402


@pytest.fixture(scope="session")
def server():
    with Server() as srv:
        yield srv


@pytest.fixture
def fresh_server():
    with Server() as srv:
        yield srv


@pytest.fixture
def anon(server):
    return server.client()


@pytest.fixture
def admin(server):
    return server.admin_client()


@pytest.fixture
def make_user(server):
    return server.make_user

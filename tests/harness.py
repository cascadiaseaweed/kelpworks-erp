"""Test harness for KelpWorks ERP: standard library only (no pytest needed to import it).

    from harness import Server

    with Server() as srv:                 # boots the REAL server in a subprocess on a free port
        admin = srv.admin_client()        # an API client already logged in as the admin
        status, body = admin.get("/api/refdata")

What it gives a test:
  * Server       a throwaway server: its own temporary SQLite database + uploads folder (never the repo's kelp_erp.db), created and seeded
                 exactly the way a first deploy is (init_db: schema -> migrate -> seed -> ensure_users). Pass `db_path=` to boot it on a COPY
                 of an existing database instead (used to check that migrations upgrade an older database).
  * ApiClient    a thin urllib client: JSON in / JSON out, bearer-token auth, `.login()`, and a `Response` with .status / .json / .body / .headers.
  * helpers      `Server.admin_client()`, `Server.make_user()` (an extra non-admin user, logged in), `Server.stop()`.

The server runs as a separate process, so the tests exercise the same code path as production (HTTP, routing, auth, SQLite on disk).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(ROOT, "kelp_erp_server.py")

# Fixed credentials for the throwaway server (it only ever listens on 127.0.0.1)
ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "test-admin-pass-1"
SIGNING_SECRET = "test-signing-secret-not-for-production"


class Response:
    """What ApiClient returns: the HTTP status, the raw body bytes and the headers; `.json` parses the body on demand."""

    def __init__(self, status, body, headers):
        self.status, self.body, self.headers = status, body, headers

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def raise_for_status(self):
        if not self.ok:
            raise AssertionError("HTTP %s: %s" % (self.status, self.text[:500]))
        return self

    def __repr__(self):
        return "<Response %s %d bytes>" % (self.status, len(self.body or b""))


class ApiClient:
    """A minimal HTTP client for the KelpWorks API. Never raises on an HTTP error status: tests assert on `.status` themselves."""

    def __init__(self, base_url, token=None, timeout=30):
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def request(self, method, path, body=None, token=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method.upper())
        if data is not None:
            req.add_header("Content-Type", "application/json")
        tok = token if token is not None else self.token
        if tok:
            req.add_header("Authorization", "Bearer " + tok)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return Response(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), dict(e.headers))

    def post_bytes(self, path, data, content_type="application/zip", token=None):
        """POST a raw binary body (a file upload such as a backup .zip)."""
        req = urllib.request.Request(self.base_url + path, data=data, method="POST")
        req.add_header("Content-Type", content_type)
        tok = token if token is not None else self.token
        if tok:
            req.add_header("Authorization", "Bearer " + tok)
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120)) as r:
                return Response(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), dict(e.headers))

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def login(self, email, password):
        """Log in and keep the token on this client. Returns the login Response (check `.status` for a failed login)."""
        r = self.post("/api/auth/login", {"email": email, "password": password}, token="")
        if r.ok:
            self.token = r.json["token"]
        return r


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """A throwaway KelpWorks server in a subprocess. Use as a context manager, or call start() / stop()."""

    def __init__(self, db_path=None, env=None, startup_timeout=40):
        self.db_source, self.extra_env, self.startup_timeout = db_path, env or {}, startup_timeout
        self.tmp = self.proc = self.port = None
        self.users = 0

    # -- lifecycle -- #
    def start(self):
        self.tmp = tempfile.mkdtemp(prefix="kelp_test_")
        self.db_path = os.path.join(self.tmp, "kelp.db")
        if self.db_source:
            shutil.copyfile(self.db_source, self.db_path)           # boot on a COPY, never on the original
        self.log_path = os.path.join(self.tmp, "server.log")
        return self._launch()

    def restart(self):
        """Stop the server process and boot it again on the SAME database and uploads (what a redeploy does)."""
        self._stop_process()
        return self._launch()

    def _launch(self):
        self.port = free_port()
        env = dict(os.environ, PORT=str(self.port), HOST="127.0.0.1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                   KELP_ERP_DB=self.db_path, KELP_ERP_UPLOADS=os.path.join(self.tmp, "uploads"), KELP_ERP_SECRET=SIGNING_SECRET,
                   KELP_ERP_ADMIN_EMAIL=ADMIN_EMAIL, KELP_ERP_ADMIN_PASSWORD=ADMIN_PASSWORD, KELP_ERP_INITIAL_PASSWORD="initial-pass-123")
        env.update(self.extra_env)
        self._log = open(self.log_path, "ab")
        self.proc = subprocess.Popen([sys.executable, SERVER_PY], cwd=ROOT, env=env, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                log = self.log_tail()                      # read it BEFORE stop() deletes the temporary folder
                self.stop()
                raise RuntimeError("The server exited during startup:\n" + log)
            try:
                if urllib.request.urlopen(self.url + "/", timeout=2).status == 200:
                    return self
            except OSError:
                time.sleep(0.2)
        log = self.log_tail()
        self.stop()
        raise RuntimeError("The server did not answer within %ss:\n%s" % (self.startup_timeout, log))

    def _stop_process(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_log", None):
            self._log.close()

    def stop(self):
        self._stop_process()
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- access -- #
    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def log_tail(self, n=40):
        try:
            with open(self.log_path, "rb") as f:
                return b"".join(f.readlines()[-n:]).decode("utf-8", "replace")
        except OSError:
            return "(no log)"

    def client(self):
        return ApiClient(self.url)

    def admin_client(self):
        """An ApiClient already logged in as the admin."""
        c = self.client()
        c.login(ADMIN_EMAIL, ADMIN_PASSWORD).raise_for_status()
        return c

    def make_user(self, role="user", **flags):
        """Create an extra user through the admin API and return (client logged in as them, info dict with their email + password). `flags` e.g. isQualityManager=True."""
        self.users += 1
        email, password = "tester%d@test.local" % self.users, "tester-pass-%d" % (1000 + self.users)
        body = dict({"name": "Tester %d" % self.users, "email": email, "password": password, "role": role, "mustChange": False}, **flags)
        created = self.admin_client().post("/api/users", body)
        created.raise_for_status()
        c = self.client()
        c.login(email, password).raise_for_status()
        info = created.json if isinstance(created.json, dict) else {}
        return c, dict(info, email=email, password=password)

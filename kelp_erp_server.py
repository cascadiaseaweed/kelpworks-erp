#!/usr/bin/env python3
"""
KelpWorks ERP — a small manufacturing/processing ERP for a kelp-extract business.

Pure Python standard library (no pip install), built in the same shape as the
CascadiaTime and KelpStock apps in this repo:
  - sqlite3  : single-file relational database (kelp_erp.db)
  - http     : threaded HTTP server serving both the REST API and the web UI
  - hashlib  : PBKDF2 password hashing
  - hmac     : signed bearer tokens

What it models (a kelp -> liquid extract process):
  1. Stabilized inventory : ground kelp stored in 1000 L IBC totes with citric
     acid. Each tote is one lot, named  SITE-SPECIES-YYYYMMDD-TOTE  (e.g.
     JAM-SL-20260504-003). Totes from one harvest day share an *average* weight
     (total kg harvested / number of totes).
  2. Production runs       : pull stabilized totes, dilute to a target TDS, add
     citric acid + potassium sorbate as preservatives, and bottle Liquid Kelp
     Extract (LKE) finished goods. Each run gets a Processing Lot # (PR-...).
  3. Finished goods        : two SKUs (Saccharina LKE, Macrocystis LKE) in IBC /
     4L / 1L / 250 ml packs.
  4. Consumables           : Citric Acid, Potassium Sorbate, empty IBC totes —
     auto-deducted by production runs, with reorder alerts.
  5. Barcode labels        : every lot (tote / FG / processing) prints a Code128
     barcode label from the web UI.

Run:
    python kelp_erp_server.py
Then open http://localhost:8002

Environment variables (optional):
    PORT               default 8002
    HOST               default 0.0.0.0
    KELP_ERP_SECRET    token signing secret (set this in production!)
    KELP_ERP_DB        database file path (default ./kelp_erp.db)
    KELP_ERP_ADMIN_EMAIL / KELP_ERP_ADMIN_PASSWORD
"""

import os
import io
import json
import time
import hmac
import base64
import zipfile
import hashlib
import sqlite3
import secrets
import datetime
from xml.sax.saxutils import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("KELP_ERP_DB", os.path.join(BASE_DIR, "kelp_erp.db"))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
SEED_FILE = os.path.join(BASE_DIR, "seed.json")
UPLOAD_DIR = os.environ.get("KELP_ERP_UPLOADS", os.path.join(BASE_DIR, "uploads"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file
PORT = int(os.environ.get("PORT", "8002"))
HOST = os.environ.get("HOST", "0.0.0.0")
DEV_SECRET = "dev-secret-change-me"
SECRET = os.environ.get("KELP_ERP_SECRET", DEV_SECRET).encode("utf-8")
TOKEN_TTL = 60 * 60 * 12  # 12 hours

ADMIN_EMAIL = os.environ.get("KELP_ERP_ADMIN_EMAIL", "admin@kelp.local")
ADMIN_PASSWORD = os.environ.get("KELP_ERP_ADMIN_PASSWORD", "kelp1234")

# Initial staff roster — created (if missing) on startup with a temporary
# password and forced to reset it on first login.
INITIAL_USERS = [
    "dpedde@cascadiaseaweed.com",
    "dboire@cascadiaseaweed.com",
    "nwrana@cascadiaseaweed.com",
]
INITIAL_USER_PASSWORD = os.environ.get("KELP_ERP_INITIAL_PASSWORD", "Cascadia123!")
MIN_PASSWORD_LEN = 8

PACKAGE_SIZES = {            # litres per unit of each package type
    "IBC": 1000.0,
    "4L": 4.0,
    "1L": 1.0,
    "250ml": 0.25,
}

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    must_change_password INTEGER NOT NULL DEFAULT 0,
    active               INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS species (
    code   TEXT PRIMARY KEY,     -- SL, MT
    name   TEXT NOT NULL,        -- Saccharina latissima
    common TEXT                  -- Sugar Kelp
);

CREATE TABLE IF NOT EXISTS sites (
    code TEXT PRIMARY KEY,       -- JAM, DIP, COR ...
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    name TEXT PRIMARY KEY
);

-- Stabilized inventory: one row per IBC tote of ground, citric-stabilized kelp.
CREATE TABLE IF NOT EXISTS tote_lots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_number    TEXT NOT NULL UNIQUE,   -- SITE-SPECIES-YYYYMMDD-TOTE
    site_code     TEXT REFERENCES sites(code),
    species_code  TEXT REFERENCES species(code),
    harvest_year  INTEGER,
    checkin_date  TEXT,                   -- YYYY-MM-DD
    tote_number   INTEGER,
    volume_l      REAL DEFAULT 1000,
    ph            REAL,
    ph_updated    TEXT,                   -- date the pH reading was last logged
    avg_weight_kg REAL,                   -- batch total kg / tote count
    location      TEXT,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'in_stock',  -- in_stock | consumed | disposed
    run_id        INTEGER REFERENCES production_runs(id),
    disposed_date TEXT,                   -- date written off (NULL unless disposed)
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tote_status ON tote_lots(status);

-- pH reading history for stabilized totes (one row per logged reading).
CREATE TABLE IF NOT EXISTS tote_ph_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tote_lot_id   INTEGER NOT NULL REFERENCES tote_lots(id) ON DELETE CASCADE,
    ph            REAL NOT NULL,
    reading_date  TEXT NOT NULL,          -- YYYY-MM-DD
    note          TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_phlog_tote ON tote_ph_log(tote_lot_id);

-- Location move history for totes and finished-goods lots.
CREATE TABLE IF NOT EXISTS location_moves (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL,          -- 'tote' | 'fg'
    entity_id     INTEGER NOT NULL,
    lot           TEXT,
    from_location TEXT,
    to_location   TEXT,
    qty           REAL,                   -- units moved (FG); NULL for a whole tote
    moved_date    TEXT NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moves_entity ON location_moves(entity_type, entity_id);

-- Audit log of edits to production runs (one row per changed field).
CREATE TABLE IF NOT EXISTS run_edits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    user_name  TEXT,
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    edited_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runedits_run ON run_edits(run_id);

-- Documents attached to a production run (lab results, paper logs, images...).
CREATE TABLE IF NOT EXISTS run_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    content_type TEXT,
    size         INTEGER,
    stored_name  TEXT NOT NULL,         -- opaque name on disk
    uploaded_by  TEXT,
    uploaded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attach_run ON run_attachments(run_id);

CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    contact    TEXT,
    email      TEXT,
    phone      TEXT,
    address    TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- A shipment of finished goods to a customer.
CREATE TABLE IF NOT EXISTS shipments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_no TEXT NOT NULL UNIQUE,        -- SH-YYYYMMDD-NNN
    customer_id INTEGER REFERENCES customers(id),
    ship_date   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'shipped',  -- shipped | delivered | cancelled
    carrier     TEXT,
    tracking_no TEXT,
    reference   TEXT,                        -- customer PO / order ref
    ship_to     TEXT,                        -- address snapshot
    notes       TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL
);

-- One line per finished-goods lot shipped (this is the traceability record).
CREATE TABLE IF NOT EXISTS shipment_lines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id   INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    fg_lot_id     INTEGER REFERENCES fg_lots(id),
    fg_lot_number TEXT,                      -- snapshot (survives lot edits)
    sku_code      TEXT,
    package_size  TEXT,
    qty           REAL NOT NULL,
    litres_each   REAL
);
CREATE INDEX IF NOT EXISTS idx_shipline_ship ON shipment_lines(shipment_id);

-- Inventory write-offs / disposals (reason is required). Covers stabilized
-- totes, finished-goods lots, and consumables.
CREATE TABLE IF NOT EXISTS disposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL,          -- 'tote' | 'fg' | 'consumable'
    entity_id     INTEGER,
    ref           TEXT,                   -- lot number / consumable name snapshot
    species_code  TEXT,                   -- totes
    sku_code      TEXT,                   -- finished goods
    qty           REAL,                   -- kg (tote) | units (fg) | amount (consumable)
    unit          TEXT,
    litres        REAL,                   -- finished-goods litres (else NULL)
    reason        TEXT NOT NULL,
    disposed_by   TEXT,
    disposed_date TEXT NOT NULL,          -- YYYY-MM-DD
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disposals_date ON disposals(disposed_date);

-- Consumables & packaging (citric acid, potassium sorbate, empty IBCs ...).
CREATE TABLE IF NOT EXISTS consumables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    unit          TEXT NOT NULL,
    on_hand       REAL NOT NULL DEFAULT 0,
    reorder_level REAL NOT NULL DEFAULT 0,
    cost_per_unit REAL,
    location      TEXT
);

CREATE TABLE IF NOT EXISTS consumable_txns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    consumable_id INTEGER NOT NULL REFERENCES consumables(id),
    delta         REAL NOT NULL,          -- + receipt, - usage
    reason        TEXT,
    ref           TEXT,                   -- e.g. processing lot
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fg_skus (
    code         TEXT PRIMARY KEY,        -- SACC-LKE, MACRO-LKE
    name         TEXT NOT NULL,
    species_code TEXT REFERENCES species(code)
);

-- Production runs: stabilized totes -> diluted, preserved LKE.
CREATE TABLE IF NOT EXISTS production_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    processing_lot TEXT NOT NULL UNIQUE,  -- PR-YYYYMMDD-NNN
    run_date       TEXT NOT NULL,
    species_code   TEXT REFERENCES species(code),
    sku_code       TEXT REFERENCES fg_skus(code),
    input_kg       REAL DEFAULT 0,
    target_tds     REAL,
    output_litres  REAL DEFAULT 0,
    citric_kg      REAL DEFAULT 0,
    sorbate_kg     REAL DEFAULT 0,
    ibc_used       INTEGER DEFAULT 0,
    location       TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_inputs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    tote_lot_id INTEGER NOT NULL REFERENCES tote_lots(id)
);

-- Finished goods on hand: one row per (run, package size).
CREATE TABLE IF NOT EXISTS fg_lots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fg_lot_number TEXT NOT NULL UNIQUE,
    sku_code      TEXT REFERENCES fg_skus(code),
    run_id        INTEGER REFERENCES production_runs(id),
    package_size  TEXT NOT NULL,          -- IBC | 4L | 1L | 250ml
    qty           REAL NOT NULL DEFAULT 0,
    litres_each   REAL NOT NULL,
    produced_date TEXT,
    tds           REAL,
    location      TEXT,
    status        TEXT NOT NULL DEFAULT 'on_hand',  -- on_hand | hold | sold
    created_at    TEXT NOT NULL
);
"""


def now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today_iso():
    return datetime.date.today().isoformat()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def hash_password(password, salt=None, iterations=200_000):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), iterations)
    return f"pbkdf2${iterations}${salt}${dk.hex()}"


def verify_password(password, stored):
    try:
        _algo, iterations, salt, _ = stored.split("$")
        return hmac.compare_digest(stored, hash_password(password, salt, int(iterations)))
    except Exception:
        return False


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        seed(conn)
    ensure_users(conn)
    conn.commit()
    conn.close()


def migrate(conn):
    """Idempotent schema migrations for databases created before a column existed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tote_lots)")}
    if "ph_updated" not in cols:
        conn.execute("ALTER TABLE tote_lots ADD COLUMN ph_updated TEXT")
    if "disposed_date" not in cols:
        conn.execute("ALTER TABLE tote_lots ADD COLUMN disposed_date TEXT")
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(consumables)")}
    if "location" not in ccols:
        conn.execute("ALTER TABLE consumables ADD COLUMN location TEXT")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "role" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if "must_change_password" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    if "active" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")


def ensure_users(conn):
    """Idempotent: keep the configured admin an admin, and create the initial
    staff roster (with a temporary, must-change password) if they don't exist."""
    conn.execute("UPDATE users SET role='admin' WHERE email=?", (ADMIN_EMAIL.strip().lower(),))
    ts = now_iso()
    for email in INITIAL_USERS:
        email = email.strip().lower()
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO users (name,email,password_hash,role,must_change_password,active,created_at)"
            " VALUES (?,?,?,?,1,1,?)",
            (email.split("@")[0], email, hash_password(INITIAL_USER_PASSWORD), "user", ts))


def seed(conn):
    """First-run seed: admin user + reference data and the stabilized tote lots
    extracted from the 202605 inventory workbook (seed.json)."""
    ts = now_iso()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name,email,password_hash,role,created_at) VALUES (?,?,?,'admin',?)",
                ("Plant Admin", ADMIN_EMAIL.strip().lower(), hash_password(ADMIN_PASSWORD), ts))
    try:
        with open(SEED_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    for s in data.get("species", []):
        cur.execute("INSERT OR IGNORE INTO species (code,name,common) VALUES (?,?,?)",
                    (s["code"], s["name"], s.get("common")))
    for s in data.get("sites", []):
        cur.execute("INSERT OR IGNORE INTO sites (code,name) VALUES (?,?)", (s["code"], s["name"]))
    for name in data.get("locations", []):
        cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (name,))
    for c in data.get("consumables", []):
        cur.execute("INSERT OR IGNORE INTO consumables (name,unit,on_hand,reorder_level,cost_per_unit)"
                    " VALUES (?,?,?,?,?)",
                    (c["name"], c["unit"], c.get("on_hand", 0), c.get("reorder_level", 0),
                     c.get("cost_per_unit")))
    for k in data.get("fg_skus", []):
        cur.execute("INSERT OR IGNORE INTO fg_skus (code,name,species_code) VALUES (?,?,?)",
                    (k["code"], k["name"], k.get("species")))
    for t in data.get("tote_lots", []):
        cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (t.get("location"),))
        cur.execute(
            "INSERT OR IGNORE INTO tote_lots "
            "(lot_number,site_code,species_code,harvest_year,checkin_date,tote_number,"
            " volume_l,ph,avg_weight_kg,location,description,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'in_stock', ?)",
            (t["lot_number"], t["lot_number"].split("-")[0], t.get("species"),
             t.get("harvest_year"), t.get("checkin_date"), t.get("tote_number"),
             t.get("volume_l") or 1000, t.get("ph"), t.get("avg_weight_kg"),
             t.get("location"), t.get("description"), ts))
    conn.commit()


# --------------------------------------------------------------------------- #
# Auth tokens
# --------------------------------------------------------------------------- #
def _b64(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id):
    payload = {"uid": user_id, "exp": int(time.time()) + TOKEN_TTL}
    body = _b64(json.dumps(payload).encode("utf-8"))
    sig = _b64(hmac.new(SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token):
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(SECRET, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def tote_public(r):
    return {"id": r["id"], "lot": r["lot_number"], "site": r["site_code"],
            "species": r["species_code"], "harvestYear": r["harvest_year"],
            "checkinDate": r["checkin_date"], "toteNumber": r["tote_number"],
            "volumeL": r["volume_l"], "ph": r["ph"], "phUpdated": r["ph_updated"],
            "avgWeightKg": r["avg_weight_kg"],
            "location": r["location"], "description": r["description"],
            "status": r["status"], "runId": r["run_id"], "disposedDate": r["disposed_date"]}


def fg_public(r):
    return {"id": r["id"], "lot": r["fg_lot_number"], "sku": r["sku_code"],
            "runId": r["run_id"], "packageSize": r["package_size"], "qty": r["qty"],
            "litresEach": r["litres_each"], "litres": round((r["qty"] or 0) * (r["litres_each"] or 0), 2),
            "producedDate": r["produced_date"], "tds": r["tds"], "location": r["location"],
            "status": r["status"]}


def run_public(r):
    return {"id": r["id"], "processingLot": r["processing_lot"], "runDate": r["run_date"],
            "species": r["species_code"], "sku": r["sku_code"], "inputKg": r["input_kg"],
            "targetTds": r["target_tds"], "outputLitres": r["output_litres"],
            "citricKg": r["citric_kg"], "sorbateKg": r["sorbate_kg"], "ibcUsed": r["ibc_used"],
            "location": r["location"], "notes": r["notes"]}


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "KelpWorksERP/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---- helpers ---------------------------------------------------------- #
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError(400, "Invalid JSON body")

    def _auth(self, conn):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError(401, "Missing token")
        payload = read_token(header[7:])
        if not payload:
            raise ApiError(401, "Invalid or expired token")
        row = conn.execute("SELECT * FROM users WHERE id=?", (payload["uid"],)).fetchone()
        if not row:
            raise ApiError(401, "User not found")
        if not row["active"]:
            raise ApiError(403, "This account has been deactivated")
        return row

    def _require_admin(self, user):
        if user["role"] != "admin":
            raise ApiError(403, "Administrator access required")

    # ---- dispatch --------------------------------------------------------- #
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if "/attachments/" in path and path.endswith("/download"):
            return self._download_attachment(path)
        if path == "/api/reports/xlsx":
            return self._report_xlsx()
        if path.startswith("/api/"):
            return self._handle_api("GET")
        return self._serve_static(path)

    def _report_xlsx(self):
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            header = self.headers.get("Authorization", "")
            tok = header[7:] if header.startswith("Bearer ") else qs.get("token", [None])[0]
            if not read_token(tok or ""):
                return self._send_json({"error": "Invalid or missing token"}, 401)
            try:
                data = self.route_reports(qs, conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            spname = {r["code"]: (r["common"] or r["name"]) for r in conn.execute("SELECT * FROM species")}
            skname = {r["code"]: r["name"] for r in conn.execute("SELECT * FROM fg_skus")}
            content = report_workbook(data, spname, skname)
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition",
                             'attachment; filename="kelpworks-report-%s_%s.xlsx"'
                             % (data["from"], data["to"]))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:  # pragma: no cover
            self._send_json({"error": "Server error: %s" % e}, 500)
        finally:
            conn.close()

    def _download_attachment(self, path):
        """Stream an attachment's bytes. Auth via Bearer header or ?token= (so a
        PDF/image can open directly in a browser tab)."""
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            header = self.headers.get("Authorization", "")
            tok = header[7:] if header.startswith("Bearer ") else qs.get("token", [None])[0]
            if not read_token(tok or ""):
                return self._send_json({"error": "Invalid or missing token"}, 401)
            seg = [s for s in path.split("/") if s]   # api production :id attachments :aid download
            rid, aid = int(seg[2]), int(seg[4])
            r = conn.execute("SELECT * FROM run_attachments WHERE id=? AND run_id=?",
                             (aid, rid)).fetchone()
            if not r:
                return self._send_json({"error": "Attachment not found"}, 404)
            full = os.path.join(UPLOAD_DIR, r["stored_name"])
            if not os.path.isfile(full):
                return self._send_json({"error": "File missing on disk"}, 404)
            with open(full, "rb") as f:
                data = f.read()
            disp = "attachment" if qs.get("dl", [""])[0] else "inline"
            safe = r["filename"].replace('"', '').replace("\r", "").replace("\n", "")
            self.send_response(200)
            self.send_header("Content-Type", r["content_type"] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", '%s; filename="%s"' % (disp, safe))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # pragma: no cover
            self._send_json({"error": "Server error: %s" % e}, 500)
        finally:
            conn.close()

    def do_POST(self):
        return self._handle_api("POST")

    def do_PUT(self):
        return self._handle_api("PUT")

    def do_DELETE(self):
        return self._handle_api("DELETE")

    # ---- static files ----------------------------------------------------- #
    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("\\/")
        full = os.path.join(PUBLIC_DIR, safe)
        if not full.startswith(PUBLIC_DIR) or not os.path.isfile(full):
            full = os.path.join(PUBLIC_DIR, "index.html")
            if not os.path.isfile(full):
                self.send_error(404, "Not found")
                return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    # ---- API router ------------------------------------------------------- #
    def _handle_api(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        conn = db()
        try:
            result = self._route(method, parsed.path, query, conn)
            conn.commit()
            self._send_json(result if result is not None else {"ok": True})
        except ApiError as e:
            conn.rollback()
            self._send_json({"error": e.message}, status=e.status)
        except Exception as e:  # pragma: no cover
            conn.rollback()
            self._send_json({"error": "Server error: %s" % e}, status=500)
        finally:
            conn.close()

    def _route(self, method, path, query, conn):
        seg = [s for s in path.split("/") if s]

        if method == "POST" and seg == ["api", "auth", "login"]:
            return self.login(conn)

        user = self._auth(conn)  # everything below requires auth

        if method == "GET" and seg == ["api", "me"]:
            return self._me(user)
        if method == "POST" and seg == ["api", "me", "password"]:
            return self.change_my_password(conn, user)
        if seg[:2] == ["api", "users"]:
            return self.route_users(method, seg, conn, user)
        if method == "GET" and seg == ["api", "refdata"]:
            return self.refdata(conn)
        if method == "GET" and seg == ["api", "dashboard"]:
            return self.dashboard(conn)
        if seg[:2] == ["api", "totes"]:
            return self.route_totes(method, seg, query, conn)
        if seg[:2] == ["api", "harvest"]:
            return self.route_harvest(method, seg, conn)
        if seg[:2] == ["api", "consumables"]:
            return self.route_consumables(method, seg, conn)
        if seg[:2] == ["api", "production"]:
            return self.route_production(method, seg, conn, user)
        if seg[:2] == ["api", "fg"]:
            return self.route_fg(method, seg, query, conn)
        if seg[:2] == ["api", "customers"]:
            return self.route_customers(method, seg, conn)
        if seg[:2] == ["api", "shipments"]:
            return self.route_shipments(method, seg, query, conn, user)
        if method == "GET" and seg == ["api", "reports"]:
            return self.route_reports(query, conn)
        if method == "GET" and seg == ["api", "ledger"]:
            return self.ledger(query, conn)
        if seg == ["api", "dispose"] and method == "POST":
            return self.dispose(conn, user)
        if seg == ["api", "disposals"] and method == "GET":
            return self.list_disposals(query, conn)

        raise ApiError(404, "Unknown endpoint")

    # ---- auth ------------------------------------------------------------- #
    def login(self, conn):
        data = self._body_json()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise ApiError(401, "Invalid email or password")
        if not row["active"]:
            raise ApiError(403, "This account has been deactivated")
        return {"token": make_token(row["id"]), "user": self._me(row)}

    def _me(self, row):
        return {"id": row["id"], "name": row["name"], "email": row["email"],
                "role": row["role"], "mustChange": bool(row["must_change_password"])}

    # ---- users / admin ---------------------------------------------------- #
    def _user_public(self, r):
        return {"id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"],
                "active": bool(r["active"]), "mustChange": bool(r["must_change_password"]),
                "createdAt": r["created_at"]}

    def _users(self, conn):
        return [self._user_public(r) for r in conn.execute(
            "SELECT * FROM users ORDER BY active DESC, role DESC, email")]

    def _active_admin_count(self, conn, exclude_id=None):
        return conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1 AND id!=?",
            (exclude_id or -1,)).fetchone()["c"]

    def change_my_password(self, conn, user):
        d = self._body_json()
        if not verify_password(d.get("currentPassword") or "", user["password_hash"]):
            raise ApiError(400, "Current password is incorrect")
        newpw = d.get("newPassword") or ""
        if len(newpw) < MIN_PASSWORD_LEN:
            raise ApiError(400, "New password must be at least %d characters" % MIN_PASSWORD_LEN)
        conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                     (hash_password(newpw), user["id"]))
        return {"ok": True}

    def route_users(self, method, seg, conn, user):
        self._require_admin(user)
        if seg == ["api", "users"]:
            if method == "GET":
                return {"users": self._users(conn)}
            if method == "POST":
                d = self._body_json()
                name = (d.get("name") or "").strip()
                email = (d.get("email") or "").strip().lower()
                pw = d.get("password") or ""
                role = "admin" if d.get("role") == "admin" else "user"
                if not name or not email:
                    raise ApiError(400, "Name and email are required")
                if "@" not in email:
                    raise ApiError(400, "Enter a valid email address")
                if len(pw) < MIN_PASSWORD_LEN:
                    raise ApiError(400, "Password must be at least %d characters" % MIN_PASSWORD_LEN)
                if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                    raise ApiError(409, "A user with that email already exists")
                must_change = 0 if d.get("mustChange") is False else 1
                conn.execute(
                    "INSERT INTO users (name,email,password_hash,role,must_change_password,active,created_at)"
                    " VALUES (?,?,?,?,?,1,?)",
                    (name, email, hash_password(pw), role, must_change, now_iso()))
                return {"users": self._users(conn)}
        if len(seg) >= 3 and seg[2].isdigit():
            uid = int(seg[2])
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not target:
                raise ApiError(404, "User not found")

            if len(seg) == 4 and seg[3] == "password" and method == "POST":
                d = self._body_json()
                pw = d.get("password") or ""
                if len(pw) < MIN_PASSWORD_LEN:
                    raise ApiError(400, "Password must be at least %d characters" % MIN_PASSWORD_LEN)
                must_change = 0 if d.get("mustChange") is False else 1
                conn.execute("UPDATE users SET password_hash=?, must_change_password=? WHERE id=?",
                             (hash_password(pw), must_change, uid))
                return {"ok": True}

            if len(seg) == 3 and method == "PUT":
                d = self._body_json()
                new_role = d.get("role", target["role"])
                new_role = "admin" if new_role == "admin" else "user"
                new_active = int(bool(d.get("active", target["active"])))
                # Never strip the last active admin.
                demoting = target["role"] == "admin" and (new_role != "admin" or not new_active)
                if demoting and self._active_admin_count(conn, exclude_id=uid) == 0:
                    raise ApiError(400, "There must be at least one active administrator")
                conn.execute("UPDATE users SET name=?, role=?, active=? WHERE id=?",
                             ((d["name"].strip() if d.get("name") else target["name"]),
                              new_role, new_active, uid))
                return {"users": self._users(conn)}
        raise ApiError(404, "Unknown users endpoint")

    # ---- reference data --------------------------------------------------- #
    def refdata(self, conn):
        species = [dict(code=r["code"], name=r["name"], common=r["common"])
                   for r in conn.execute("SELECT * FROM species ORDER BY code")]
        sites = [dict(code=r["code"], name=r["name"])
                 for r in conn.execute("SELECT * FROM sites ORDER BY code")]
        locations = [r["name"] for r in conn.execute("SELECT name FROM locations ORDER BY name")]
        skus = [dict(code=r["code"], name=r["name"], species=r["species_code"])
                for r in conn.execute("SELECT * FROM fg_skus ORDER BY code")]
        customers = [self._customer_public(r) for r in conn.execute(
            "SELECT * FROM customers WHERE active=1 ORDER BY name")]
        return {"species": species, "sites": sites, "locations": locations,
                "skus": skus, "packageSizes": PACKAGE_SIZES, "customers": customers}

    # ---- dashboard -------------------------------------------------------- #
    def dashboard(self, conn):
        stab = conn.execute(
            "SELECT COUNT(*) totes, COALESCE(SUM(avg_weight_kg),0) kg "
            "FROM tote_lots WHERE status='in_stock'").fetchone()
        by_species = [{"species": r["species_code"], "totes": r["totes"],
                       "kg": round(r["kg"] or 0, 1)}
                      for r in conn.execute(
                          "SELECT species_code, COUNT(*) totes, COALESCE(SUM(avg_weight_kg),0) kg "
                          "FROM tote_lots WHERE status='in_stock' GROUP BY species_code ORDER BY kg DESC")]
        fg = [{"sku": r["sku_code"], "packageSize": r["package_size"],
               "qty": r["qty"], "litres": round(r["litres"] or 0, 1)}
              for r in conn.execute(
                  "SELECT sku_code, package_size, SUM(qty) qty, SUM(qty*litres_each) litres "
                  "FROM fg_lots WHERE status NOT IN ('sold','disposed') GROUP BY sku_code, package_size "
                  "ORDER BY sku_code, litres DESC")]
        fg_litres = conn.execute(
            "SELECT COALESCE(SUM(qty*litres_each),0) l FROM fg_lots WHERE status NOT IN ('sold','disposed')").fetchone()["l"]
        consum = [dict(id=r["id"], name=r["name"], unit=r["unit"], onHand=r["on_hand"],
                       reorderLevel=r["reorder_level"], low=(r["on_hand"] <= r["reorder_level"]))
                  for r in conn.execute("SELECT * FROM consumables ORDER BY name")]
        runs = [run_public(r) for r in conn.execute(
            "SELECT * FROM production_runs ORDER BY run_date DESC, id DESC LIMIT 5")]
        return {
            "stabilized": {"totes": stab["totes"], "kg": round(stab["kg"] or 0, 1),
                           "bySpecies": by_species},
            "finishedGoods": {"litres": round(fg_litres or 0, 1), "lines": fg},
            "consumables": consum,
            "lowStock": [c for c in consum if c["low"]],
            "recentRuns": runs,
        }

    # ---- stabilized totes ------------------------------------------------- #
    def route_totes(self, method, seg, query, conn):
        if seg == ["api", "totes"] and method == "GET":
            status = query.get("status", [""])[0]
            sql = ("SELECT * FROM tote_lots WHERE 1=1"
                   + (" AND status=?" if status else "")
                   + " ORDER BY checkin_date DESC, lot_number")
            rows = conn.execute(sql, (status,) if status else ()).fetchall()
            return {"totes": [tote_public(r) for r in rows]}
        if seg == ["api", "totes", "move-bulk"] and method == "POST":
            d = self._body_json()
            ids = [int(x) for x in (d.get("ids") or [])]
            to = self._ensure_location(conn, d.get("toLocation"))
            if not ids:
                raise ApiError(400, "Select at least one tote to move")
            if not to:
                raise ApiError(400, "A destination location is required")
            date = (d.get("date") or today_iso()).strip()
            note = d.get("note")
            moved = 0
            for tid in ids:
                it = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()
                if not it or it["status"] != "in_stock" or it["location"] == to:
                    continue
                self._log_move(conn, "tote", tid, it["lot_number"], it["location"], to, None, date, note)
                conn.execute("UPDATE tote_lots SET location=? WHERE id=?", (to, tid))
                moved += 1
            return {"moved": moved, "toLocation": to}
        if len(seg) >= 3 and seg[2].isdigit():
            tid = int(seg[2])
            it = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()
            if not it:
                raise ApiError(404, "Tote not found")

            # /api/totes/:id/move  — relocate a tote / read its move history
            if len(seg) == 4 and seg[3] == "move":
                if method == "GET":
                    return {"moveLog": self._move_log(conn, "tote", tid), "location": it["location"]}
                if method == "POST":
                    d = self._body_json()
                    to = self._ensure_location(conn, d.get("toLocation"))
                    if not to:
                        raise ApiError(400, "A destination location is required")
                    date = (d.get("date") or today_iso()).strip()
                    if to != it["location"]:
                        self._log_move(conn, "tote", tid, it["lot_number"], it["location"],
                                       to, None, date, d.get("note"))
                        conn.execute("UPDATE tote_lots SET location=? WHERE id=?", (to, tid))
                    return {"tote": tote_public(conn.execute(
                        "SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()),
                        "moveLog": self._move_log(conn, "tote", tid)}
                raise ApiError(405, "Method not allowed")

            # /api/totes/:id/ph  — log a new pH reading / read the history
            if len(seg) == 4 and seg[3] == "ph":
                if method == "GET":
                    return {"phLog": self._ph_log(conn, tid),
                            "ph": it["ph"], "phUpdated": it["ph_updated"]}
                if method == "POST":
                    d = self._body_json()
                    ph = numn(d.get("ph"))
                    if ph is None:
                        raise ApiError(400, "A pH value is required")
                    date = (d.get("date") or today_iso()).strip()
                    conn.execute(
                        "INSERT INTO tote_ph_log (tote_lot_id,ph,reading_date,note,created_at)"
                        " VALUES (?,?,?,?,?)", (tid, ph, date, d.get("note"), now_iso()))
                    conn.execute("UPDATE tote_lots SET ph=?, ph_updated=? WHERE id=?", (ph, date, tid))
                    return {"tote": tote_public(conn.execute(
                        "SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()),
                        "phLog": self._ph_log(conn, tid)}
                raise ApiError(405, "Method not allowed")

            if len(seg) == 3 and method == "PUT":
                d = self._body_json()
                # A pH change here is also logged, with today's date.
                if "ph" in d and numn(d["ph"]) is not None and numn(d["ph"]) != it["ph"]:
                    conn.execute(
                        "INSERT INTO tote_ph_log (tote_lot_id,ph,reading_date,note,created_at)"
                        " VALUES (?,?,?,?,?)", (tid, numn(d["ph"]), today_iso(), None, now_iso()))
                ph_updated = today_iso() if ("ph" in d and numn(d["ph"]) != it["ph"]) else it["ph_updated"]
                conn.execute(
                    "UPDATE tote_lots SET ph=?, ph_updated=?, avg_weight_kg=?, location=?, status=? WHERE id=?",
                    (numn(d["ph"]) if "ph" in d else it["ph"], ph_updated,
                     numn(d["avgWeightKg"]) if "avgWeightKg" in d else it["avg_weight_kg"],
                     d["location"] if "location" in d else it["location"],
                     d["status"] if "status" in d else it["status"], tid))
                return {"tote": tote_public(conn.execute(
                    "SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone())}
            if len(seg) == 3 and method == "DELETE":
                if it["status"] == "consumed":
                    raise ApiError(400, "Cannot delete a tote already consumed by a run")
                conn.execute("DELETE FROM tote_lots WHERE id=?", (tid,))
                return {"ok": True}
        raise ApiError(404, "Unknown totes endpoint")

    def _ph_log(self, conn, tote_id):
        return [dict(ph=r["ph"], date=r["reading_date"], note=r["note"], at=r["created_at"])
                for r in conn.execute(
                    "SELECT * FROM tote_ph_log WHERE tote_lot_id=? ORDER BY reading_date DESC, id DESC",
                    (tote_id,))]

    # ---- locations & moves ------------------------------------------------ #
    def _ensure_location(self, conn, name):
        name = (name or "").strip()
        if name:
            conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (name,))
        return name or None

    def _log_move(self, conn, etype, eid, lot, frm, to, qty, date, note):
        conn.execute(
            "INSERT INTO location_moves (entity_type,entity_id,lot,from_location,to_location,"
            "qty,moved_date,note,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (etype, eid, lot, frm, to, qty, date, note, now_iso()))

    def _move_log(self, conn, etype, eid):
        return [{"from": r["from_location"], "to": r["to_location"], "qty": r["qty"],
                 "date": r["moved_date"], "note": r["note"], "at": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM location_moves WHERE entity_type=? AND entity_id=? "
                    "ORDER BY moved_date DESC, id DESC", (etype, eid))]

    def _unique_fg_lot(self, conn, base):
        cand, n = base, 1
        while conn.execute("SELECT 1 FROM fg_lots WHERE fg_lot_number=?", (cand,)).fetchone():
            n += 1
            cand = "%s#%d" % (base, n)
        return cand

    # ---- harvest check-in (creates a batch of totes) ---------------------- #
    def route_harvest(self, method, seg, conn):
        if seg == ["api", "harvest"] and method == "POST":
            d = self._body_json()
            site = (d.get("site") or "").strip().upper()
            species = (d.get("species") or "").strip().upper()
            date = (d.get("checkinDate") or today_iso()).strip()
            count = int(num(d.get("toteCount")))
            total_kg = num(d.get("totalKg"))
            ph = numn(d.get("ph"))
            location = (d.get("location") or "").strip() or None
            if not site or not species or count <= 0:
                raise ApiError(400, "Site, species and a tote count > 0 are required")
            # IBC totes consumed for this harvest come from a chosen source (empty
            # IBC stock). Validate availability before creating any lots.
            ibc_id = d.get("ibcConsumableId")
            ibc_row = None
            if ibc_id:
                ibc_row = conn.execute("SELECT * FROM consumables WHERE id=?", (ibc_id,)).fetchone()
                if not ibc_row:
                    raise ApiError(400, "Unknown IBC tote source")
                if ibc_row["on_hand"] < count:
                    raise ApiError(400, "Not enough %s on hand (%g < %d)"
                                   % (ibc_row["name"], ibc_row["on_hand"], count))
            if not conn.execute("SELECT 1 FROM sites WHERE code=?", (site,)).fetchone():
                conn.execute("INSERT INTO sites (code,name) VALUES (?,?)", (site, site))
            if not conn.execute("SELECT 1 FROM species WHERE code=?", (species,)).fetchone():
                raise ApiError(400, "Unknown species code: %s" % species)
            if location:
                conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (location,))
            avg = round(total_kg / count, 2) if count else 0
            datestr = date.replace("-", "")
            sp = conn.execute("SELECT common FROM species WHERE code=?", (species,)).fetchone()
            common = sp["common"] if sp else species
            # continue tote numbering after any existing totes for this batch key
            existing = conn.execute(
                "SELECT COALESCE(MAX(tote_number),0) n FROM tote_lots "
                "WHERE site_code=? AND species_code=? AND checkin_date=?",
                (site, species, date)).fetchone()["n"]
            ts = now_iso()
            created = []
            for i in range(1, count + 1):
                n = existing + i
                lot = "%s-%s-%s-%03d" % (site, species, datestr, n)
                conn.execute(
                    "INSERT INTO tote_lots (lot_number,site_code,species_code,harvest_year,"
                    "checkin_date,tote_number,volume_l,ph,avg_weight_kg,location,description,"
                    "status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'in_stock', ?)",
                    (lot, site, species, int(date[:4]), date, n, 1000, ph, avg, location,
                     "Fresh Stabilized Ground %s" % common, ts))
                created.append(lot)
            if ibc_row:
                self._consume(conn, ibc_row["id"], -count, "Harvest check-in (IBC fill)",
                              "%s-%s-%s" % (site, species, datestr))
            return {"created": created, "avgWeightKg": avg, "count": len(created),
                    "ibcSource": ibc_row["name"] if ibc_row else None}
        raise ApiError(404, "Unknown harvest endpoint")

    # ---- consumables ------------------------------------------------------ #
    def route_consumables(self, method, seg, conn):
        if seg == ["api", "consumables"]:
            if method == "GET":
                rows = conn.execute("SELECT * FROM consumables ORDER BY name").fetchall()
                return {"consumables": [
                    dict(id=r["id"], name=r["name"], unit=r["unit"], onHand=r["on_hand"],
                         reorderLevel=r["reorder_level"], costPerUnit=r["cost_per_unit"],
                         location=r["location"], low=(r["on_hand"] <= r["reorder_level"]))
                    for r in rows]}
            if method == "POST":
                d = self._body_json()
                name = (d.get("name") or "").strip()
                if not name:
                    raise ApiError(400, "Name is required")
                if conn.execute("SELECT 1 FROM consumables WHERE name=?", (name,)).fetchone():
                    raise ApiError(409, "That consumable already exists")
                location = self._ensure_location(conn, d.get("location"))
                conn.execute("INSERT INTO consumables (name,unit,on_hand,reorder_level,cost_per_unit,location)"
                             " VALUES (?,?,?,?,?,?)",
                             (name, (d.get("unit") or "unit").strip(), num(d.get("onHand")),
                              num(d.get("reorderLevel")), numn(d.get("costPerUnit")), location))
                return {"ok": True}
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "adjust" and method == "POST":
            cid = int(seg[2])
            c = conn.execute("SELECT * FROM consumables WHERE id=?", (cid,)).fetchone()
            if not c:
                raise ApiError(404, "Consumable not found")
            d = self._body_json()
            delta = num(d.get("delta"))
            if delta == 0:
                raise ApiError(400, "Adjustment delta must be non-zero")
            self._consume(conn, cid, delta, d.get("reason") or "Manual adjustment", d.get("ref"))
            return {"onHand": conn.execute(
                "SELECT on_hand FROM consumables WHERE id=?", (cid,)).fetchone()["on_hand"]}
        if len(seg) == 3 and seg[2].isdigit() and method == "PUT":
            cid = int(seg[2])
            c = conn.execute("SELECT * FROM consumables WHERE id=?", (cid,)).fetchone()
            if not c:
                raise ApiError(404, "Consumable not found")
            d = self._body_json()
            location = self._ensure_location(conn, d["location"]) if "location" in d else c["location"]
            conn.execute("UPDATE consumables SET reorder_level=?, cost_per_unit=?, location=? WHERE id=?",
                         (num(d["reorderLevel"]) if "reorderLevel" in d else c["reorder_level"],
                          numn(d["costPerUnit"]) if "costPerUnit" in d else c["cost_per_unit"],
                          location, cid))
            return {"ok": True}
        raise ApiError(404, "Unknown consumables endpoint")

    def _consume(self, conn, consumable_id, delta, reason, ref):
        conn.execute("UPDATE consumables SET on_hand = on_hand + ? WHERE id=?", (delta, consumable_id))
        conn.execute("INSERT INTO consumable_txns (consumable_id,delta,reason,ref,created_at)"
                     " VALUES (?,?,?,?,?)", (consumable_id, delta, reason, ref, now_iso()))

    def _consumable_by_name(self, conn, name):
        return conn.execute("SELECT * FROM consumables WHERE name=?", (name,)).fetchone()

    # ---- production ------------------------------------------------------- #
    # Fields a run edit may touch: (db column, json key, label, kind)
    RUN_EDIT_FIELDS = [
        ("run_date", "runDate", "Run date", "text"),
        ("target_tds", "targetTds", "Target TDS", "num"),
        ("citric_kg", "citricKg", "Citric acid (kg)", "num"),
        ("sorbate_kg", "sorbateKg", "Potassium sorbate (kg)", "num"),
        ("location", "location", "Location", "text"),
        ("notes", "notes", "Notes", "text"),
    ]

    def route_production(self, method, seg, conn, user):
        if seg == ["api", "production"] and method == "GET":
            runs = []
            for r in conn.execute("SELECT * FROM production_runs ORDER BY run_date DESC, id DESC"):
                d = run_public(r)
                d["inputTotes"] = [row["lot_number"] for row in conn.execute(
                    "SELECT t.lot_number FROM run_inputs ri JOIN tote_lots t ON t.id=ri.tote_lot_id "
                    "WHERE ri.run_id=? ORDER BY t.lot_number", (r["id"],))]
                d["fgLots"] = [fg_public(row) for row in conn.execute(
                    "SELECT * FROM fg_lots WHERE run_id=? ORDER BY package_size", (r["id"],))]
                d["edits"] = self._run_edits(conn, r["id"])
                d["attachments"] = self._attachments(conn, r["id"])
                runs.append(d)
            return {"runs": runs}
        if seg == ["api", "production"] and method == "POST":
            return self.create_run(conn)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "edits" and method == "GET":
            return {"edits": self._run_edits(conn, int(seg[2]))}
        if len(seg) == 3 and seg[2].isdigit() and method == "PUT":
            return self.edit_run(conn, int(seg[2]), user)
        if len(seg) >= 4 and seg[2].isdigit() and seg[3] == "attachments":
            rid = int(seg[2])
            if not conn.execute("SELECT 1 FROM production_runs WHERE id=?", (rid,)).fetchone():
                raise ApiError(404, "Production run not found")
            if len(seg) == 4 and method == "GET":
                return {"attachments": self._attachments(conn, rid)}
            if len(seg) == 4 and method == "POST":
                return self.add_attachment(conn, rid, user)
            if len(seg) == 5 and seg[4].isdigit() and method == "DELETE":
                return self.delete_attachment(conn, rid, int(seg[4]))
        raise ApiError(404, "Unknown production endpoint")

    def _attachments(self, conn, run_id):
        return [{"id": r["id"], "filename": r["filename"], "contentType": r["content_type"],
                 "size": r["size"], "uploadedBy": r["uploaded_by"], "uploadedAt": r["uploaded_at"]}
                for r in conn.execute(
                    "SELECT * FROM run_attachments WHERE run_id=? ORDER BY uploaded_at DESC, id DESC",
                    (run_id,))]

    def add_attachment(self, conn, run_id, user):
        d = self._body_json()
        filename = (d.get("filename") or "document").strip().replace("\\", "/").split("/")[-1] or "document"
        data_b64 = d.get("dataB64") or ""
        if data_b64.startswith("data:") and "," in data_b64:
            data_b64 = data_b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(data_b64)
        except Exception:
            raise ApiError(400, "Could not decode file data")
        if not raw:
            raise ApiError(400, "The file is empty")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ApiError(400, "File exceeds the %d MB limit" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(filename)[1][:12]
        stored = secrets.token_hex(8) + ext
        with open(os.path.join(UPLOAD_DIR, stored), "wb") as f:
            f.write(raw)
        conn.execute(
            "INSERT INTO run_attachments (run_id,filename,content_type,size,stored_name,"
            "uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, filename, d.get("contentType") or "application/octet-stream", len(raw),
             stored, user["name"] if user else None, now_iso()))
        return {"attachments": self._attachments(conn, run_id)}

    def delete_attachment(self, conn, run_id, aid):
        r = conn.execute("SELECT * FROM run_attachments WHERE id=? AND run_id=?",
                         (aid, run_id)).fetchone()
        if not r:
            raise ApiError(404, "Attachment not found")
        try:
            os.remove(os.path.join(UPLOAD_DIR, r["stored_name"]))
        except OSError:
            pass
        conn.execute("DELETE FROM run_attachments WHERE id=?", (aid,))
        return {"attachments": self._attachments(conn, run_id)}

    def _run_edits(self, conn, run_id):
        return [{"user": r["user_name"], "field": r["field"], "old": r["old_value"],
                 "new": r["new_value"], "at": r["edited_at"]}
                for r in conn.execute(
                    "SELECT * FROM run_edits WHERE run_id=? ORDER BY edited_at DESC, id DESC",
                    (run_id,))]

    def edit_run(self, conn, rid, user):
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not run:
            raise ApiError(404, "Production run not found")
        d = self._body_json()
        updates, changes = {}, []
        for col, key, label, kind in self.RUN_EDIT_FIELDS:
            if key not in d:
                continue
            old = run[col]
            new = numn(d[key]) if kind == "num" else ((d[key] or "").strip() or None)
            if col == "run_date" and not new:
                continue  # never blank out the run date
            if kind == "num":
                same = (old is None and new is None) or \
                       (old is not None and new is not None and float(old) == float(new))
            else:
                same = (old or "") == (new or "")
            if same:
                continue
            updates[col] = new
            changes.append((label, old, new))
        if not updates:
            return {"run": run_public(run), "edits": self._run_edits(conn, rid), "changed": 0}

        # Keep consumable stock consistent when preservative amounts are corrected.
        if "citric_kg" in updates:
            delta = (updates["citric_kg"] or 0) - (run["citric_kg"] or 0)
            row = self._consumable_by_name(conn, "Citric Acid")
            if delta and row:
                self._consume(conn, row["id"], -delta, "Production run edit", run["processing_lot"])
        if "sorbate_kg" in updates:
            delta = (updates["sorbate_kg"] or 0) - (run["sorbate_kg"] or 0)
            row = self._consumable_by_name(conn, "Potassium Sorbate")
            if delta and row:
                self._consume(conn, row["id"], -delta, "Production run edit", run["processing_lot"])

        sets = ", ".join("%s=?" % c for c in updates)
        conn.execute("UPDATE production_runs SET %s WHERE id=?" % sets,
                     (*updates.values(), rid))
        ts = now_iso()
        uname = user["name"] if user else "?"
        for label, old, new in changes:
            conn.execute(
                "INSERT INTO run_edits (run_id,user_name,field,old_value,new_value,edited_at)"
                " VALUES (?,?,?,?,?,?)", (rid, uname, label, _fmtval(old), _fmtval(new), ts))
        return {"run": run_public(conn.execute(
            "SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()),
            "edits": self._run_edits(conn, rid), "changed": len(changes)}

    def create_run(self, conn):
        d = self._body_json()
        tote_ids = [int(x) for x in (d.get("toteIds") or [])]
        if not tote_ids:
            raise ApiError(400, "Select at least one stabilized tote to process")
        sku = (d.get("sku") or "").strip()
        sku_row = conn.execute("SELECT * FROM fg_skus WHERE code=?", (sku,)).fetchone()
        if not sku_row:
            raise ApiError(400, "Choose a finished-good SKU")
        species = sku_row["species_code"]
        packages = d.get("packages") or []   # [{size, qty}]
        target_tds = numn(d.get("targetTds"))
        citric = num(d.get("citricKg"))
        sorbate = num(d.get("sorbateKg"))
        location = (d.get("location") or "").strip() or None
        run_date = (d.get("runDate") or today_iso()).strip()
        notes = d.get("notes")

        # Validate totes are all in stock.
        rows = conn.execute(
            "SELECT * FROM tote_lots WHERE id IN (%s)" % ",".join("?" * len(tote_ids)),
            tote_ids).fetchall()
        if len(rows) != len(tote_ids):
            raise ApiError(400, "Some selected totes were not found")
        for r in rows:
            if r["status"] != "in_stock":
                raise ApiError(400, "Tote %s is not in stock" % r["lot_number"])
        input_kg = round(sum((r["avg_weight_kg"] or 0) for r in rows), 2)

        # Output litres = sum of packaged litres.
        output_litres = 0.0
        ibc_used = 0
        for p in packages:
            size = p.get("size")
            qty = num(p.get("qty"))
            if size not in PACKAGE_SIZES or qty <= 0:
                continue
            output_litres += PACKAGE_SIZES[size] * qty
            if size == "IBC":
                ibc_used += int(qty)
        output_litres = round(output_litres, 2)

        # Check consumable availability (citric, sorbate, empty IBCs).
        citric_row = self._consumable_by_name(conn, "Citric Acid")
        sorbate_row = self._consumable_by_name(conn, "Potassium Sorbate")
        if citric and citric_row and citric_row["on_hand"] < citric:
            raise ApiError(400, "Not enough Citric Acid on hand (%.1f < %.1f)"
                           % (citric_row["on_hand"], citric))
        if sorbate and sorbate_row and sorbate_row["on_hand"] < sorbate:
            raise ApiError(400, "Not enough Potassium Sorbate on hand (%.1f < %.1f)"
                           % (sorbate_row["on_hand"], sorbate))
        ibc_row = self._consumable_by_name(conn, "Empty New IBC Tote")
        if ibc_used and ibc_row and ibc_row["on_hand"] < ibc_used:
            raise ApiError(400, "Not enough empty IBC totes on hand (%d < %d)"
                           % (int(ibc_row["on_hand"]), ibc_used))

        # Processing lot number: PR-YYYYMMDD-NNN (NNN = next overall sequence).
        lot = (d.get("processingLot") or "").strip()
        if not lot:
            seq = conn.execute("SELECT COUNT(*) c FROM production_runs").fetchone()["c"] + 1
            lot = "PR-%s-%03d" % (run_date.replace("-", ""), seq)
        if conn.execute("SELECT 1 FROM production_runs WHERE processing_lot=?", (lot,)).fetchone():
            raise ApiError(409, "Processing lot %s already exists" % lot)

        ts = now_iso()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO production_runs (processing_lot,run_date,species_code,sku_code,input_kg,"
            "target_tds,output_litres,citric_kg,sorbate_kg,ibc_used,location,notes,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lot, run_date, species, sku, input_kg, target_tds, output_litres,
             citric, sorbate, ibc_used, location, notes, ts))
        run_id = cur.lastrowid

        # Consume totes.
        for r in rows:
            cur.execute("UPDATE tote_lots SET status='consumed', run_id=? WHERE id=?", (run_id, r["id"]))
            cur.execute("INSERT INTO run_inputs (run_id,tote_lot_id) VALUES (?,?)", (run_id, r["id"]))

        # Deduct consumables.
        if citric and citric_row:
            self._consume(conn, citric_row["id"], -citric, "Production run", lot)
        if sorbate and sorbate_row:
            self._consume(conn, sorbate_row["id"], -sorbate, "Production run", lot)
        # Finished goods go into NEW clean IBCs (consume from the new-IBC pool).
        if ibc_used and ibc_row:
            self._consume(conn, ibc_row["id"], -ibc_used, "Production run (FG into new IBCs)", lot)
        # The IBC totes the stabilized kelp was stored in are now emptied by
        # processing and return to the USED-IBC pool (one per tote processed).
        used_row = self._consumable_by_name(conn, "Empty Used IBC Tote")
        if used_row and rows:
            self._consume(conn, used_row["id"], len(rows), "Emptied by processing", lot)

        # Create FG lots, one per package size.
        fg_created = []
        for p in packages:
            size = p.get("size")
            qty = num(p.get("qty"))
            if size not in PACKAGE_SIZES or qty <= 0:
                continue
            fg_lot = "%s-%s" % (lot, size)
            cur.execute(
                "INSERT INTO fg_lots (fg_lot_number,sku_code,run_id,package_size,qty,litres_each,"
                "produced_date,tds,location,status,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?, 'on_hand', ?)",
                (fg_lot, sku, run_id, size, qty, PACKAGE_SIZES[size], run_date,
                 target_tds, location, ts))
            fg_created.append(fg_lot)

        return {"processingLot": lot, "runId": run_id, "inputKg": input_kg,
                "outputLitres": output_litres, "fgLots": fg_created}

    # ---- finished goods --------------------------------------------------- #
    def route_fg(self, method, seg, query, conn):
        if seg == ["api", "fg"] and method == "GET":
            status = query.get("status", [""])[0]
            sql = "SELECT * FROM fg_lots" + (" WHERE status=?" if status else "")
            sql += " ORDER BY produced_date DESC, fg_lot_number"
            rows = conn.execute(sql, (status,) if status else ()).fetchall()
            return {"fg": [fg_public(r) for r in rows]}
        if seg == ["api", "fg", "move-bulk"] and method == "POST":
            d = self._body_json()
            ids = [int(x) for x in (d.get("ids") or [])]
            to = self._ensure_location(conn, d.get("toLocation"))
            if not ids:
                raise ApiError(400, "Select at least one finished-goods lot to move")
            if not to:
                raise ApiError(400, "A destination location is required")
            date = (d.get("date") or today_iso()).strip()
            note = d.get("note")
            moved = 0
            for fid in ids:
                it = conn.execute("SELECT * FROM fg_lots WHERE id=?", (fid,)).fetchone()
                if not it or it["status"] == "sold" or it["location"] == to:
                    continue
                conn.execute("UPDATE fg_lots SET location=? WHERE id=?", (to, fid))
                self._log_move(conn, "fg", fid, it["fg_lot_number"], it["location"], to, it["qty"], date, note)
                moved += 1
            return {"moved": moved, "toLocation": to}
        if len(seg) >= 3 and seg[2].isdigit():
            fid = int(seg[2])
            it = conn.execute("SELECT * FROM fg_lots WHERE id=?", (fid,)).fetchone()
            if not it:
                raise ApiError(404, "FG lot not found")

            # /api/fg/:id/move — relocate units (whole lot or a partial split)
            if len(seg) == 4 and seg[3] == "move":
                if method == "GET":
                    return {"moveLog": self._move_log(conn, "fg", fid), "location": it["location"]}
                if method == "POST":
                    d = self._body_json()
                    to = self._ensure_location(conn, d.get("toLocation"))
                    if not to:
                        raise ApiError(400, "A destination location is required")
                    qty = num(d.get("qty"), it["qty"])
                    if qty <= 0 or qty > it["qty"]:
                        raise ApiError(400, "Move quantity must be between 0 and the %g units on hand" % it["qty"])
                    date = (d.get("date") or today_iso()).strip()
                    note = d.get("note")
                    if to == it["location"]:
                        return {"fg": fg_public(it), "moveLog": self._move_log(conn, "fg", fid)}
                    if qty >= it["qty"]:
                        conn.execute("UPDATE fg_lots SET location=? WHERE id=?", (to, fid))
                        self._log_move(conn, "fg", fid, it["fg_lot_number"], it["location"], to, qty, date, note)
                    else:
                        # split: reduce source, merge into a destination twin or create one
                        conn.execute("UPDATE fg_lots SET qty=qty-? WHERE id=?", (qty, fid))
                        twin = conn.execute(
                            "SELECT * FROM fg_lots WHERE id!=? AND run_id IS ? AND sku_code IS ? "
                            "AND package_size=? AND location IS ? AND status=?",
                            (fid, it["run_id"], it["sku_code"], it["package_size"], to, it["status"])).fetchone()
                        if twin:
                            conn.execute("UPDATE fg_lots SET qty=qty+? WHERE id=?", (qty, twin["id"]))
                            dest_id = twin["id"]
                        else:
                            base = "%s-%s" % (it["fg_lot_number"], "".join(c for c in to if c.isalnum())[:6].upper() or "MOV")
                            new_lot = self._unique_fg_lot(conn, base)
                            cur = conn.cursor()
                            cur.execute(
                                "INSERT INTO fg_lots (fg_lot_number,sku_code,run_id,package_size,qty,"
                                "litres_each,produced_date,tds,location,status,created_at)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (new_lot, it["sku_code"], it["run_id"], it["package_size"], qty,
                                 it["litres_each"], it["produced_date"], it["tds"], to, it["status"], now_iso()))
                            dest_id = cur.lastrowid
                        self._log_move(conn, "fg", fid, it["fg_lot_number"], it["location"], to, qty, date, note)
                        self._log_move(conn, "fg", dest_id, it["fg_lot_number"], it["location"], to, qty, date, note)
                    return {"fg": [fg_public(r) for r in conn.execute(
                        "SELECT * FROM fg_lots WHERE id=?", (fid,))],
                        "moveLog": self._move_log(conn, "fg", fid)}
                raise ApiError(405, "Method not allowed")

            if len(seg) == 3 and method == "PUT":
                d = self._body_json()
                self._ensure_location(conn, d.get("location"))
                conn.execute(
                    "UPDATE fg_lots SET qty=?, status=?, location=?, tds=? WHERE id=?",
                    (num(d["qty"]) if "qty" in d else it["qty"],
                     d["status"] if "status" in d else it["status"],
                     d["location"] if "location" in d else it["location"],
                     numn(d["tds"]) if "tds" in d else it["tds"], fid))
                return {"fg": fg_public(conn.execute(
                    "SELECT * FROM fg_lots WHERE id=?", (fid,)).fetchone())}
        raise ApiError(404, "Unknown fg endpoint")

    # ---- customers -------------------------------------------------------- #
    def _customer_public(self, r):
        return {"id": r["id"], "name": r["name"], "contact": r["contact"], "email": r["email"],
                "phone": r["phone"], "address": r["address"]}

    def route_customers(self, method, seg, conn):
        if seg == ["api", "customers"]:
            if method == "GET":
                return {"customers": [self._customer_public(r) for r in conn.execute(
                    "SELECT * FROM customers WHERE active=1 ORDER BY name")]}
            if method == "POST":
                d = self._body_json()
                name = (d.get("name") or "").strip()
                if not name:
                    raise ApiError(400, "Customer name is required")
                if conn.execute("SELECT 1 FROM customers WHERE name=?", (name,)).fetchone():
                    raise ApiError(409, "A customer with that name already exists")
                conn.execute(
                    "INSERT INTO customers (name,contact,email,phone,address,created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (name, d.get("contact"), d.get("email"), d.get("phone"), d.get("address"), now_iso()))
                return {"customers": [self._customer_public(r) for r in conn.execute(
                    "SELECT * FROM customers WHERE active=1 ORDER BY name")]}
        if len(seg) == 3 and seg[2].isdigit() and method == "PUT":
            cid = int(seg[2])
            c = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
            if not c:
                raise ApiError(404, "Customer not found")
            d = self._body_json()
            conn.execute(
                "UPDATE customers SET name=?, contact=?, email=?, phone=?, address=? WHERE id=?",
                ((d["name"].strip() if d.get("name") else c["name"]),
                 d.get("contact") if "contact" in d else c["contact"],
                 d.get("email") if "email" in d else c["email"],
                 d.get("phone") if "phone" in d else c["phone"],
                 d.get("address") if "address" in d else c["address"], cid))
            return {"ok": True}
        raise ApiError(404, "Unknown customers endpoint")

    # ---- shipments -------------------------------------------------------- #
    def _shipment_trace(self, conn, ship_id):
        """Lines with their full provenance: FG lot -> run -> source totes."""
        lines = []
        for ln in conn.execute("SELECT * FROM shipment_lines WHERE shipment_id=? ORDER BY id",
                               (ship_id,)):
            run_lot, run_date, totes = None, None, []
            fg = conn.execute("SELECT * FROM fg_lots WHERE id=?", (ln["fg_lot_id"],)).fetchone()
            if fg and fg["run_id"]:
                run = conn.execute("SELECT * FROM production_runs WHERE id=?", (fg["run_id"],)).fetchone()
                if run:
                    run_lot, run_date = run["processing_lot"], run["run_date"]
                    totes = [r["lot_number"] for r in conn.execute(
                        "SELECT t.lot_number FROM run_inputs ri JOIN tote_lots t ON t.id=ri.tote_lot_id "
                        "WHERE ri.run_id=? ORDER BY t.lot_number", (run["id"],))]
            lines.append({"id": ln["id"], "lot": ln["fg_lot_number"], "sku": ln["sku_code"],
                          "packageSize": ln["package_size"], "qty": ln["qty"],
                          "litresEach": ln["litres_each"],
                          "litres": round((ln["qty"] or 0) * (ln["litres_each"] or 0), 2),
                          "processingLot": run_lot, "runDate": run_date, "inputTotes": totes})
        return lines

    def _shipment_public(self, conn, r, with_lines=False):
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (r["customer_id"],)).fetchone()
        agg = conn.execute(
            "SELECT COUNT(*) lines, COALESCE(SUM(qty),0) units, "
            "COALESCE(SUM(qty*litres_each),0) litres FROM shipment_lines WHERE shipment_id=?",
            (r["id"],)).fetchone()
        out = {"id": r["id"], "shipmentNo": r["shipment_no"], "customerId": r["customer_id"],
               "customer": cust["name"] if cust else None,
               "shipDate": r["ship_date"], "status": r["status"], "carrier": r["carrier"],
               "trackingNo": r["tracking_no"], "reference": r["reference"], "shipTo": r["ship_to"],
               "notes": r["notes"], "createdBy": r["created_by"],
               "lineCount": agg["lines"], "units": agg["units"],
               "litres": round(agg["litres"] or 0, 1)}
        if with_lines:
            out["lines"] = self._shipment_trace(conn, r["id"])
        return out

    def route_shipments(self, method, seg, query, conn, user):
        if seg == ["api", "shipments"]:
            if method == "GET":
                cust = query.get("customer", [""])[0]
                sql = "SELECT * FROM shipments"
                args = ()
                if cust:
                    sql += " WHERE customer_id=?"
                    args = (int(cust),)
                sql += " ORDER BY ship_date DESC, id DESC"
                return {"shipments": [self._shipment_public(conn, r) for r in conn.execute(sql, args)]}
            if method == "POST":
                return self.create_shipment(conn, user)
        if len(seg) == 3 and seg[2].isdigit():
            sid = int(seg[2])
            r = conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
            if not r:
                raise ApiError(404, "Shipment not found")
            if method == "GET":
                return {"shipment": self._shipment_public(conn, r, with_lines=True)}
            if method == "PUT":
                return self.update_shipment(conn, r)
        raise ApiError(404, "Unknown shipments endpoint")

    def create_shipment(self, conn, user):
        d = self._body_json()
        cust = conn.execute("SELECT * FROM customers WHERE id=?",
                            (d.get("customerId"),)).fetchone()
        if not cust:
            raise ApiError(400, "Choose a customer")
        raw_lines = d.get("lines") or []
        ship_date = (d.get("shipDate") or today_iso()).strip()
        # Validate every line against on-hand stock before committing anything.
        prepared = []
        for ln in raw_lines:
            fg = conn.execute("SELECT * FROM fg_lots WHERE id=?", (ln.get("fgLotId"),)).fetchone()
            qty = num(ln.get("qty"))
            if not fg or qty <= 0:
                continue
            if qty > fg["qty"]:
                raise ApiError(400, "Only %g of %s on hand (asked %g)"
                               % (fg["qty"], fg["fg_lot_number"], qty))
            prepared.append((fg, qty))
        if not prepared:
            raise ApiError(400, "Add at least one finished-goods line to ship")

        seq = conn.execute("SELECT COUNT(*) c FROM shipments").fetchone()["c"] + 1
        ship_no = (d.get("shipmentNo") or "").strip() or "SH-%s-%03d" % (ship_date.replace("-", ""), seq)
        if conn.execute("SELECT 1 FROM shipments WHERE shipment_no=?", (ship_no,)).fetchone():
            raise ApiError(409, "Shipment %s already exists" % ship_no)
        ship_to = (d.get("shipTo") or cust["address"] or "")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO shipments (shipment_no,customer_id,ship_date,status,carrier,tracking_no,"
            "reference,ship_to,notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ship_no, cust["id"], ship_date, "shipped", d.get("carrier"), d.get("trackingNo"),
             d.get("reference"), ship_to, d.get("notes"), user["name"] if user else None, now_iso()))
        sid = cur.lastrowid
        for fg, qty in prepared:
            cur.execute(
                "INSERT INTO shipment_lines (shipment_id,fg_lot_id,fg_lot_number,sku_code,"
                "package_size,qty,litres_each) VALUES (?,?,?,?,?,?,?)",
                (sid, fg["id"], fg["fg_lot_number"], fg["sku_code"], fg["package_size"], qty,
                 fg["litres_each"]))
            new_qty = fg["qty"] - qty
            cur.execute("UPDATE fg_lots SET qty=?, status=? WHERE id=?",
                        (new_qty, "sold" if new_qty <= 0 else fg["status"], fg["id"]))
        r = conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        return {"shipment": self._shipment_public(conn, r, with_lines=True)}

    def update_shipment(self, conn, r):
        d = self._body_json()
        new_status = d.get("status", r["status"])
        old_status = r["status"]
        if new_status not in ("shipped", "delivered", "cancelled"):
            raise ApiError(400, "Invalid status")
        # Cancelling restocks the shipped units; un-cancelling re-deducts them.
        if new_status == "cancelled" and old_status != "cancelled":
            for ln in conn.execute("SELECT * FROM shipment_lines WHERE shipment_id=?", (r["id"],)):
                fg = conn.execute("SELECT * FROM fg_lots WHERE id=?", (ln["fg_lot_id"],)).fetchone()
                if fg:
                    nq = (fg["qty"] or 0) + (ln["qty"] or 0)
                    conn.execute("UPDATE fg_lots SET qty=?, status=? WHERE id=?",
                                 (nq, "on_hand" if fg["status"] == "sold" and nq > 0 else fg["status"], fg["id"]))
        elif old_status == "cancelled" and new_status != "cancelled":
            for ln in conn.execute("SELECT * FROM shipment_lines WHERE shipment_id=?", (r["id"],)):
                fg = conn.execute("SELECT * FROM fg_lots WHERE id=?", (ln["fg_lot_id"],)).fetchone()
                if fg:
                    nq = (fg["qty"] or 0) - (ln["qty"] or 0)
                    conn.execute("UPDATE fg_lots SET qty=?, status=? WHERE id=?",
                                 (nq, "sold" if nq <= 0 else fg["status"], fg["id"]))
        conn.execute(
            "UPDATE shipments SET status=?, carrier=?, tracking_no=?, reference=?, notes=? WHERE id=?",
            (new_status,
             d["carrier"] if "carrier" in d else r["carrier"],
             d["trackingNo"] if "trackingNo" in d else r["tracking_no"],
             d["reference"] if "reference" in d else r["reference"],
             d["notes"] if "notes" in d else r["notes"], r["id"]))
        return {"shipment": self._shipment_public(
            conn, conn.execute("SELECT * FROM shipments WHERE id=?", (r["id"],)).fetchone(),
            with_lines=True)}

    # ---- reports ---------------------------------------------------------- #
    # ---- transaction ledger (report drill-down) --------------------------- #
    def ledger(self, query, conn):
        dim = query.get("dim", [""])[0]
        key = query.get("key", [""])[0]
        frm = query.get("from", [""])[0]
        to = query.get("to", [""])[0]
        if not (frm and to):
            frm, to, _ = month_bounds(today_iso()[:7])
        if frm > to:
            frm, to = to, frm
        txns = []        # each: {date, description, change, balance}
        opening = 0.0
        unit = ""
        title = key

        if dim == "consumable":
            c = conn.execute("SELECT * FROM consumables WHERE name=? OR id=?",
                             (key, key if str(key).isdigit() else -1)).fetchone()
            if not c:
                raise ApiError(404, "Consumable not found")
            title, unit = c["name"], c["unit"]
            # balance at start of period = current on hand minus everything from `frm` onward
            after = conn.execute(
                "SELECT COALESCE(SUM(delta),0) s FROM consumable_txns WHERE consumable_id=? "
                "AND substr(created_at,1,10)>=?", (c["id"], frm)).fetchone()["s"]
            opening = round((c["on_hand"] or 0) - (after or 0), 2)
            rows = conn.execute(
                "SELECT * FROM consumable_txns WHERE consumable_id=? AND substr(created_at,1,10)>=? "
                "AND substr(created_at,1,10)<=? ORDER BY created_at, id", (c["id"], frm, to)).fetchall()
            bal = opening
            for r in rows:
                bal = round(bal + (r["delta"] or 0), 2)
                desc = r["reason"] or "Adjustment"
                if r["ref"]:
                    desc += " (" + r["ref"] + ")"
                txns.append({"date": (r["created_at"] or "")[:10], "description": desc,
                             "change": r["delta"], "balance": bal})

        elif dim == "species":
            sp = conn.execute("SELECT * FROM species WHERE code=?", (key,)).fetchone()
            title = (sp["common"] or sp["name"]) if sp else key
            unit = "kg"
            opening = round(conn.execute(
                "SELECT COALESCE(SUM(t.avg_weight_kg),0) k FROM tote_lots t "
                "LEFT JOIN production_runs r ON r.id=t.run_id "
                "WHERE t.species_code=? AND t.checkin_date<? "
                "AND (t.run_id IS NULL OR r.run_date>=?) "
                "AND (t.disposed_date IS NULL OR t.disposed_date>=?)",
                (key, frm, frm, frm)).fetchone()["k"], 1)
            evs = []
            for r in conn.execute(
                    "SELECT checkin_date d, lot_number, avg_weight_kg FROM tote_lots "
                    "WHERE species_code=? AND checkin_date>=? AND checkin_date<=?",
                    (key, frm, to)):
                evs.append((r["d"], "Checked in " + r["lot_number"], r["avg_weight_kg"] or 0))
            for r in conn.execute(
                    "SELECT pr.run_date d, t.lot_number, pr.processing_lot, t.avg_weight_kg "
                    "FROM tote_lots t JOIN production_runs pr ON pr.id=t.run_id "
                    "WHERE t.species_code=? AND pr.run_date>=? AND pr.run_date<=?",
                    (key, frm, to)):
                evs.append((r["d"], "Consumed by " + r["processing_lot"] + " (" + r["lot_number"] + ")",
                            -(r["avg_weight_kg"] or 0)))
            for r in conn.execute(
                    "SELECT disposed_date d, lot_number, avg_weight_kg FROM tote_lots "
                    "WHERE species_code=? AND disposed_date>=? AND disposed_date<=?",
                    (key, frm, to)):
                evs.append((r["d"], "Disposed " + r["lot_number"], -(r["avg_weight_kg"] or 0)))
            evs.sort(key=lambda e: e[0] or "")
            bal = opening
            for d_, desc, chg in evs:
                bal = round(bal + chg, 1)
                txns.append({"date": d_, "description": desc, "change": round(chg, 1), "balance": bal})

        elif dim == "sku":
            sk = conn.execute("SELECT * FROM fg_skus WHERE code=?", (key,)).fetchone()
            title = sk["name"] if sk else key
            unit = "L"
            prod_b = conn.execute("SELECT COALESCE(SUM(output_litres),0) s FROM production_runs "
                                  "WHERE sku_code=? AND run_date<?", (key, frm)).fetchone()["s"]
            ship_b = conn.execute(
                "SELECT COALESCE(SUM(sl.qty*sl.litres_each),0) s FROM shipment_lines sl "
                "JOIN shipments s ON s.id=sl.shipment_id WHERE sl.sku_code=? AND s.status!='cancelled' "
                "AND s.ship_date<?", (key, frm)).fetchone()["s"]
            disp_b = conn.execute("SELECT COALESCE(SUM(litres),0) s FROM disposals "
                                  "WHERE entity_type='fg' AND sku_code=? AND disposed_date<?",
                                  (key, frm)).fetchone()["s"]
            opening = round((prod_b or 0) - (ship_b or 0) - (disp_b or 0), 1)
            evs = []
            for r in conn.execute("SELECT run_date d, processing_lot, output_litres FROM production_runs "
                                  "WHERE sku_code=? AND run_date>=? AND run_date<=?", (key, frm, to)):
                evs.append((r["d"], "Produced " + r["processing_lot"], r["output_litres"] or 0))
            for r in conn.execute(
                    "SELECT s.ship_date d, s.shipment_no, COALESCE(cu.name,'') cust, "
                    "SUM(sl.qty*sl.litres_each) litres FROM shipment_lines sl "
                    "JOIN shipments s ON s.id=sl.shipment_id LEFT JOIN customers cu ON cu.id=s.customer_id "
                    "WHERE sl.sku_code=? AND s.status!='cancelled' AND s.ship_date>=? AND s.ship_date<=? "
                    "GROUP BY s.id", (key, frm, to)):
                evs.append((r["d"], "Shipped " + r["shipment_no"] + (" → " + r["cust"] if r["cust"] else ""),
                            -(r["litres"] or 0)))
            for r in conn.execute("SELECT disposed_date d, ref, litres FROM disposals "
                                  "WHERE entity_type='fg' AND sku_code=? AND disposed_date>=? "
                                  "AND disposed_date<=?", (key, frm, to)):
                evs.append((r["d"], "Disposed " + (r["ref"] or ""), -(r["litres"] or 0)))
            evs.sort(key=lambda e: e[0] or "")
            bal = opening
            for d_, desc, chg in evs:
                bal = round(bal + chg, 1)
                txns.append({"date": d_, "description": desc, "change": round(chg, 1), "balance": bal})
        else:
            raise ApiError(400, "Unknown ledger dimension")

        closing = round(opening + sum(t["change"] or 0 for t in txns), 2)
        return {"title": title, "unit": unit, "from": frm, "to": to,
                "opening": opening, "closing": closing, "txns": txns}

    def route_reports(self, query, conn):
        frm = query.get("from", [""])[0]
        to = query.get("to", [""])[0]
        month = query.get("month", [""])[0]
        if frm and to:
            start, end = frm, to
        elif month:
            try:
                start, end, _next = month_bounds(month)
            except Exception:
                raise ApiError(400, "Invalid month (expected YYYY-MM)")
        else:
            start, end, _next = month_bounds(today_iso()[:7])
        if start > end:
            start, end = end, start
        asof = query.get("asof", [end])[0]   # point-in-time = end of the period

        def rows(sql, args=()):
            return conn.execute(sql, args).fetchall()

        # --- Stabilized inventory (totes) ---
        created = rows(
            "SELECT species_code sp, COUNT(*) totes, COALESCE(SUM(avg_weight_kg),0) kg "
            "FROM tote_lots WHERE checkin_date>=? AND checkin_date<=? GROUP BY species_code",
            (start, end))
        consumed = rows(
            "SELECT t.species_code sp, COUNT(*) totes, COALESCE(SUM(t.avg_weight_kg),0) kg "
            "FROM tote_lots t JOIN production_runs r ON r.id=t.run_id "
            "WHERE r.run_date>=? AND r.run_date<=? GROUP BY t.species_code", (start, end))
        onhand_stab = rows(
            "SELECT t.species_code sp, COUNT(*) totes, COALESCE(SUM(t.avg_weight_kg),0) kg "
            "FROM tote_lots t LEFT JOIN production_runs r ON r.id=t.run_id "
            "WHERE t.checkin_date<=? AND (t.run_id IS NULL OR r.run_date>?) "
            "AND (t.disposed_date IS NULL OR t.disposed_date>?) "
            "GROUP BY t.species_code", (asof, asof, asof))

        def sp_list(rs):
            return [{"species": r["sp"], "totes": r["totes"], "kg": round(r["kg"] or 0, 1)} for r in rs]

        def sp_tot(rs):
            return {"totes": sum(r["totes"] for r in rs), "kg": round(sum(r["kg"] or 0 for r in rs), 1),
                    "bySpecies": sp_list(rs)}

        # --- Production ---
        p = conn.execute(
            "SELECT COUNT(*) runs, COALESCE(SUM(input_kg),0) ik, COALESCE(SUM(output_litres),0) ol, "
            "COALESCE(SUM(citric_kg),0) ck, COALESCE(SUM(sorbate_kg),0) sk "
            "FROM production_runs WHERE run_date>=? AND run_date<=?", (start, end)).fetchone()
        prod_by_sku = rows(
            "SELECT sku_code sku, COUNT(*) runs, COALESCE(SUM(output_litres),0) litres "
            "FROM production_runs WHERE run_date>=? AND run_date<=? GROUP BY sku_code", (start, end))

        # --- Finished goods produced (by run output) & shipped (dated) ---
        produced_sku = {r["sku"]: r["litres"] for r in prod_by_sku}
        shipped = rows(
            "SELECT COALESCE(c.name,'(no customer)') cust, COALESCE(SUM(sl.qty),0) units, "
            "COALESCE(SUM(sl.qty*sl.litres_each),0) litres "
            "FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id "
            "LEFT JOIN customers c ON c.id=s.customer_id "
            "WHERE s.status!='cancelled' AND s.ship_date>=? AND s.ship_date<=? GROUP BY c.name "
            "ORDER BY litres DESC", (start, end))
        shipped_sku = rows(
            "SELECT sl.sku_code sku, COALESCE(SUM(sl.qty),0) units, COALESCE(SUM(sl.qty*sl.litres_each),0) litres "
            "FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id "
            "WHERE s.status!='cancelled' AND s.ship_date>=? AND s.ship_date<=? GROUP BY sl.sku_code",
            (start, end))

        # FG on hand as-of = produced(run_date<=asof) - shipped(ship_date<=asof)
        prod_asof = {r["sku"]: r["litres"] for r in rows(
            "SELECT sku_code sku, COALESCE(SUM(output_litres),0) litres FROM production_runs "
            "WHERE run_date<=? GROUP BY sku_code", (asof,))}
        ship_asof = {r["sku"]: r["litres"] for r in rows(
            "SELECT sl.sku_code sku, COALESCE(SUM(sl.qty*sl.litres_each),0) litres "
            "FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id "
            "WHERE s.status!='cancelled' AND s.ship_date<=? GROUP BY sl.sku_code", (asof,))}
        disp_asof = {r["sku"]: r["litres"] for r in rows(
            "SELECT sku_code sku, COALESCE(SUM(litres),0) litres FROM disposals "
            "WHERE entity_type='fg' AND disposed_date<=? GROUP BY sku_code", (asof,))}
        fg_onhand = []
        for sku in sorted(set(prod_asof) | set(ship_asof) | set(disp_asof)):
            litres = round((prod_asof.get(sku, 0) or 0) - (ship_asof.get(sku, 0) or 0)
                           - (disp_asof.get(sku, 0) or 0), 1)
            fg_onhand.append({"sku": sku, "litres": litres})

        # --- Consumables: in-month receipts/usage + on-hand as-of ---
        cons_month = rows(
            "SELECT c.name, c.unit, "
            "COALESCE(SUM(CASE WHEN t.delta>0 THEN t.delta ELSE 0 END),0) recv, "
            "COALESCE(SUM(CASE WHEN t.delta<0 THEN -t.delta ELSE 0 END),0) used "
            "FROM consumables c LEFT JOIN consumable_txns t ON t.consumable_id=c.id "
            "AND substr(t.created_at,1,10)>=? AND substr(t.created_at,1,10)<=? "
            "GROUP BY c.id ORDER BY c.name", (start, end))
        cons_asof = rows(
            "SELECT c.name, c.unit, c.on_hand - COALESCE("
            "(SELECT SUM(delta) FROM consumable_txns t WHERE t.consumable_id=c.id "
            "AND substr(t.created_at,1,10)>?),0) onhand FROM consumables c ORDER BY c.name", (asof,))

        # --- Inventory by location (current on hand) ---
        loc_stab = rows(
            "SELECT COALESCE(location,'(unspecified)') loc, COUNT(*) totes, "
            "COALESCE(SUM(avg_weight_kg),0) kg FROM tote_lots WHERE status='in_stock' "
            "GROUP BY location ORDER BY kg DESC")
        loc_fg = rows(
            "SELECT COALESCE(location,'(unspecified)') loc, COALESCE(SUM(qty),0) units, "
            "COALESCE(SUM(qty*litres_each),0) litres FROM fg_lots "
            "WHERE status NOT IN ('sold','disposed') AND qty>0 GROUP BY location ORDER BY litres DESC")
        loc_cons = rows(
            "SELECT COALESCE(location,'(unspecified)') loc, name, on_hand, unit "
            "FROM consumables ORDER BY loc, name")

        return {
            "month": month or start[:7], "from": start, "to": end,
            "period": _period_label(start, end),
            "monthStart": start, "monthEnd": end, "asOf": asof,
            "stabilized": {"created": sp_tot(created), "consumed": sp_tot(consumed),
                           "onHand": sp_tot(onhand_stab)},
            "production": {
                "runs": p["runs"], "inputKg": round(p["ik"] or 0, 1),
                "outputLitres": round(p["ol"] or 0, 1),
                "yield": round((p["ol"] / p["ik"]), 3) if p["ik"] else None,
                "citricKg": round(p["ck"] or 0, 1), "sorbateKg": round(p["sk"] or 0, 1),
                "bySku": [{"sku": r["sku"], "runs": r["runs"], "litres": round(r["litres"] or 0, 1)}
                          for r in prod_by_sku]},
            "finishedGoods": {
                "producedBySku": [{"sku": k, "litres": round(v or 0, 1)} for k, v in produced_sku.items()],
                "producedLitres": round(sum(produced_sku.values()) if produced_sku else 0, 1),
                "shippedByCustomer": [{"customer": r["cust"], "units": r["units"],
                                       "litres": round(r["litres"] or 0, 1)} for r in shipped],
                "shippedBySku": [{"sku": r["sku"], "units": r["units"],
                                  "litres": round(r["litres"] or 0, 1)} for r in shipped_sku],
                "shippedLitres": round(sum((r["litres"] or 0) for r in shipped), 1),
                "onHand": fg_onhand,
                "onHandLitres": round(sum(x["litres"] for x in fg_onhand), 1)},
            "consumables": {
                "inMonth": [{"name": r["name"], "unit": r["unit"], "received": round(r["recv"] or 0, 1),
                             "used": round(r["used"] or 0, 1)} for r in cons_month],
                "onHand": [{"name": r["name"], "unit": r["unit"], "onHand": round(r["onhand"] or 0, 1)}
                           for r in cons_asof]},
            "disposed": self._disposed_in_month(conn, start, end),
            "byLocation": {
                "stabilized": [{"location": r["loc"], "totes": r["totes"], "kg": round(r["kg"] or 0, 1)}
                               for r in loc_stab],
                "finishedGoods": [{"location": r["loc"], "units": r["units"],
                                   "litres": round(r["litres"] or 0, 1)} for r in loc_fg],
                "consumables": [{"location": r["loc"], "name": r["name"],
                                 "onHand": round(r["on_hand"] or 0, 1), "unit": r["unit"]}
                                for r in loc_cons],
            },
        }

    def _disposed_in_month(self, conn, start, end):
        rows = conn.execute(
            "SELECT entity_type, COUNT(*) n, COALESCE(SUM(qty),0) qty, COALESCE(SUM(litres),0) litres "
            "FROM disposals WHERE disposed_date>=? AND disposed_date<=? GROUP BY entity_type",
            (start, end)).fetchall()
        out = {"totes": 0, "toteKg": 0.0, "fgLots": 0, "fgLitres": 0.0, "consumableEvents": 0}
        for r in rows:
            if r["entity_type"] == "tote":
                out["totes"], out["toteKg"] = r["n"], round(r["qty"] or 0, 1)
            elif r["entity_type"] == "fg":
                out["fgLots"], out["fgLitres"] = r["n"], round(r["litres"] or 0, 1)
            elif r["entity_type"] == "consumable":
                out["consumableEvents"] = r["n"]
        out["lines"] = [{"type": r["entity_type"], "ref": r["ref"], "qty": r["qty"], "unit": r["unit"],
                         "reason": r["reason"], "by": r["disposed_by"], "date": r["disposed_date"]}
                        for r in conn.execute(
                            "SELECT * FROM disposals WHERE disposed_date>=? AND disposed_date<=? "
                            "ORDER BY disposed_date DESC, id DESC", (start, end))]
        return out

    # ---- disposal / write-off -------------------------------------------- #
    def dispose(self, conn, user):
        d = self._body_json()
        typ = d.get("type")
        reason = (d.get("reason") or "").strip()
        date = (d.get("date") or today_iso()).strip()
        if not reason:
            raise ApiError(400, "A reason / description is required to write off inventory")
        who = user["name"] if user else None
        ts = now_iso()
        disposed = 0

        def log(entity_type, eid, ref, qty, unit, litres=None, species=None, sku=None):
            conn.execute(
                "INSERT INTO disposals (entity_type,entity_id,ref,species_code,sku_code,qty,unit,"
                "litres,reason,disposed_by,disposed_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (entity_type, eid, ref, species, sku, qty, unit, litres, reason, who, date, ts))

        if typ == "tote":
            ids = [int(x) for x in (d.get("itemIds") or [])]
            if not ids:
                raise ApiError(400, "Select at least one tote to dispose")
            for tid in ids:
                t = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()
                if not t or t["status"] != "in_stock":
                    continue
                conn.execute("UPDATE tote_lots SET status='disposed', disposed_date=? WHERE id=?", (date, tid))
                log("tote", tid, t["lot_number"], t["avg_weight_kg"], "kg", species=t["species_code"])
                disposed += 1

        elif typ == "fg":
            ids = [int(x) for x in (d.get("itemIds") or [])]
            if not ids:
                raise ApiError(400, "Select at least one finished-goods lot to dispose")
            for fid in ids:
                f = conn.execute("SELECT * FROM fg_lots WHERE id=?", (fid,)).fetchone()
                if not f or f["status"] == "disposed" or (f["qty"] or 0) <= 0:
                    continue
                litres = round((f["qty"] or 0) * (f["litres_each"] or 0), 2)
                log("fg", fid, f["fg_lot_number"], f["qty"], "units", litres=litres, sku=f["sku_code"])
                conn.execute("UPDATE fg_lots SET qty=0, status='disposed' WHERE id=?", (fid,))
                disposed += 1

        elif typ == "consumable":
            items = d.get("items") or []   # [{id, qty}]
            any_qty = False
            for it in items:
                c = conn.execute("SELECT * FROM consumables WHERE id=?", (it.get("id"),)).fetchone()
                qty = num(it.get("qty"))
                if not c or qty <= 0:
                    continue
                any_qty = True
                if qty > c["on_hand"]:
                    raise ApiError(400, "Cannot dispose %g %s of %s — only %g on hand"
                                   % (qty, c["unit"], c["name"], c["on_hand"]))
                self._consume(conn, c["id"], -qty, "Disposal: " + reason, None)
                log("consumable", c["id"], c["name"], qty, c["unit"])
                disposed += 1
            if not any_qty:
                raise ApiError(400, "Enter a quantity to write off for at least one consumable")
        else:
            raise ApiError(400, "Unknown disposal type")

        return {"disposed": disposed, "reason": reason, "date": date}

    def list_disposals(self, query, conn):
        typ = query.get("type", [""])[0]
        sql = "SELECT * FROM disposals"
        args = ()
        if typ:
            sql += " WHERE entity_type=?"
            args = (typ,)
        sql += " ORDER BY disposed_date DESC, id DESC LIMIT 500"
        return {"disposals": [
            {"id": r["id"], "type": r["entity_type"], "ref": r["ref"], "qty": r["qty"], "unit": r["unit"],
             "litres": r["litres"], "reason": r["reason"], "by": r["disposed_by"], "date": r["disposed_date"]}
            for r in conn.execute(sql, args)]}


def num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def numn(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def month_bounds(month):
    """('YYYY-MM') -> (first_day, last_day, first_day_next_month) as ISO date strings."""
    y, m = int(month[:4]), int(month[5:7])
    start = datetime.date(y, m, 1)
    nm = datetime.date(y + (1 if m == 12 else 0), (m % 12) + 1, 1)
    return start.isoformat(), (nm - datetime.timedelta(days=1)).isoformat(), nm.isoformat()


def _period_label(start, end):
    """Human label for a date range, e.g. 'Jun 1 – Jun 18, 2026'."""
    try:
        a = datetime.date.fromisoformat(start)
        b = datetime.date.fromisoformat(end)
    except Exception:
        return "%s - %s" % (start, end)
    if a == b:
        return a.strftime("%b %-d, %Y") if os.name != "nt" else a.strftime("%b %#d, %Y")
    fmt_d = "%b %#d" if os.name == "nt" else "%b %-d"
    if (a.year, a.month) == (b.year, b.month):
        left = a.strftime(fmt_d)
    elif a.year == b.year:
        left = a.strftime(fmt_d)
    else:
        left = a.strftime(fmt_d + ", %Y")
    return "%s – %s" % (left, b.strftime(fmt_d + ", %Y"))


def _fmtval(v):
    """Stringify a value for the edit log (drops trailing .0 on whole numbers)."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# --------------------------------------------------------------------------- #
# Minimal dependency-free .xlsx writer (OOXML via zipfile) — enough to produce
# a nicely formatted, multi-sheet workbook without openpyxl.
# --------------------------------------------------------------------------- #
# cellXfs style indices (see STYLES_XML): 0 default, 1 title, 2 section bar,
# 3 col header, 4 #,##0, 5 #,##0.0, 6 #,##0.000, 7 bold label, 8 grey note,
# 9 col header (right).
STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="3">'
    '<numFmt numFmtId="164" formatCode="#,##0"/>'
    '<numFmt numFmtId="165" formatCode="#,##0.0"/>'
    '<numFmt numFmtId="166" formatCode="#,##0.000"/>'
    '</numFmts>'
    '<fonts count="5">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="16"/><color rgb="FF15564F"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF15564F"/><name val="Calibri"/></font>'
    '<font><i/><sz val="9"/><color rgb="FF888888"/><name val="Calibri"/></font>'
    '</fonts>'
    '<fills count="4">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF15564F"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEEF3F2"/></patternFill></fill>'
    '</fills>'
    '<borders count="2">'
    '<border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left/><right/><top/><bottom style="thin"><color rgb="FF15564F"/></bottom><diagonal/></border>'
    '</borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="10">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
    '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)


def _xlsx_col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx_numstr(v):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int) or (isinstance(v, float) and float(v).is_integer()):
        return str(int(v))
    return repr(float(v))


class XlsxSheet:
    def __init__(self, name):
        self.name = name
        self.rows = []          # each row: list of cells or None
        self.widths = []        # column widths
        self.merges = []

    def set_widths(self, widths):
        self.widths = widths

    def row(self, cells):
        self.rows.append(cells)

    def title(self, text, span):
        r = len(self.rows) + 1
        self.row([("t", text, 1)] + [None] * (span - 1))
        self.merges.append("A%d:%s%d" % (r, _xlsx_col(span - 1), r))

    def section(self, text, span):
        self.row([])            # spacer
        r = len(self.rows) + 1
        self.row([("t", text, 2)] + [("t", "", 2)] * (span - 1))
        self.merges.append("A%d:%s%d" % (r, _xlsx_col(span - 1), r))

    def xml(self):
        cols = ""
        if self.widths:
            cols = "<cols>" + "".join(
                '<col min="%d" max="%d" width="%g" customWidth="1"/>' % (i + 1, i + 1, w)
                for i, w in enumerate(self.widths)) + "</cols>"
        body = ""
        for ri, row in enumerate(self.rows, start=1):
            cells = ""
            for ci, cell in enumerate(row or []):
                if cell is None:
                    continue
                kind, val, style = cell
                ref = "%s%d" % (_xlsx_col(ci), ri)
                if kind == "n" and val is not None:
                    cells += '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, _xlsx_numstr(val))
                else:
                    txt = "" if (val is None or val == "") else escape(str(val))
                    cells += ('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                              % (ref, style, txt))
            body += '<row r="%d">%s</row>' % (ri, cells)
        merges = ""
        if self.merges:
            merges = ('<mergeCells count="%d">%s</mergeCells>'
                      % (len(self.merges), "".join('<mergeCell ref="%s"/>' % m for m in self.merges)))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
                + cols + "<sheetData>" + body + "</sheetData>" + merges + "</worksheet>")


def xlsx_build(sheets):
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    overrides = "".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1)
        for i in range(len(sheets)))
    z.writestr("[Content_Types].xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               + overrides + "</Types>")
    z.writestr("_rels/.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               '</Relationships>')
    sheets_xml = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(s.name[:31]), i + 1, i + 1)
                         for i, s in enumerate(sheets))
    z.writestr("xl/workbook.xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
               '<sheets>' + sheets_xml + '</sheets></workbook>')
    rels = "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
                   for i in range(len(sheets)))
    rels += ('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             % (len(sheets) + 1))
    z.writestr("xl/_rels/workbook.xml.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + rels + "</Relationships>")
    z.writestr("xl/styles.xml", STYLES_XML)
    for i, s in enumerate(sheets):
        z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), s.xml())
    z.close()
    return buf.getvalue()


def report_workbook(data, spname, skname):
    """Build a formatted multi-sheet .xlsx from the reports payload."""
    T = lambda v, s=0: ("t", v, s)
    N = lambda v, s=4: (("n", v, s) if v is not None else ("t", "—", 0))
    H = lambda v: ("t", v, 3)
    HR = lambda v: ("t", v, 9)
    ml = data.get("period") or data.get("month", "")
    sheets = []

    s = XlsxSheet("Summary"); s.set_widths([38, 16, 10])
    s.title("Cascadia Seaweed — Manufacturing Report", 3)
    s.row([T("Period", 7), T(ml)])
    s.row([T("Inventory on hand as of", 7), T(data["asOf"])])
    s.section("Key figures", 3)
    s.row([H("Metric"), HR("Value"), H("Unit")])
    pr = data["production"]
    for label, val, unit, st in [
        ("Stabilized inventory created", data["stabilized"]["created"]["kg"], "kg", 5),
        ("Stabilized inventory consumed", data["stabilized"]["consumed"]["kg"], "kg", 5),
        ("LKE produced", pr["outputLitres"], "L", 5),
        ("LKE shipped", data["finishedGoods"]["shippedLitres"], "L", 5),
        ("Stabilized on hand (point in time)", data["stabilized"]["onHand"]["kg"], "kg", 5),
        ("Finished goods on hand (point in time)", data["finishedGoods"]["onHandLitres"], "L", 5),
        ("Production runs", pr["runs"], "runs", 4),
        ("Yield", pr["yield"], "L/kg", 6)]:
        s.row([T(label), N(val, st), T(unit, 8)])
    sheets.append(s)

    s = XlsxSheet("Stabilized"); s.set_widths([28, 12, 16])
    s.title("Stabilized Inventory (IBC totes)", 3)
    def sp_block(title, block):
        s.section(title, 3)
        s.row([H("Species"), HR("Totes"), HR("Kg")])
        for r in block["bySpecies"]:
            s.row([T(spname.get(r["species"], r["species"])), N(r["totes"]), N(r["kg"], 5)])
        s.row([T("Total", 7), N(block["totes"]), N(block["kg"], 5)])
    sp_block("Created in " + ml, data["stabilized"]["created"])
    sp_block("Consumed into production", data["stabilized"]["consumed"])
    sp_block("On hand as of " + data["asOf"], data["stabilized"]["onHand"])
    sheets.append(s)

    s = XlsxSheet("Production"); s.set_widths([30, 14, 16])
    s.title("Production", 3)
    s.section("Summary (" + ml + ")", 3)
    for label, val, st in [("Runs", pr["runs"], 4), ("Input (kg)", pr["inputKg"], 5),
                           ("Output (L)", pr["outputLitres"], 5), ("Yield (L/kg)", pr["yield"], 6),
                           ("Citric acid (kg)", pr["citricKg"], 5), ("Potassium sorbate (kg)", pr["sorbateKg"], 5)]:
        s.row([T(label, 7), N(val, st)])
    s.section("By product", 3)
    s.row([H("SKU"), HR("Runs"), HR("Litres")])
    for r in pr["bySku"]:
        s.row([T(skname.get(r["sku"], r["sku"])), N(r["runs"]), N(r["litres"], 5)])
    sheets.append(s)

    s = XlsxSheet("Finished Goods"); s.set_widths([34, 12, 14])
    s.title("Finished Goods", 3)
    fg = data["finishedGoods"]
    s.section("Produced in " + ml, 3)
    s.row([H("SKU"), HR("Litres")])
    for r in fg["producedBySku"]:
        s.row([T(skname.get(r["sku"], r["sku"])), N(r["litres"], 5)])
    s.section("Shipped — by customer", 3)
    s.row([H("Customer"), HR("Units"), HR("Litres")])
    for r in fg["shippedByCustomer"]:
        s.row([T(r["customer"]), N(r["units"]), N(r["litres"], 5)])
    s.section("On hand as of " + data["asOf"], 3)
    s.row([H("SKU"), HR("Litres")])
    for r in fg["onHand"]:
        s.row([T(skname.get(r["sku"], r["sku"])), N(r["litres"], 5)])
    sheets.append(s)

    s = XlsxSheet("Consumables"); s.set_widths([26, 10, 12, 12, 16])
    s.title("Consumables", 5)
    s.section("Received / used in " + ml, 5)
    s.row([H("Item"), H("Unit"), HR("Received"), HR("Used"), HR("On hand " + data["asOf"])])
    oh = {r["name"]: r["onHand"] for r in data["consumables"]["onHand"]}
    for r in data["consumables"]["inMonth"]:
        s.row([T(r["name"]), T(r["unit"]), N(r["received"], 5), N(r["used"], 5), N(oh.get(r["name"]), 5)])
    sheets.append(s)

    bl = data.get("byLocation") or {}
    s = XlsxSheet("By Location"); s.set_widths([28, 14, 14])
    s.title("Inventory by Location — current on hand", 3)
    s.section("Stabilized totes", 3)
    s.row([H("Location"), HR("Totes"), HR("Kg")])
    for r in bl.get("stabilized", []):
        s.row([T(r["location"]), N(r["totes"]), N(r["kg"], 5)])
    s.section("Finished goods", 3)
    s.row([H("Location"), HR("Units"), HR("Litres")])
    for r in bl.get("finishedGoods", []):
        s.row([T(r["location"]), N(r["units"]), N(r["litres"], 5)])
    s.section("Consumables / packaging", 3)
    s.row([H("Location"), H("Item"), HR("On hand")])
    for r in bl.get("consumables", []):
        s.row([T(r["location"]), T(r["name"]), N(r["onHand"], 5)])
    sheets.append(s)

    dz = data.get("disposed") or {}
    s = XlsxSheet("Disposals"); s.set_widths([12, 12, 26, 10, 8, 40, 16])
    s.title("Disposed / Written Off — " + ml, 7)
    s.section("Summary", 7)
    s.row([T("Totes", 7), N(dz.get("totes", 0)), T("kg", 8), N(dz.get("toteKg", 0), 5)])
    s.row([T("FG lots", 7), N(dz.get("fgLots", 0)), T("litres", 8), N(dz.get("fgLitres", 0), 5)])
    s.row([T("Consumable write-offs", 7), N(dz.get("consumableEvents", 0))])
    s.section("Detail", 7)
    s.row([H("Date"), H("Type"), H("Item"), HR("Qty"), H("Unit"), H("Reason"), H("By")])
    for l in dz.get("lines", []):
        s.row([T(l["date"]), T(l["type"]), T(l["ref"]), N(l["qty"], 5),
               T(l["unit"] or ""), T(l["reason"]), T(l["by"] or "")])
    sheets.append(s)

    return xlsx_build(sheets)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    init_db()
    print("KelpWorks ERP running at http://%s:%s  (DB: %s)" % (HOST, PORT, DB_PATH))
    if ADMIN_PASSWORD == "kelp1234":
        print("Seed login: %s / kelp1234  (set KELP_ERP_ADMIN_PASSWORD before hosting)" % ADMIN_EMAIL)
    else:
        print("Admin login: %s  (password set via KELP_ERP_ADMIN_PASSWORD)" % ADMIN_EMAIL)
    if SECRET == DEV_SECRET.encode("utf-8"):
        print("WARNING: using the default dev signing secret. Set KELP_ERP_SECRET in production.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()

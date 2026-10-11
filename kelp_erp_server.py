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
  4. Reagents & packaging  : Citric Acid, Potassium Sorbate, Sodium Benzoate,
     containers (IBC totes, drums, bottles) and finished-good labels --
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
import re
import csv
import json
import math
import time
import hmac
import base64
import zipfile
import hashlib
import sqlite3
import struct
import zlib
import secrets
import tempfile
import logging
import sys
import gzip
import signal
import shutil
import threading
import datetime
import statistics
from xml.sax.saxutils import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("KELP_ERP_DB", os.path.join(BASE_DIR, "kelp_erp.db"))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
SEED_FILE = os.path.join(BASE_DIR, "seed.json")
UPLOAD_DIR = os.environ.get("KELP_ERP_UPLOADS", os.path.join(BASE_DIR, "uploads"))
# Controlled documents (SOPs) live in their own folder, but as a sibling of
# UPLOAD_DIR so they land on the same persistent disk in production (see
# render.yaml) without needing a separate env var / disk mount of their own.
SOP_DIR = os.path.join(os.path.dirname(UPLOAD_DIR), "sop_documents")
LAB_DIR = os.path.join(os.path.dirname(UPLOAD_DIR), "lab_templates")   # lab requisition .docx templates
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file
MAX_REQUEST_BYTES = 40 * 1024 * 1024  # largest JSON request body (a 25 MB file is ~34 MB as base64)
MAX_LOGIN_BODY_BYTES = 8 * 1024       # the only request accepted without a token
SOCKET_TIMEOUT = float(os.environ.get("KELP_ERP_SOCKET_TIMEOUT", "30"))     # seconds a client may stay silent mid-request
MAX_ZIP_MEMBER_BYTES = 10 * 1024 * 1024   # largest part of a .docx / .xlsx we will unpack
MAX_PDF_STREAM_BYTES = 20 * 1024 * 1024   # largest inflated PDF stream, and the budget for a whole PDF is three times that
MAX_PREVIEW_ROWS, MAX_PREVIEW_COLS = 1000, 60
MAX_PREVIEW_HTML_BYTES = 5 * 1024 * 1024
PORT = int(os.environ.get("PORT", "8002"))
HOST = os.environ.get("HOST", "0.0.0.0")
DEV_SECRET = "dev-secret-change-me"          # the old public default: never accepted as a signing secret any more


def load_secret():
    """(secret bytes, where it came from). KELP_ERP_SECRET when set; otherwise a random key generated once and kept in `kelp_secret.key` next to the
    database, so tokens survive restarts but nobody can forge one from the source code."""
    env = os.environ.get("KELP_ERP_SECRET", "").strip()
    if env and env != DEV_SECRET:
        return env.encode("utf-8"), "environment"
    path = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "kelp_secret.key")
    try:
        with open(path, "r", encoding="ascii") as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key.encode("ascii"), path
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as f:
            f.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key.encode("ascii"), path
    except OSError:
        return key.encode("ascii"), "generated for this process only (could not write %s)" % path


SECRET, SECRET_SOURCE = load_secret()
TOKEN_TTL = 60 * 60 * 12  # 12 hours

ADMIN_EMAIL = os.environ.get("KELP_ERP_ADMIN_EMAIL", "admin@kelp.local")
# Convenience defaults (admin kelp1234, staff Cascadia123!) exist ONLY for local development: start with KELP_ERP_ENV=development.
# Anywhere else a missing password is generated: the admin's once, printed once at first start; staff accounts get an unusable one until an
# administrator resets it (Admin > Users).
DEV_MODE = os.environ.get("KELP_ERP_ENV", "").strip().lower() == "development"
ADMIN_PASSWORD = os.environ.get("KELP_ERP_ADMIN_PASSWORD") or ("kelp1234" if DEV_MODE else "")

# Initial staff roster — created (if missing) on startup with a temporary
# password and forced to reset it on first login.
INITIAL_USERS = [
    "dpedde@cascadiaseaweed.com",
    "dboire@cascadiaseaweed.com",
    "nwrana@cascadiaseaweed.com",
]
INITIAL_USER_PASSWORD = os.environ.get("KELP_ERP_INITIAL_PASSWORD") or ("Cascadia123!" if DEV_MODE else "")
MIN_PASSWORD_LEN = 8

# Environment. Anything other than "staging" is production. A STAGING server may restore a copy of the live data (full backup .zip) from the
# Admin page; that is only possible when KELP_ERP_ENV=staging AND KELP_ERP_ALLOW_RESTORE=1, so the live site can never be overwritten by it.
ENV_NAME = "staging" if os.environ.get("KELP_ERP_ENV", "").strip().lower() == "staging" else ("development" if DEV_MODE else "production")
ALLOW_RESTORE = ENV_NAME == "staging" and os.environ.get("KELP_ERP_ALLOW_RESTORE", "").strip() == "1"
STAGING_PASSWORD = os.environ.get("KELP_ERP_STAGING_PASSWORD", "")        # every login password after a restore (live passwords never carry over)
MAX_RESTORE_BYTES = int(os.environ.get("KELP_ERP_MAX_RESTORE_MB", "900")) * 1024 * 1024
BACKUP_FORMAT = "kelpworks-backup"

# (key, default value, label, description) -- seeded into the `settings`
# table on every boot (INSERT OR IGNORE, so an admin's edited value is never
# overwritten). Every constant a calculated field depends on belongs here,
# not as a bare literal in the formula -- see the Calculations page.
SETTINGS_DEFAULTS = [
    ("orp_spoiled_below", -200, "ORP \"Spoiled\" threshold (mV)",
     "ORP readings below this value are classified \"Spoiled\"."),
    ("orp_spoilage_underway_below", -50, "ORP \"Spoilage underway\" threshold (mV)",
     "ORP readings at or above \"Spoiled\" but below this value are classified \"Spoilage underway\"."),
    ("orp_watch_closely_below", 0, "ORP \"Watch closely\" threshold (mV)",
     "ORP readings at or above \"Spoilage underway\" but below this value are classified \"Watch closely\";"
     " at or above it, \"Stable / safe zone\"."),
    ("dilution_tank_capacity_each_l", 5000, "Tank 6A / 6B capacity, each (L)",
     "Maximum working volume of EACH of Tank 6A and Tank 6B (6A + 6B connected = double) -- used by the Dilution plan's available space / maximum product transfer."),
    ("sample_retention_months", 12, "Retention sample shelf life (months)",
     "How long a Retention sample is kept: its discard-by date is the collection date plus this many months (shown in the Samples tab's Retention inventory)."),
    ("preproc_target_solids_pct", 50, "Pre-processing target blend solids (%)",
     "Default solids loading a shred-and-blend (Pre-Processing) batch is diluted to. Recommended dilution water = shredded kg x (starting % solids / target % solids - 1), taking 1 kg of water = 1 L."),
    ("preproc_target_ph", 3.7, "Pre-processing target pH",
     "Default pH a shred-and-blend batch is adjusted to with citric acid before it is packed back into inventory."),
    ("dilution_variance_flag_pct", 5, "Dilution final-volume variance flag (%)",
     "Flags a dilution when the final tank volume differs from the expected volume (starting level + product transferred + water added) by more than this percentage."),
    ("extraction_default_amplitude_pct", 100, "Extraction default Amplitude (%)",
     "Pre-filled value for a new run's Extraction Amplitude (%) field."),
    ("extraction_default_flowrate_lpm", 15, "Extraction default Flow rate (L/min)",
     "Pre-filled value for a new run's Extraction Flow rate (L/min) field."),
    ("homog_default_target_pct_wet_solids", 50.0, "Homogenization default Target %Solids Loading, (w/w)",
     "Pre-filled value for a new run's Target %Solids Loading, (w/w) field."),
    ("ksorbate_stock_concentration_default_pct", 25.0, "Ksorbate stock concentration default (w/v %)",
     "Pre-filled value for a new run's Ksorbate stock concentration (w/v) field."),
    ("nabenzoate_stock_concentration_default_pct", 25.0, "Nabenzoate stock concentration default (w/v %)",
     "Pre-filled value for a new run's Sodium benzoate stock concentration (w/v) field."),
    ("yield_report_min_runs", 5, "Yield & Usage minimum runs per group",
     "A Yield & Usage group with fewer completed (non-excluded) runs than this is tagged \"Low sample\"."),
    ("coa_application_rate_kg_ha", 0, "Certificate of Analysis: product application rate (kg/ha)",
     "Kilograms of product applied per hectare in one application. A heavy-metal result (mg/kg) is converted to a loading on the Certificate of Analysis: kg metal/ha = mg/kg x this rate x Application periods / 1,000,000, and judged against the kg/ha specification. 0 = not set (metals are listed but not judged)."),
    ("coa_application_periods", 1, "Certificate of Analysis: application periods",
     "Number of applications the metal loading accumulates over (a multiplier on the kg metal/ha: loading = mg/kg x application rate x this / 1,000,000). 1 = a single application."),
    ("qc_chart_sigma", 3, "Control charts: sigma multiplier",
     "Recommended control limits = mean +/- this many standard deviations, the standard deviation estimated from the average moving range (MRbar / 1.128). 3 is the usual choice."),
    ("qc_chart_min_n_provisional", 8, "Control charts: points needed for provisional limits",
     "Fewest measurements before the Quality Control page offers PROVISIONAL control-limit recommendations. Below this it only shows how many more are needed."),
    ("qc_chart_min_n_established", 20, "Control charts: points needed for established limits",
     "Measurements after which recommended limits are no longer labelled provisional (20 to 25 is the usual guidance for an individuals chart)."),
    ("qc_chart_run_length", 7, "Control charts: run-length signal",
     "A signal is raised when this many consecutive points fall on the same side of the centre line."),
    ("qc_chart_trend_length", 6, "Control charts: trend signal",
     "A signal is raised when this many consecutive points keep rising (or keep falling)."),
    ("separation_default_flowrate_lpm", 40, "Separation default Flow rate (L/min)",
     "Pre-filled value for a new run's Separation Flow rate (L/min) field."),
    ("separation_default_mesh_micron", 74, "Separation default Mesh size (micron)",
     "Pre-filled value for a new run's Separation Mesh size (micron) field."),
    ("pasteurization_default_product_setpoint_c", 80, "Pasteurization default Product set-point (°C)",
     "Pre-filled value for a new run's Pasteurization Product set-point (°C) field."),
    ("pasteurization_default_boiler_setpoint_c", 90, "Pasteurization default Boiler set-point (°C)",
     "Pre-filled value for a new run's Pasteurization Boiler set-point (°C) field."),
]

# The fixed catalog of "QC Check" fields on the production log -- only fields
# inside a box whose qc-check-title is literally "QC Check" (not "Process
# Check", not "Sample Point"). field_id is the production_runs column name
# itself (already unique across the schema), doubling as this field's stable
# identity for qc_field_log. (field_id, stage, stage_label, subtitle, label, unit)
QC_FIELD_REGISTRY = [
    # Homogenization -- "Lot characterization"
    ("homog_qc_ph",           "homogenization", "Homogenization", "Lot characterization", "pH", ""),
    ("homog_tds_pct",         "homogenization", "Homogenization", "Lot characterization", "TDS (%)", "%"),
    ("homog_brix_pct",        "homogenization", "Homogenization", "Lot characterization", "Brix (%)", "%"),
    ("homog_mannitol_pct",    "homogenization", "Homogenization", "Lot characterization", "Mannitol (%)", "%"),
    ("homog_ts_liquid_pct",   "homogenization", "Homogenization", "Lot characterization", "TSliquid (%)", "%"),
    ("homog_rho_liquid_g_ml", "homogenization", "Homogenization", "Lot characterization", "ρliquid (g/mL)", "g/mL"),
    ("homog_ts_slurry_pct",   "homogenization", "Homogenization", "Lot characterization", "TSslurry (%)", "%"),
    ("homog_rho_slurry_g_ml", "homogenization", "Homogenization", "Lot characterization", "ρslurry (g/mL)", "g/mL"),
    ("homog_ts_solids_pct",   "homogenization", "Homogenization", "Lot characterization", "%Moisture<sub>solids</sub>", "%"),
    ("homog_solids_loading_pct", "homogenization", "Homogenization", "Lot characterization", "Solids Loading (%)", "%"),
    # Extraction -- "Extraction Performance"
    ("extraction_qc_ph",           "extraction", "Extraction", "Extraction Performance", "pH", ""),
    ("extraction_tds_pct",         "extraction", "Extraction", "Extraction Performance", "TDS (%)", "%"),
    ("extraction_brix_pct",        "extraction", "Extraction", "Extraction Performance", "Brix (%)", "%"),
    ("extraction_mannitol_pct",    "extraction", "Extraction", "Extraction Performance", "Mannitol (%)", "%"),
    ("extraction_ts_liquid_pct",   "extraction", "Extraction", "Extraction Performance", "TSliquid (%)", "%"),
    ("extraction_rho_liquid_g_ml", "extraction", "Extraction", "Extraction Performance", "ρliquid (g/mL)", "g/mL"),
    ("extraction_ts_slurry_pct",   "extraction", "Extraction", "Extraction Performance", "TSslurry (%)", "%"),
    ("extraction_rho_slurry_g_ml", "extraction", "Extraction", "Extraction Performance", "ρslurry (g/mL)", "g/mL"),
    ("extraction_ts_solids_pct",   "extraction", "Extraction", "Extraction Performance", "%Moisture<sub>solids</sub>", "%"),
    # Separation -- "Solids characterization"
    ("separation_pct_moisture", "separation", "Separation", "Solids characterization", "%Moisture<sub>centrifuge_solids</sub>", "%"),
    ("separation_pct_moisture_screw", "separation", "Separation", "Solids characterization", "%Moisture<sub>screw_solids</sub>", "%"),
    # Separation -- "Filtrate characterization"
    ("separation_liquid_qc_ph",           "separation", "Separation", "Filtrate characterization", "pH", ""),
    ("separation_liquid_tds_pct",         "separation", "Separation", "Filtrate characterization", "TDS (%)", "%"),
    ("separation_liquid_brix_pct",        "separation", "Separation", "Filtrate characterization", "Brix (%)", "%"),
    ("separation_liquid_mannitol_pct",    "separation", "Separation", "Filtrate characterization", "Mannitol (%)", "%"),
    ("separation_liquid_ts_liquid_pct",   "separation", "Separation", "Filtrate characterization", "TSliquid (%)", "%"),
    ("separation_liquid_rho_liquid_g_ml", "separation", "Separation", "Filtrate characterization", "ρliquid (g/mL)", "g/mL"),
    # Packaging -- "LKE characterization"
    ("packaging_qc_ph",           "packaging", "Packaging", "LKE characterization", "pH", ""),
    ("packaging_tds_pct",         "packaging", "Packaging", "LKE characterization", "TDS (%)", "%"),
    ("packaging_brix_pct",        "packaging", "Packaging", "LKE characterization", "Brix (%)", "%"),
    ("packaging_mannitol_pct",    "packaging", "Packaging", "LKE characterization", "Mannitol (%)", "%"),
    ("packaging_ts_liquid_pct",   "packaging", "Packaging", "LKE characterization", "TSliquid (%)", "%"),
    ("packaging_rho_liquid_g_ml", "packaging", "Packaging", "LKE characterization", "ρliquid (g/mL)", "g/mL"),
]


# Production-log required fields. A run cannot be finalized until every field
# here has a value, and a section's progress dot only turns green once all of
# its fields do. Keys are the JSON names in run_public()["stages"][stage]
# (feedstock keys are the run_inputs public names). Deliberately NOT required:
# every Notes field; the Homogenization / Separation / Pasteurization Sample
# Point boxes; Extraction's QC Check Total solids + Density fields; checkboxes
# (an unticked box is a real answer); values the app calculates itself; and
# the legacy Dilution tank rows (new ones can no longer be added).
REQUIRED_FEEDSTOCK = [
    ("loadedAt", "Loaded at"), ("ph", "pH"), ("orp", "ORP (mV)"), ("weightKg", "Weight (kg)"),
    ("volumeL", "Volume (L)"), ("odour", "Odour"), ("odourIntensity", "Odour intensity"),
    ("decision", "Accept / reject decision"), ("surfacePhoto", "Surface photo"), ("striationPhoto", "Settling / striation photo"),
]
_QC_ALL = [
    ("qcPh", "QC Check: pH"), ("tdsPct", "QC Check: TDS (%)"), ("brixPct", "QC Check: Brix (%)"),
    ("mannitolPct", "QC Check: Mannitol (%)"), ("tsLiquidPct", "QC Check: TSliquid (%)"),
    ("rhoLiquidGMl", "QC Check: \u03c1liquid (g/mL)"),
]
# A field entry may carry a 4th element: a predicate over the run's stages dict -- the field is
# only required when it is true (per-tank fields apply only to the tanks being filled).
_uses_6a = lambda st: (st["pasteurization"].get("receivingTanks") or "") in ("6A", "6AB")
_uses_6b = lambda st: (st["pasteurization"].get("receivingTanks") or "") in ("6B", "6AB")
PROGRESS_SECTIONS = [
    {"key": "feedstock", "label": "Feedstock"},
    {"key": "homogenization", "label": "Homogenization", "fields": [
        ("homogenization", "startedAt", "Started at"), ("homogenization", "rinsingWaterL", "Rinse water (L)"),
        ("homogenization", "slurryL", "Pre-Dilution Tank Level (L)"), ("homogenization", "wetSolidsWtG", "Wet-solids-wt (g)"),
        ("homogenization", "liquidWtG", "Liquid-wt (g)"), ("homogenization", "targetPctWetSolids", "Target %Solids Loading, (w/w)"),
        ("homogenization", "dilutionWaterL", "Dilution water added (L)"),
        ("homogenization", "postDilutionTankL", "Post-Dilution Tank Level (L)")]
        + [("homogenization", k, l) for k, l in _QC_ALL]
        + [("homogenization", "tsSlurryPct", "QC Check: TSslurry (%)"),
           ("homogenization", "rhoSlurryGMl", "QC Check: \u03c1slurry (g/mL)"),
           ("homogenization", "tsSolidsPct", "QC Check: %Moisture solids"),
           ("homogenization", "solidsLoadingPct", "QC Check: Solids Loading (%)")]},
    {"key": "extraction", "label": "Extraction", "fields": [
        ("extraction", "startedAt", "Started at"), ("extraction", "amplitudePct", "Amplitude (%)"),
        ("extraction", "flowrateLpm", "Flow rate (L/min)"), ("extraction", "pressurePsi", "Pressure (psi)"),
        ("extraction", "startingPowerW", "Starting power (W)"),
        ("extraction", "qcPh", "QC Check: pH"), ("extraction", "tdsPct", "QC Check: TDS (%)"),
        ("extraction", "brixPct", "QC Check: Brix (%)"), ("extraction", "mannitolPct", "QC Check: Mannitol (%)")]},
    {"key": "separation", "label": "Separation", "fields": [
        ("separation", "startedAt", "Started at"), ("separation", "flowrateLpm", "Flow rate (L/min)"),
        ("separation", "meshMicron", "Mesh size (micron)"), ("separation", "wetSolidsWtKg", "Total wet-solids weight (kg)"),
        ("separation", "pctMoisture", "%Moisture centrifuge solids"), ("separation", "pctMoistureScrew", "%Moisture screw solids"),
        ("separation", "liquidQcPh", "Filtrate QC Check: pH"), ("separation", "liquidTdsPct", "Filtrate QC Check: TDS (%)"),
        ("separation", "liquidBrixPct", "Filtrate QC Check: Brix (%)"),
        ("separation", "liquidMannitolPct", "Filtrate QC Check: Mannitol (%)"),
        ("separation", "liquidTsLiquidPct", "Filtrate QC Check: TSliquid (%)"),
        ("separation", "liquidRhoLiquidGMl", "Filtrate QC Check: \u03c1liquid (g/mL)")]},
    {"key": "pasteurization", "label": "Pasteurization", "fields": [
        ("pasteurization", "startedAt", "Started at"), ("pasteurization", "productSetpointC", "Product set-point (\u00b0C)"),
        ("pasteurization", "boilerSetpointC", "Boiler set-point (\u00b0C)"),
        ("pasteurization", "tank5aL", "Tank 5A level (L)"), ("pasteurization", "tank5bL", "Tank 5B level (L)"),
        ("pasteurization", "receivingTanks", "Receiving tanks (6A / 6B / both)"),
        ("pasteurization", "tank6aStartL", "Tank 6A level before transfer (L)", _uses_6a),
        ("pasteurization", "tank6bStartL", "Tank 6B level before transfer (L)", _uses_6b)]},
    {"key": "dilution", "label": "Dilution & Preservation", "fields": [
        ("dilution", "fillLevelTank6abL", "Fill level, Tank 6A/B (L)"), ("dilution", "measuredPh", "Measured pH"),
        ("dilution", "citricKg", "Citric acid added (kg)"), ("dilution", "ksorbateStockPct", "Ksorbate stock concentration"),
        ("dilution", "ksorbateAddedL", "Ksorbate added (L)"), ("dilution", "nabenzoateStockPct", "Nabenzoate stock concentration"),
        ("dilution", "nabenzoateAddedL", "Sodium benzoate added (L)"),
        ("dilution", "productTransferredL", "Product transferred from 5A/5B (L)"),
        ("dilution", "waterAddedL", "Dilution water added (L)"),
        ("dilution", "tank6aFinalL", "Final level, Tank 6A (L)", _uses_6a),
        ("dilution", "tank6bFinalL", "Final level, Tank 6B (L)", _uses_6b),
        ("dilution", "ksorbateAddedL6a", "Ksorbate added, Tank 6A (L)", _uses_6a),
        ("dilution", "ksorbateAddedL6b", "Ksorbate added, Tank 6B (L)", _uses_6b),
        ("dilution", "nabenzoateAddedL6a", "Sodium benzoate added, Tank 6A (L)", _uses_6a),
        ("dilution", "nabenzoateAddedL6b", "Sodium benzoate added, Tank 6B (L)", _uses_6b)]
        + [("packaging", k, l.replace("QC Check", "LKE QC Check")) for k, l in _QC_ALL]
        + [("packaging", "sampleCollectedAt", "LKE Sample Point: collection date and time")],
     "sample_rows": "LKE Sample Point: at least one sample"},
    {"key": "packaging", "label": "Packaging", "fields": [
        ("packaging", "packagedAt", "Packaging date and time")],
     "packaging_entries": "At least one packaged output quantity"},
]


def required_keys_by_stage():
    """stage -> [keys] the SPA marks with an asterisk (plus the two table rules)."""
    out = {"feedstock": [k for k, _l in REQUIRED_FEEDSTOCK], "packagingEntries": True, "packagingSampleRows": True}
    for sec in PROGRESS_SECTIONS:
        for entry in sec.get("fields", []):
            stage, key = entry[0], entry[1]
            out.setdefault(stage, [])
            if key not in out[stage]:
                out[stage].append(key)
    return out


_KEY_TOKENS = {"ph": "pH", "tds": "TDS", "orp": "ORP", "psi": "(psi)", "pct": "(%)", "l": "(L)", "kg": "(kg)",
               "g": "(g)", "ml": "mL", "rho": "\u03c1", "ts": "TS", "qc": "QC", "lpm": "(L/min)", "w": "(W)",
               "c": "(\u00b0C)", "ibc": "IBC", "sku": "SKU", "id": "ID", "gperml": "(g/mL)", "ksorbate": "Ksorbate",
               "nabenzoate": "Nabenzoate", "6ab": "6A/B"}


_KEY_LABELS = {
    "citricItemId": "Citric acid item used", "ksorbateItemId": "Potassium sorbate item used",
    "nabenzoateItemId": "Sodium benzoate item used",
    "slurryL": "Pre-Dilution Tank Level (L)", "postDilutionTankL": "Post-Dilution Tank Level (L)",
    "dilutionWaterL": "Dilution water added (L)", "lotPctSolids": "Lot %Solids Loading, (w/w)",
    "pctWetSolids": "Measured %Solids Loading, (w/w)", "targetPctWetSolids": "Target %Solids Loading, (w/w)",
    "dilutionWaterTargetL": "Recommended Dilution Water (L)",
    "tank5aL": "Tank 5A level (L)", "tank5bL": "Tank 5B level (L)", "receivingTanks": "Receiving tanks",
    "tank6aStartL": "Tank 6A level before transfer (L)", "tank6bStartL": "Tank 6B level before transfer (L)",
    "maxTransferL": "Max product to transfer (L)", "recommendedTransferL": "Recommended product transfer (L)",
    "recommendedWaterL": "Recommended dilution water (L)", "productTransferredL": "Product transferred (L)",
    "waterAddedL": "Dilution water added (L)", "tank6aFinalL": "Final level, Tank 6A (L)",
    "tank6bFinalL": "Final level, Tank 6B (L)", "ksorbateAddedL6a": "Ksorbate added, Tank 6A (L)",
    "ksorbateAddedL6b": "Ksorbate added, Tank 6B (L)", "nabenzoateAddedL6a": "Sodium benzoate added, Tank 6A (L)",
    "nabenzoateAddedL6b": "Sodium benzoate added, Tank 6B (L)", "finalVariancePct": "Final volume variance (%)",
    "fillLevelTank6abL": "Total final volume, Tanks 6A/6B (L)",
}


def humanize_key(k):
    if k in _KEY_LABELS:
        return _KEY_LABELS[k]
    toks = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+[a-z]*", str(k).replace("GMl", "Gperml"))
    return " ".join(_KEY_TOKENS.get(t.lower(), t[:1].upper() + t[1:]) for t in toks) or str(k)


STAGE_LABELS = {"homogenization": "Homogenization", "extraction": "Extraction", "separation": "Separation",
                "pasteurization": "Pasteurization", "dilution": "Dilution & Preservation", "packaging": "Packaging"}


def _has_value(v):
    return v is not None and not (isinstance(v, str) and v.strip() == "")


def get_settings(conn):
    return {r["key"]: {"value": r["value"], "label": r["label"], "description": r["description"]}
            for r in conn.execute("SELECT * FROM settings ORDER BY key")}


def get_setting_value(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def packaging_container_litres_map(conn):
    """Live name -> litres-each lookup for every container consumable valid
    as a Packaging table / FG-output option (litres_each set)."""
    return {r["name"]: r["litres_each"] for r in
            conn.execute("SELECT name, litres_each FROM consumables WHERE is_container=1 AND litres_each IS NOT NULL")}

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
    created_at           TEXT NOT NULL,
    token_version        INTEGER NOT NULL DEFAULT 0       -- bumped to end a user's existing sessions (password change / reset / deactivation)
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
    checkin_date  TEXT,                   -- YYYY-MM-DD; exposed to the API/UI as "harvestDate"/"Harvest date"
    received_date TEXT,                   -- YYYY-MM-DD; when the tote was received at the facility (distinct from when it was harvested)
    tote_number   INTEGER,
    volume_l      REAL DEFAULT 1000,
    ph            REAL,
    ph_updated    TEXT,                   -- date the pH reading was last logged
    avg_weight_kg REAL,                   -- batch total kg / tote count
    location      TEXT,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'in_stock',  -- in_stock | hold | consumed | disposed
    run_id        INTEGER REFERENCES production_runs(id),
    disposed_date TEXT,                   -- date written off (NULL unless disposed)
    stabilization_method TEXT DEFAULT 'Citric acid',  -- Citric acid | Fresh
    storage_unit         TEXT DEFAULT 'Tote',         -- Tote | Bag
    storage_source       TEXT,   -- where the empty storage unit came from (consumable name, or e.g. "Burlap sack")
    orp                   REAL,  -- current ORP (mV) reading
    orp_updated           TEXT,  -- date the ORP reading was last logged
    notes                 TEXT,  -- free-text operator notes (separate from the auto-generated description)
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

-- Feedstock Stability log: one row per changed field on a tote (pH, weight,
-- location, status, ...), who changed it and when. Supersedes tote_ph_log
-- (kept, unused, for any history already in it) as the single audit trail.
CREATE TABLE IF NOT EXISTS tote_stability_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tote_lot_id   INTEGER NOT NULL REFERENCES tote_lots(id) ON DELETE CASCADE,
    user_name     TEXT,
    field         TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL,
    run_id        INTEGER,        -- production run this entry originated from, if any
    attachment_id INTEGER         -- an uploaded photo: run_attachments.id if run_id is set,
                                   -- else tote_attachments.id (this row's own tote_lot_id)
);
CREATE INDEX IF NOT EXISTS idx_stability_tote ON tote_stability_log(tote_lot_id);

-- Photos captured against a tote directly (e.g. from Feedstock Inventory's
-- Detail card), independent of any production run.
CREATE TABLE IF NOT EXISTS tote_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tote_lot_id  INTEGER NOT NULL REFERENCES tote_lots(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    content_type TEXT,
    size         INTEGER,
    stored_name  TEXT NOT NULL,
    uploaded_by  TEXT,
    uploaded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tote_attach_tote ON tote_attachments(tote_lot_id);

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

-- Controlled documents (Standard Operating Procedures) referenced by name
-- from spots in the production log (e.g. a QC Check's "Determining % Wet
-- Solids SOP" link) -- admin-managed via Admin > SOP Documents, so the
-- actual file can be uploaded/replaced without touching any code.
CREATE TABLE IF NOT EXISTS sop_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,   -- editable display name, shown wherever this doc is linked
    key          TEXT UNIQUE,            -- stable internal reference code a link is coded against,
                                          -- so renaming `name` never breaks that link; set once, not editable
    filename     TEXT,
    content_type TEXT,
    size         INTEGER,
    stored_name  TEXT,                  -- opaque name on disk; NULL until a file is uploaded
    uploaded_by  TEXT,
    uploaded_at  TEXT,
    updated_at   TEXT NOT NULL
);

-- Audit trail for sop_documents edits (name/file changes) -- one row per
-- changed field, same shape as tote_stability_log/run_edits.
CREATE TABLE IF NOT EXISTS sop_document_edits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sop_id        INTEGER NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
    user_name     TEXT,
    field         TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sop_edits_sop ON sop_document_edits(sop_id);

-- Lot-specific quality control measurements, traced back to the production
-- run (processing lot) they were taken on.
CREATE TABLE IF NOT EXISTS qc_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    sample_location TEXT,
    sample_type     TEXT,
    metric          TEXT NOT NULL,
    value           REAL NOT NULL,
    unit            TEXT,
    notes           TEXT,
    recorded_by     TEXT,
    recorded_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qc_run ON qc_logs(run_id);

-- Per-field audit trail for the fixed "QC Check" fields on production_runs
-- (see QC_FIELD_REGISTRY) -- replaces qc_logs (which stays, unused, for
-- historical data) as the write path's companion ledger. One row per
-- (run, field): last-write-wins, updated only when the field's *value*
-- actually changes (see save_stage/_log_qc_field_changes). field_id is the
-- production_runs column name for that field, already unique across the
-- schema. No row = "not yet recorded".
CREATE TABLE IF NOT EXISTS qc_field_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    field_id      TEXT NOT NULL,
    value         REAL NOT NULL,
    recorded_by   TEXT,
    recorded_at   TEXT NOT NULL,
    UNIQUE (run_id, field_id)
);
CREATE INDEX IF NOT EXISTS idx_qcfieldlog_run ON qc_field_log(run_id);

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

-- Inventory items (the table keeps its original "consumables" name; the UI
-- calls the general group "Reagents": citric acid, potassium sorbate, sodium
-- benzoate ...). Three groups share this one table, told apart by flags
-- rather than a category column:
--   * Packaging: is_container=1 -- a container type (New 1,000 L IBC Tote,
--     55 gallon drum, 2 L, 1 L, a Sample Point vessel like a falcon tube,
--     ...) with the same on-hand/reorder/cost/location fields as any other
--     item. litres_each is set only for containers used in Packaging's
--     FG-output math; left null for containers that are never an FG
--     package (Used 1,000 L IBC Tote) or that are only a Sample Point
--     vessel (is_sample_container=1) -- a container can be neither,
--     either, or both.
--   * Finished-good labels: label_sku_code + label_package both set -- one
--     item per (SKU, package type), deducted 1 per container consumed by the
--     Packaging table's commit (Save / finalize). Unrelated to the internal barcode labels printed from a row's Label button.
--   * Reagents: everything else.
-- item_number is an optional, admin-assigned stock/part number on any item.
CREATE TABLE IF NOT EXISTS consumables (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    unit                TEXT NOT NULL,
    on_hand             REAL NOT NULL DEFAULT 0,
    reorder_level       REAL NOT NULL DEFAULT 0,
    cost_per_unit       REAL,
    location            TEXT,
    is_container        INTEGER NOT NULL DEFAULT 0,
    litres_each         REAL,
    is_sample_container INTEGER NOT NULL DEFAULT 0,
    item_number         TEXT,
    label_sku_code      TEXT,
    label_package       TEXT,
    is_cip_agent        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consumable_txns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    consumable_id INTEGER NOT NULL REFERENCES consumables(id),
    delta         REAL NOT NULL,          -- + receipt, - usage
    reason        TEXT,
    ref           TEXT,                   -- e.g. processing lot
    user_name     TEXT,                   -- who made the change, if known
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fg_skus (
    code               TEXT PRIMARY KEY,        -- SACC-LKE, MACRO-LKE
    name               TEXT NOT NULL,
    species_code       TEXT REFERENCES species(code),  -- legacy single-species FK, unused
    tds_target         REAL,   -- product spec: target TDS (%)
    ph_target          REAL,   -- product spec: target pH
    ksorbate_target    REAL,   -- product spec: potassium sorbate (w/v)
    nabenzoate_target  REAL,   -- product spec: sodium benzoate (w/v)
    active             INTEGER NOT NULL DEFAULT 1  -- offered in the picker?
);

-- Many-to-many: a SKU may draw from more than one species (e.g. a blend).
CREATE TABLE IF NOT EXISTS fg_sku_species (
    sku_code     TEXT NOT NULL REFERENCES fg_skus(code),
    species_code TEXT NOT NULL REFERENCES species(code),
    PRIMARY KEY (sku_code, species_code)
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
    nabenzoate_kg  REAL DEFAULT 0,
    exclude_from_stats INTEGER NOT NULL DEFAULT 0,  -- test/spoiled run: skipped by Yield & Usage
    exclude_reason TEXT,
    finalized_at   TEXT,  -- when the run was finalized (NULL for runs finalized before this was recorded)
    ibc_used       INTEGER DEFAULT 0,
    location       TEXT,
    notes          TEXT,
    status         TEXT NOT NULL DEFAULT 'completed',  -- draft | completed
    draft_data     TEXT,  -- JSON {toteIds, packages, feedstockDetails} while status='draft'
    operators      TEXT,  -- free text, e.g. "DP, AL, NW"
    -- Homogenization stage (singular per run)
    homog_rinsing_water_l    REAL,
    homog_slurry_l           REAL,
    homog_dilution_water_l   REAL,
    homog_citric_kg          REAL,
    homog_output_l           REAL,
    homog_started_at         TEXT,
    homog_wet_solids_wt_g    REAL,  -- Homogenization Input QC Check
    homog_liquid_wt_g        REAL,
    homog_pct_wet_solids     REAL,  -- calculated: wet_solids_wt / (wet_solids_wt + liquid_wt)
    homog_post_dilution_tank_l REAL, -- measured Post-Dilution Tank Level (L)
    homog_lot_pct_solids     REAL,  -- calculated lot %solids (ratio) = measured * (post_tank - dilution_water) / post_tank
    homog_initial_ph              REAL,  -- Homogenization Input
    homog_target_pct_wet_solids   REAL,  -- Homogenization Output, entered as a percent (e.g. 50.0)
    homog_dilution_water_target_l REAL,  -- calculated Recommended Dilution Water (L) = V1*(c1/c2 - 1), 0 if c1<=c2 (c1V1=c2V2); older runs stored the former Target fill level here
    homog_final_ph                REAL,
    -- Homogenization Output, QC Check (lot characterization)
    homog_qc_ph              REAL,
    homog_tds_pct            REAL,
    homog_brix_pct           REAL,
    homog_mannitol_pct       REAL,
    homog_ts_liquid_pct      REAL,
    homog_rho_liquid_g_ml    REAL,
    homog_ts_slurry_pct      REAL,
    homog_rho_slurry_g_ml    REAL,
    homog_ts_solids_pct      REAL,
    homog_solids_loading_pct REAL,
    -- Homogenization Output, old fixed Sample Point checklist (0/1 collected)
    -- -- superseded by the repeatable run_sample_points table, columns stay
    -- (additive-only) but are no longer collected.
    homog_sample_slurry_microbial  INTEGER DEFAULT 0,
    homog_sample_slurry_retention  INTEGER DEFAULT 0,
    homog_sample_liquid_metals     INTEGER DEFAULT 0,
    homog_sample_solids_proximate  INTEGER DEFAULT 0,
    -- Homogenization Output, Sample Point box: one collection date/time
    -- shared by every row in the run_sample_points table.
    homog_sample_collected_at      TEXT,
    -- Extraction stage (singular per run)
    extraction_amplitude_pct     REAL,
    extraction_flowrate_lpm      REAL,
    extraction_pressure_psi      REAL,
    extraction_starting_power_w  REAL,
    extraction_started_at        TEXT,
    -- Extraction Out, QC Check (subtitle "Extraction Performance") -- same
    -- Liquid / Slurry-Solids readings as the Homogenization Output QC Check.
    extraction_qc_ph             REAL,
    extraction_tds_pct           REAL,
    extraction_brix_pct          REAL,
    extraction_mannitol_pct      REAL,
    extraction_ts_liquid_pct     REAL,
    extraction_rho_liquid_g_ml   REAL,
    extraction_ts_slurry_pct     REAL,
    extraction_rho_slurry_g_ml   REAL,
    extraction_ts_solids_pct     REAL,
    -- Separation stage parameters. run_separation_solids stays defined
    -- (additive-only) but is no longer used -- the repeatable solids
    -- collection feature was removed in favor of the single Total
    -- wet-solids weight field below, and separation_water_addition_l stays
    -- defined but uncollected -- "Water addition (L)" was removed.
    separation_flowrate_lpm      REAL,
    separation_mesh_micron       REAL,
    separation_water_addition_l  REAL,
    separation_started_at        TEXT,
    -- Separation, Solids Out
    separation_wet_solids_wt_kg  REAL,
    separation_pct_moisture      REAL,
    separation_pct_moisture_screw REAL,
    -- Separation, Liquid Out QC Check (subtitle "Filtrate characterization")
    -- -- Liquid-only fields, no Slurry/Solids section.
    separation_liquid_qc_ph            REAL,
    separation_liquid_tds_pct          REAL,
    separation_liquid_brix_pct         REAL,
    separation_liquid_mannitol_pct     REAL,
    separation_liquid_ts_liquid_pct    REAL,
    separation_liquid_rho_liquid_g_ml  REAL,
    -- Separation, Solids Out Sample Point box: one collection date/time
    -- shared by every row in that box's run_sample_points rows.
    separation_solids_sample_collected_at  TEXT,
    -- Pasteurization stage (singular per run)
    pasteurization_product_setpoint_c  REAL,
    pasteurization_boiler_setpoint_c   REAL,
    pasteurization_total_volume_l      REAL,
    pasteurization_started_at          TEXT,
    -- Pasteurization's two Sample Point boxes (pre/post), each with its own
    -- collection date/time shared by every row in that box's run_sample_points rows.
    pasteurization_pre_sample_collected_at   TEXT,
    pasteurization_post_sample_collected_at  TEXT,
    -- Pasteurization In, Process Check (subtitle "Dilution requirements")
    pasteurization_tds_pct  REAL,
    -- Dilution & Preservation, "Dilution" -> Process Check (subtitle
    -- "pH control") and "Preservatives" -- singular per run, independent of
    -- the repeatable run_dilutions tank list below.
    dilution_fill_level_tank_6ab_l  REAL,
    dilution_measured_ph            REAL,
    dilution_citric_kg              REAL,
    dilution_ksorbate_stock_pct     REAL,
    dilution_ksorbate_added_l       REAL,
    dilution_nabenzoate_stock_pct   REAL,
    dilution_nabenzoate_added_l     REAL,
    -- Packaging stage. packaging_started_at stays defined (additive-only)
    -- but is no longer collected -- "Packaging started at" was removed in
    -- favor of packaging_packaged_at, the repeatable table's shared
    -- "Packaging date and time" (see run_packaging_entries above).
    packaging_started_at          TEXT,
    packaging_packaged_at         TEXT,
    -- Packaging, Quality Check (subtitle "LKE characterization") -- Liquid-only fields.
    packaging_qc_ph               REAL,
    packaging_tds_pct             REAL,
    packaging_brix_pct            REAL,
    packaging_mannitol_pct        REAL,
    packaging_ts_liquid_pct       REAL,
    packaging_rho_liquid_g_ml     REAL,
    -- Packaging, Sample Point box (subtitle "LKE characterization"): one
    -- collection date/time shared by every row in that box's
    -- run_sample_points rows.
    packaging_sample_collected_at TEXT,
    rejected_feedstock_json       TEXT,  -- JSON [{toteLot, reason, at}] for inspected-but-rejected totes
    created_at     TEXT NOT NULL
);

-- Feedstock characterization: one row per tote fed into a run (receiving inspection).
CREATE TABLE IF NOT EXISTS run_inputs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    tote_lot_id      INTEGER NOT NULL REFERENCES tote_lots(id),
    loaded_at        TEXT,
    surface_photo    TEXT,      -- stored filename (run_attachments-style, on disk in UPLOAD_DIR)
    striation_photo  TEXT,
    ph               REAL,
    ph_measured_at   TEXT,      -- client-stamped moment the pH reading was entered
    orp              REAL,      -- mV
    orp_range        TEXT,      -- ORP classification, calculated from REF_ORP_classification
    odour            TEXT,      -- comma-joined; multiple odours may be selected
    odour_other      TEXT,
    odour_intensity  TEXT,      -- Mild | Medium | Strong
    weight_kg        REAL,      -- this tote's weight as measured for this run
    volume_l         REAL,      -- this tote's volume as measured for this run
    density_kg_l     REAL,      -- calculated: weight_kg / volume_l
    decision         TEXT NOT NULL DEFAULT 'accepted',  -- accepted | rejected
    decision_set     INTEGER NOT NULL DEFAULT 0,        -- 1 once an operator explicitly chose
    rejection_reason TEXT,
    notes            TEXT
);

-- Repeatable Separation solids collections (a run may have several).
CREATE TABLE IF NOT EXISTS run_separation_solids (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    weight_kg REAL,
    photo     TEXT,
    notes     TEXT,
    logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sepsolids_run ON run_separation_solids(run_id);

-- Repeatable Dilution & Preservation entries (a run may split output across tanks).
CREATE TABLE IF NOT EXISTS run_dilutions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    tank                   TEXT,
    volume_initial_l       REAL,
    water_required_l       REAL,
    volume_final_l         REAL,
    sorbate_required_kg    REAL,
    benzoate_required_kg   REAL,
    preservatives_added    INTEGER NOT NULL DEFAULT 0,
    preservatives_added_at TEXT,
    citric_kg              REAL,
    samples_taken          INTEGER NOT NULL DEFAULT 0,
    samples_taken_at       TEXT,
    notes                  TEXT,
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dilutions_run ON run_dilutions(run_id);

-- Repeatable Sample Point entries (Homogenization Output "Sample Point" box) --
-- a run may collect any number of samples, each its own row/container. The
-- box's single "Collection date and time" applies to every row printed from
-- it, so that lives on production_runs (homog_sample_collected_at) instead.
CREATE TABLE IF NOT EXISTS run_sample_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    stage         TEXT,     -- which Sample Point box this row belongs to, e.g.
                            -- 'homogenization', 'separation_solids' (a run can
                            -- have more than one Sample Point box)
    type          TEXT,     -- Slurry | Liquid | Solid
    description   TEXT,     -- Microbial | Retention | Metals & Nutrients | Proximate Analysis | R&D | Other
    label_type    TEXT,     -- detailed | simplified: which sample label this row prints (default detailed)
    label_numbered INTEGER NOT NULL DEFAULT 0,   -- 1 = its labels / sample IDs carry -1, -2, -3 ... (unit number)
    label_name    TEXT,     -- name printed on the label's second line when the user changed it (NULL = the default for the stage)
    qty           INTEGER DEFAULT 1,   -- 1-10; also the number of labels printed for this row
    container     TEXT,     -- 50 mL falcon tube | 100 g sample bag | 1 L bottle | 2 L bottle
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samplepoints_run ON run_sample_points(run_id);

-- Repeatable Packaging entries (Packaging "table" -- add/remove rows, same
-- format as Sample Point): one row per container-unit/qty logged during
-- bottling. The box's single "Packaging date and time" applies to every row,
-- so that lives on production_runs (packaging_packaged_at) instead -- same
-- shared-timestamp pattern as the Sample Point boxes.
CREATE TABLE IF NOT EXISTS run_packaging_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    container_unit TEXT,
    qty            REAL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packagingentries_run ON run_packaging_entries(run_id);

-- Freely adding/editing/removing Packaging table rows never touches
-- container stock by itself -- only committing (the Packaging section's
-- Save button, or finalize) does, and only for the *net* change per
-- container since the last commit, as one consumable_txns line each (see
-- _commit_packaging_stock). This table remembers what was last committed
-- per (run, container) so that net change can be computed; a container
-- untouched since the last commit needs no new entry at all.
CREATE TABLE IF NOT EXISTS run_packaging_commits (
    run_id         INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    container_unit TEXT NOT NULL,
    committed_qty  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, container_unit)
);

-- FG labels follow the container: whenever the Packaging commit consumes N of
-- a container, the label item mapped to (run SKU, that container) is consumed
-- N too. Tracked per label item (not per container) so changing the run's SKU
-- before finalize refunds the old SKU's labels and deducts the new one's.
CREATE TABLE IF NOT EXISTS run_label_commits (
    run_id         INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    consumable_id  INTEGER NOT NULL REFERENCES consumables(id),
    committed_qty  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, consumable_id)
);

-- Same net-change commit model for the reagents (Citric Acid, Potassium
-- Sorbate, Sodium Benzoate) logged under Dilution & Preservation: saving that
-- section (or finalizing) deducts only the change in kg since the last
-- commit, one consumable_txns line per reagent (see _commit_reagent_usage);
-- discarding a draft refunds exactly what was committed. reagent is the
-- consumable's name.
CREATE TABLE IF NOT EXISTS run_reagent_commits (
    run_id         INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    reagent        TEXT NOT NULL,         -- the reagent TYPE (Citric Acid / Potassium Sorbate / Sodium Benzoate)
    committed_kg   REAL NOT NULL DEFAULT 0,
    consumable_id  INTEGER,               -- the inventory item the kg were deducted from (NULL = the item named like the type)
    PRIMARY KEY (run_id, reagent)
);

-- CIP (Clean In Place) log: one row per cleaning event, with the chemicals
-- used as lines on it. The lines are the single source of truth for both the
-- cleaning record and reagent consumption -- saving/editing/deleting an event
-- adjusts the CIP agents' stock by the difference (see _apply_cip_stock).
-- Independent of production runs (a CIP happens between runs, on equipment);
-- run_id is an optional "cleaned after/before this run" link.
CREATE TABLE IF NOT EXISTS cip_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    cip_ref               TEXT NOT NULL UNIQUE,   -- CIP-YYYYMMDD-NNN
    started_at            TEXT NOT NULL,
    ended_at              TEXT,
    equipment             TEXT NOT NULL,          -- process area / circuit cleaned (free text)
    purpose               TEXT,                   -- Post-run | Pre-run | Changeover | Scheduled | Other
    run_id                INTEGER REFERENCES production_runs(id) ON DELETE SET NULL,
    operators             TEXT,
    rinse_ph              REAL,                   -- no longer collected (additive-only; kept, unused)
    rinse_conductivity_us REAL,                   -- no longer collected (additive-only; kept, unused)
    result                TEXT,                   -- pass | fail
    notes                 TEXT,
    created_by            TEXT,
    created_at            TEXT NOT NULL,
    updated_by            TEXT,
    updated_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_cip_started ON cip_events(started_at);
CREATE TABLE IF NOT EXISTS cip_event_chemicals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cip_event_id   INTEGER NOT NULL REFERENCES cip_events(id) ON DELETE CASCADE,
    consumable_id  INTEGER NOT NULL REFERENCES consumables(id),
    qty            REAL NOT NULL,                 -- in the item's own unit (L)
    concentration_pct REAL,
    temp_c         REAL,
    contact_min    REAL
);
CREATE INDEX IF NOT EXISTS idx_cipchem_event ON cip_event_chemicals(cip_event_id);

-- Admin-editable options for the Packaging table's "Container unit" dropdown,
-- each mapped to a fixed litres-per-unit conversion (e.g. IBC = 1000 L) --
-- distinct from the fixed IBC/4L/1L/250ml package_size_*_l settings used by
-- the Bottling/packaging output grid at finalize.
-- Superseded by the consumables.is_container/litres_each/is_sample_container
-- columns above -- container types now live entirely in consumables (the
-- Packaging section), so an admin can track on-hand/reorder/cost/location
-- for them like any other consumable. This table stays defined (additive-
-- only) purely so migrate() can copy any rows an admin already added here
-- into consumables on upgrade; nothing else reads or writes it anymore.
CREATE TABLE IF NOT EXISTS container_units (
    code        TEXT PRIMARY KEY,
    litres_each REAL NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    is_ibc      INTEGER NOT NULL DEFAULT 0
);

-- Admin-editable numeric constants used by calculated fields throughout the
-- app (see the Calculations page) -- e.g. ORP classification thresholds,
-- package litre sizes, calculation defaults. Never read a "magic number"
-- straight from code for a calculation the Calculations page documents;
-- add a row here instead so an admin can tune it without a redeploy.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       REAL NOT NULL,
    label       TEXT NOT NULL,
    description TEXT,
    updated_at  TEXT
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
    status        TEXT NOT NULL DEFAULT 'on_hand',  -- pending_release | on_hand | hold | sold | disposed
    created_at    TEXT NOT NULL
);

-- Product release (review + QA sign-off) audit trail. APPEND-ONLY: rows are
-- never updated or deleted by the app, and every row carries the hash of the
-- row before it (prev_hash -> entry_hash), so any later tampering with the
-- history breaks the chain and shows up in the "Verify audit trail" check.
-- log_hash is the SHA-256 of the run's production-log snapshot at the moment
-- of the event; a release is only valid for exactly the log that was reviewed.
CREATE TABLE IF NOT EXISTS release_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    event_type   TEXT NOT NULL,     -- submitted | review_approved | review_returned | resubmitted |
                                    -- released | release_rejected | reopened | voided | legacy_release |
                                    -- fg_lot_edited | lab_result_hold
    user_id      INTEGER,
    user_name    TEXT,
    user_email   TEXT,
    capacity     TEXT,              -- Production Manager | Quality Manager | System
    meaning      TEXT,              -- what the signature attests to
    comment      TEXT,
    log_hash     TEXT,
    detail       TEXT,              -- JSON: from/to state, lots affected, trigger, ...
    created_at   TEXT NOT NULL,
    prev_hash    TEXT NOT NULL DEFAULT '',
    entry_hash   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_events_run ON release_events(run_id);

-- Additional dilution passes (2, 3, ...): when product is left in Tank 5A/5B after the first
-- pass (which is the run-level pasteurization_* / dilution_* columns), each further pass
-- records its own plan (5A/5B levels, receiving tank(s), starting levels, TDS), actuals
-- (product transferred, water added, final level per tank) and per-tank preservative
-- additions. The run-level reagent totals are the sum over all passes (see
-- Handler._recompute_dilution_totals), so reagent deduction is unchanged.
CREATE TABLE IF NOT EXISTS run_dilution_passes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    pass_no                 INTEGER NOT NULL,
    tank5a_l                REAL, tank5b_l REAL,
    receiving               TEXT,              -- 6A | 6B | 6AB
    tank6a_start_l          REAL, tank6b_start_l REAL,
    tds_pct                 REAL,              -- Separation filtrate TDS used by this pass's plan
    max_transfer_l          REAL, transfer_rec_l REAL, water_rec_l REAL,
    product_transferred_l   REAL, water_added_l REAL,
    tank6a_final_l          REAL, tank6b_final_l REAL,
    ksorbate_added_l_6a     REAL, ksorbate_added_l_6b REAL,
    nabenzoate_added_l_6a   REAL, nabenzoate_added_l_6b REAL,
    final_variance_pct      REAL,
    measured_ph             REAL,              -- pH balancing for this pass
    citric_kg               REAL,              -- citric acid added in this pass (counts toward the run's citric usage)
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dilpasses_run ON run_dilution_passes(run_id);

-- Sample catalogue: every tube / bag logged in a finalized run's Sample Point boxes becomes one row here
-- (a Sample Point row of qty 4 -> 4 samples), kept in sync with the run's sample rows by sync_samples().
-- status: available (in inventory) | in_cart (assigned for lab analysis, requisition not yet created) |
--         submitted (sent to a lab on a requisition) | removed (consumed / disposed / expired / lost).
CREATE TABLE IF NOT EXISTS samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_code     TEXT NOT NULL UNIQUE,          -- <processing lot>-<STG>-<NN>, e.g. PR-20261006-053-HOM-03
    short_id        TEXT,                           -- (retired: an earlier numbering scheme; no longer used)
    id_detailed     TEXT,                           -- Sample ID Detailed = first line of the Detailed label: <processing lot>[-<unit no>]
    id_simplified   TEXT,                           -- Sample ID Simplified = first line of the Simplified label: Lot-<last 3 digits>[-<unit no>]
    label_type      TEXT,                           -- detailed | simplified: the label this sample's point prints; picks which ID is its display ID
    run_id          INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    sample_point_id INTEGER,                        -- run_sample_points.id this unit came from
    unit_no         INTEGER,                        -- 1..qty within that row
    stage           TEXT,
    type            TEXT,                           -- Slurry | Liquid | Solid
    description     TEXT,                           -- Microbial | Retention | Metals & Nutrients | ...
    container       TEXT,
    collected_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'available',
    location        TEXT,                           -- where the physical sample is stored
    notes           TEXT,
    removed_at      TEXT, removed_by TEXT, removed_reason TEXT,
    requisition_id  INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_run ON samples(run_id);
CREATE INDEX IF NOT EXISTS idx_samples_status ON samples(status);
-- Append-only history of everything that happens to a sample.
CREATE TABLE IF NOT EXISTS sample_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id  INTEGER NOT NULL,
    event_type TEXT NOT NULL,                       -- created | cart_add | cart_remove | assigned | requisition | removed | restored | location | note
    detail     TEXT,
    user_name  TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sample_events_sample ON sample_events(sample_id);
-- Labs and the analyses each can perform (admin-maintained), plus an optional .docx requisition template per lab.
CREATE TABLE IF NOT EXISTS labs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    contact         TEXT, email TEXT, phone TEXT, address TEXT, notes TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    template_name   TEXT,                           -- original filename of the uploaded requisition template
    template_stored TEXT,                           -- opaque name on disk under LAB_DIR
    sample_sheet    INTEGER NOT NULL DEFAULT 0,     -- 1 = also generate the "attached spreadsheet" (sample ID / description / tests per sample)
    merge_ids       INTEGER NOT NULL DEFAULT 0,     -- 1 = samples sharing a Sample ID are ONE line on the form and the sheet (container qty / total volume add up)
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_analyses (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_id  INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    code    TEXT, notes TEXT,
    method  TEXT,                                   -- e.g. "ICP-MS": printed in a form's Specifications / Methods column
    active  INTEGER NOT NULL DEFAULT 1,
    category TEXT,                                  -- a group shown in the cart with a select-all, e.g. "CFIA Heavy Metals"
    kind    TEXT NOT NULL DEFAULT 'analysis',       -- analysis | mineral (counts toward a mineral scan) | scan (added automatically from the mineral count)
    symbol  TEXT,                                   -- element symbol of a mineral (As, Ca ...)
    capacity INTEGER,                               -- scan only: the most minerals this scan covers (12, 20)
    req_note TEXT                                   -- text added to the requisition Notes whenever this analysis is requested
);
-- One-time seeding markers (so an analysis an admin deletes is not recreated at the next start).
CREATE TABLE IF NOT EXISTS app_flags (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_analyses_lab ON lab_analyses(lab_id);
-- The customer (our) contact details printed on lab requisition forms: {{customer_phone}} and {{customer_email_1}} .. {{customer_email_5}}.
CREATE TABLE IF NOT EXISTS requisition_contact (
    key   TEXT PRIMARY KEY,                         -- phone | email_1 .. email_5 | submit_name/phone/email | results_name/phone/email_1 .. email_5
    value TEXT NOT NULL DEFAULT ''
);
-- Control charts (Quality Control tab): the control limits a user set by hand for one measurement at one process section, plus an
-- append-only log of every change (who / when / what), because limits decide what counts as "out of control".
CREATE TABLE IF NOT EXISTS qc_chart_limits (
    metric  TEXT NOT NULL,                          -- qc:ph | feed:orp | lab:apc ...
    section TEXT NOT NULL,                          -- homogenization | extraction | separation_filtrate | ... | lab
    lcl REAL, ucl REAL, center REAL, note TEXT,
    set_by TEXT, set_at TEXT,
    PRIMARY KEY (metric, section)
);
CREATE TABLE IF NOT EXISTS qc_chart_limit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL, section TEXT NOT NULL,
    lcl REAL, ucl REAL, center REAL, note TEXT,
    set_by TEXT, set_at TEXT NOT NULL
);
-- Names printed on the sample labels, per sample-point stage, where the user has changed the built-in default.
CREATE TABLE IF NOT EXISTS sample_label_names (
    stage TEXT PRIMARY KEY,
    name  TEXT NOT NULL
);
-- The cart: samples (from finalized runs) waiting for a lab + analyses and a requisition.
CREATE TABLE IF NOT EXISTS sample_cart (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL UNIQUE,
    lab_id    INTEGER,
    analyses  TEXT,                                 -- JSON list of lab_analyses ids
    added_by  TEXT,
    added_at  TEXT NOT NULL
);
-- A generated lab requisition (one per run + lab); the filled document is stored as that run's attachment.
CREATE TABLE IF NOT EXISTS lab_requisitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    req_number    TEXT NOT NULL UNIQUE,             -- REQ-YYYYMMDD-NNN
    run_id        INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    lab_id        INTEGER,
    lab_name      TEXT,
    attachment_id INTEGER,
    notes         TEXT,
    created_by    TEXT,
    created_at    TEXT NOT NULL,
    po_number     TEXT,                             -- PO / reference number entered when the requisition was created
    sheet_attachment_id INTEGER                     -- the companion sample spreadsheet (labs with sample_sheet=1)
);
CREATE TABLE IF NOT EXISTS requisition_samples (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id INTEGER NOT NULL REFERENCES lab_requisitions(id) ON DELETE CASCADE,
    sample_id      INTEGER NOT NULL,
    analyses       TEXT                             -- JSON list of analysis names as requested
);

-- Certificate of Analysis: product specifications (admin-editable, seeded by ensure_coa_specs) and the lab results entered per run.
-- A result is never edited or deleted: a correction voids it (with a reason) and a new one is entered.
CREATE TABLE IF NOT EXISTS coa_specs (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    grp           TEXT NOT NULL,                    -- physical | metals | microbial
    unit          TEXT,                             -- unit the limit is stated in (kg/ha for metals, cfu/g ...)
    basis         TEXT NOT NULL,                    -- run | value | metal | absent
    min_val       REAL, max_val REAL,
    limit_kg_ha   REAL,                             -- metals: the regulatory loading limit (kg metal / ha) the ppm limit is derived from
    max_exclusive INTEGER NOT NULL DEFAULT 0,       -- 1 = the limit is "< max"
    required      INTEGER NOT NULL DEFAULT 0,       -- 1 = a result must be on file before Quality can release the lot
    sort          INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    method        TEXT
);
CREATE TABLE IF NOT EXISTS lab_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    spec_code     TEXT,                             -- NULL = an additional analysis with no specification
    analyte       TEXT NOT NULL,
    lab_id        INTEGER, lab_name TEXT,
    report_number TEXT, report_date TEXT, sample_ref TEXT,
    method        TEXT,
    qualifier     TEXT,                             -- '' | '<' | '>'
    value_num     REAL,                             -- NULL for a qualitative result (Negative / Positive)
    value_text    TEXT,
    unit          TEXT,
    attachment_id INTEGER,                          -- the uploaded lab report this came from
    entered_by    TEXT, entered_at TEXT NOT NULL,
    voided_at     TEXT, voided_by TEXT, void_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_results_run ON lab_results(run_id);

-- Pre-Processing: coarse-ground feedstock totes are pulled (pick list), shredded to a fine grind,
-- blended in a tank (solids loading set with dilution water, pH set with citric acid) and packed
-- into new IBCs that go back into Feedstock Inventory (tote_lots.grind = 'Fine'). The output lots
-- trace to their source totes through preproc_inputs.
CREATE TABLE IF NOT EXISTS preproc_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_lot           TEXT NOT NULL UNIQUE,   -- BL-YYYYMMDD-NNN (output lots are <batch_lot>-NN)
    status              TEXT NOT NULL DEFAULT 'draft',   -- draft | completed
    batch_date          TEXT,
    location            TEXT,                   -- where the output IBCs are stored
    operators           TEXT,
    shredder            TEXT,
    notes               TEXT,
    shredded_kg         REAL,                   -- measured shredded mass (defaults to the sum of input weights)
    start_solids_pct    REAL,                   -- measured % solids of the shredded mass
    target_solids_pct   REAL,
    recommended_water_l REAL,                   -- calculated by the plan (stored when saved)
    water_added_l       REAL,
    blend_volume_l      REAL,                   -- measured volume of the blended batch
    final_solids_pct    REAL,                   -- measured % solids after dilution
    measured_ph         REAL,
    target_ph           REAL,
    citric_kg           REAL,
    citric_item_id      INTEGER,                -- which Citric Acid inventory item was used
    created_by          TEXT,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    completed_by        TEXT
);
CREATE TABLE IF NOT EXISTS preproc_inputs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES preproc_batches(id) ON DELETE CASCADE,
    tote_lot_id INTEGER NOT NULL REFERENCES tote_lots(id),
    weight_kg   REAL,                           -- this tote's weight (starts as the stored average)
    volume_l    REAL                            -- this tote's volume (L)
);
CREATE INDEX IF NOT EXISTS idx_preproc_inputs_batch ON preproc_inputs(batch_id);
CREATE TABLE IF NOT EXISTS preproc_packaging (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES preproc_batches(id) ON DELETE CASCADE,
    container   TEXT,                           -- consumables.name of the empty IBC used
    qty         INTEGER,
    litres_each REAL                            -- fill volume of each
);

-- Revision tracker for a finalized run's production log. Rev 1 is the
-- finalized record; every later change to the log (any section, via any
-- endpoint) adds a revision holding a field-level diff (old -> new) and who
-- made it. Append-only; runs finalized before this existed get a synthetic
-- "original record" Rev 1 when displayed.
CREATE TABLE IF NOT EXISTS run_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    rev_no      INTEGER NOT NULL,
    kind        TEXT NOT NULL,      -- finalized | edit
    user_name   TEXT,
    created_at  TEXT NOT NULL,
    summary     TEXT,
    changes     TEXT,               -- JSON [{field, old, new}]
    log_hash    TEXT,
    category    TEXT,               -- amendment category (kind='amendment')
    reason      TEXT,               -- why the log was amended
    amendment_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_run_revisions_run ON run_revisions(run_id);

-- Records archive: the folder a finalized run was given in the records library the first time it was archived. It never changes afterwards (an
-- amendment of the run's date or product must not move its folder).
CREATE TABLE IF NOT EXISTS archive_runs (
    run_id     INTEGER PRIMARY KEY REFERENCES production_runs(id) ON DELETE CASCADE,
    folder     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- A finalized run's production log is locked; changing it requires an
-- amendment (reason + category). While open the run is 'amending' and its
-- unsold finished goods are held. prior_* lets a no-change amendment be
-- cancelled back to exactly where it was; start_snapshot is the log as it was
-- when the amendment opened (the diff base for the revision it produces).
CREATE TABLE IF NOT EXISTS run_amendments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    status            TEXT NOT NULL,        -- open | submitted | cancelled
    category          TEXT NOT NULL,
    reason            TEXT NOT NULL,
    opened_by         TEXT,
    opened_by_id      INTEGER,
    opened_at         TEXT NOT NULL,
    prior_state       TEXT,
    prior_review_hash TEXT,
    prior_lots        TEXT,
    start_snapshot    TEXT,
    start_hash        TEXT,
    was_complete      INTEGER NOT NULL DEFAULT 0,
    closed_by         TEXT,
    closed_at         TEXT,
    submit_comment    TEXT,
    revision_no       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_run_amendments_run ON run_amendments(run_id);

-- Who granted/removed the Production Manager / Quality Manager sign-off
-- permissions, and when (the permissions decide who may sign).
CREATE TABLE IF NOT EXISTS user_permission_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    user_email  TEXT,
    permission  TEXT NOT NULL,
    old_value   INTEGER,
    new_value   INTEGER,
    changed_by  TEXT,
    changed_at  TEXT NOT NULL
);
"""


def now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today_iso():
    return datetime.date.today().isoformat()


def lot_number_for(created_at, rid):
    """A production run's processing lot is reserved the instant its row is
    first inserted (draft creation, or one-shot finalize) and derived from
    that row's own autoincrement id — never recomputed later, so numbers
    stay dense/unique even with several runs started concurrently."""
    date_part = (created_at or now_iso())[:10].replace("-", "")
    return "PR-%s-%03d" % (date_part, rid)


QAQC_HOLD_LOCATION = "QAQC Hold"


def status_for_location(location, current_status):
    """A tote sitting at QAQC Hold is always status='hold'; moving it away
    releases it back to 'in_stock'. Never touches a consumed/disposed tote."""
    if current_status not in ("in_stock", "hold"):
        return current_status
    if location == QAQC_HOLD_LOCATION:
        return "hold"
    if current_status == "hold":
        return "in_stock"
    return current_status


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)          # wait for a competing writer instead of failing after 5 s
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


INFLIGHT = [0]                                    # requests being handled right now (for a clean stop)
INFLIGHT_LOCK = threading.Lock()
SHUTDOWN_WAIT_SECONDS = 15
logger = logging.getLogger("kelpworks")
_TOKEN_IN_URL = re.compile(r"(token=)[^&\s]+")


def redact(text):
    """Never write a login token (they travel in ?token= for downloads) to the log."""
    return _TOKEN_IN_URL.sub(r"\1REDACTED", str(text))


def configure_logging():
    try:
        sys.stderr.reconfigure(errors="backslashreplace")       # the Windows console is cp1252: never crash on a character
    except (AttributeError, OSError):
        pass
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


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


# ---- login throttling (single instance: in memory is enough) ----
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILS_PER_ACCOUNT = 8          # failed attempts for one email from one address within the window
LOGIN_MAX_FAILS_PER_ADDRESS = 40         # failed attempts from one address (any email) within the window
LOGIN_FAILS = {}
LOGIN_LOCK = threading.Lock()
_DUMMY_HASH = []


def login_wait_seconds(ip, email):
    """Seconds this address must wait before another sign-in attempt (0 = allowed)."""
    now = time.time()
    with LOGIN_LOCK:
        wait = 0
        for key, limit in (((ip, email), LOGIN_MAX_FAILS_PER_ACCOUNT), ((ip, None), LOGIN_MAX_FAILS_PER_ADDRESS)):
            fails = [t for t in LOGIN_FAILS.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
            LOGIN_FAILS[key] = fails
            if len(fails) >= limit:
                wait = max(wait, int(LOGIN_WINDOW_SECONDS - (now - fails[0])) + 1)
        return wait


def login_failed(ip, email):
    now = time.time()
    with LOGIN_LOCK:
        for key in ((ip, email), (ip, None)):
            LOGIN_FAILS.setdefault(key, []).append(now)
        if len(LOGIN_FAILS) > 5000:                    # keep the table small
            for key in [k for k, v in LOGIN_FAILS.items() if not v or now - v[-1] > LOGIN_WINDOW_SECONDS]:
                LOGIN_FAILS.pop(key, None)


def login_succeeded(ip, email):
    with LOGIN_LOCK:
        LOGIN_FAILS.pop((ip, email), None)


def dummy_verify(password):
    """Spend the same time as a real password check, so an unknown email cannot be told from a wrong password by timing."""
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(hash_password("not-a-real-password"))
    verify_password(password, _DUMMY_HASH[0])


def run_once(conn, key, fn):
    """Run a one-time data change exactly once (recorded in app_flags, in the same transaction as the change). Anything that would otherwise
    run at EVERY start and overwrite what an administrator has since set belongs here."""
    flag = "migrated_" + key
    if conn.execute("SELECT 1 FROM app_flags WHERE key=?", (flag,)).fetchone():
        return False
    fn()
    conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES (?,?)", (flag, now_iso()))
    return True


def soft_step(conn, label, fn):
    """A repair or backfill that must not stop the server from starting: if it fails its own changes are rolled back (SAVEPOINT), the failure
    is logged with its traceback, and the rest of the start carries on. It is tried again at the next start."""
    conn.execute("SAVEPOINT soft_step")
    try:
        fn()
        conn.execute("RELEASE soft_step")
    except Exception:
        conn.execute("ROLLBACK TO soft_step")
        conn.execute("RELEASE soft_step")
        logger.error("Start-up step '%s' failed and was skipped (it will be tried again at the next start).", label, exc_info=True)


# Extra indexes for columns the app filters on that are not foreign keys: (table, column)
EXTRA_INDEXES = [("consumable_txns", "ref"), ("fg_lots", "status"), ("production_runs", "status")]


def ensure_indexes(conn):
    """Index every foreign-key column that has no index of its own (SQLite does not do it): deleting or updating a parent row, and every
    "rows of this run / tote / lot" lookup, otherwise scans the whole child table. Generic on purpose, so a table added later is covered
    without remembering it (tests/test_integrity_cleanup.py fails if a foreign key is left unindexed). Safe to repeat."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        led = set()                                         # columns that already lead an index (or are the single-column primary key)
        for idx in conn.execute("PRAGMA index_list(%s)" % t).fetchall():
            cols = conn.execute("PRAGMA index_info(%s)" % idx[1]).fetchall()
            if cols:
                led.add(cols[0][2])
        pk = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t) if r[5]]
        if len(pk) == 1:
            led.add(pk[0])
        for fk in conn.execute("PRAGMA foreign_key_list(%s)" % t).fetchall():
            col = fk[3]
            if col not in led:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_fk_%s_%s ON %s(%s)" % (t, col, t, col))
                led.add(col)
    for t, col in EXTRA_INDEXES:
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}
        if col in have:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_x_%s_%s ON %s(%s)" % (t, col, t, col))


# The values a status column may hold. SQLite cannot add a CHECK constraint to a table that already exists, so these are enforced with triggers
# (which can be added to a live database); a request that would store anything else is refused (409) and rolled back.
STATUS_RULES = [
    ("tote_lots", ("in_stock", "wip", "hold", "consumed", "disposed")),
    ("fg_lots", ("pending_release", "on_hand", "hold", "sold", "disposed")),
    ("production_runs", ("draft", "completed")),
    ("preproc_batches", ("draft", "completed")),
]


def ensure_constraints(conn):
    """Triggers that keep bad values out of the columns the release and stock rules depend on: an unknown status, a negative finished-goods
    quantity. They only look at what a write CHANGES, so an old row that already holds an odd value never blocks an unrelated edit."""
    for table, allowed in STATUS_RULES:
        listed = ", ".join("'%s'" % v for v in allowed)
        message = "Not allowed: %s.status must be one of %s" % (table, ", ".join(allowed))
        conn.execute("CREATE TRIGGER IF NOT EXISTS chk_%s_status_ins BEFORE INSERT ON %s WHEN NEW.status NOT IN (%s) "
                     "BEGIN SELECT RAISE(ABORT, '%s'); END" % (table, table, listed, message))
        conn.execute("CREATE TRIGGER IF NOT EXISTS chk_%s_status_upd BEFORE UPDATE OF status ON %s "
                     "WHEN NEW.status IS NOT OLD.status AND NEW.status NOT IN (%s) BEGIN SELECT RAISE(ABORT, '%s'); END"
                     % (table, table, listed, message))
    conn.execute("CREATE TRIGGER IF NOT EXISTS chk_fg_lots_qty_ins BEFORE INSERT ON fg_lots WHEN NEW.qty < 0 "
                 "BEGIN SELECT RAISE(ABORT, 'Not allowed: finished-goods units cannot be negative'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS chk_fg_lots_qty_upd BEFORE UPDATE OF qty ON fg_lots WHEN NEW.qty < 0 AND NEW.qty < OLD.qty "
                 "BEGIN SELECT RAISE(ABORT, 'Not allowed: finished-goods units cannot be negative'); END")


def code_fingerprint():
    """A short fingerprint of the server code: it changes whenever a deploy changes kelp_erp_server.py (and so possibly the migrations)."""
    with open(os.path.abspath(__file__), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def backups_dir():
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")


MIGRATION_SNAPSHOTS_KEPT = 3


def snapshot_before_migrating(conn, stamp=None):
    """Copy the database (compressed, with sqlite's backup API) into backups/ next to it BEFORE a new version of the code migrates it, so a bad
    migration can be undone by restoring the file. Skipped when the disk is too full to do it safely. Returns the file path or None."""
    try:
        size = os.path.getsize(DB_PATH)
        folder = backups_dir()
        os.makedirs(folder, exist_ok=True)
        if shutil.disk_usage(folder).free < 3 * size + 50 * 1024 * 1024:
            logger.warning("Not enough free disk for a pre-migration snapshot of the database (%.1f MB); continuing without one.", size / 1048576.0)
            return None
        stamp = stamp or datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        raw_copy = os.path.join(folder, "snapshot-%s.tmp" % stamp)
        target = os.path.join(folder, "pre-migrate-%s.db.gz" % stamp)
        dst = sqlite3.connect(raw_copy)
        try:
            conn.backup(dst)
        finally:
            dst.close()
        with open(raw_copy, "rb") as src, gzip.open(target, "wb", compresslevel=6) as out:
            shutil.copyfileobj(src, out, 1 << 20)
        os.remove(raw_copy)
        for old in sorted(f for f in os.listdir(folder) if f.startswith("pre-migrate-") and f.endswith(".db.gz"))[:-MIGRATION_SNAPSHOTS_KEPT]:
            os.remove(os.path.join(folder, old))
        logger.info("Database snapshot before migrating: %s", target)
        return target
    except Exception:
        logger.error("Could not take the pre-migration snapshot; continuing without one.", exc_info=True)
        return None


def ensure_reagent_types(conn):
    """The three standard reagents carry their reagent type. migrate() sets it when the column is added, which on a NEW database is before
    the reagents exist (seed() inserts them afterwards), so this runs again after seeding. Only fills a blank type."""
    for t in ("Citric Acid", "Potassium Sorbate", "Sodium Benzoate"):
        conn.execute("UPDATE consumables SET reagent_type=? WHERE name=? AND reagent_type IS NULL AND COALESCE(is_container,0)=0 "
                     "AND label_sku_code IS NULL AND COALESCE(is_cip_agent,0)=0", (t, t))


def init_db():
    """Create / upgrade the database. The schema statements are idempotent; everything after them (migrations, seed, the ensure_* steps) runs in ONE
    transaction: a failure rolls the whole upgrade back (SQLite DDL is transactional), so a start that dies or is killed half-way leaves the database exactly
    as it was and the next start redoes the work. Repairs that must not block a start run as soft steps. Before new code migrates an existing
    database, the database is copied to backups/ (see snapshot_before_migrating)."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = db()
    try:
        conn.executescript(SCHEMA)
        fingerprint = code_fingerprint()
        known = conn.execute("SELECT value FROM app_flags WHERE key='code_fingerprint'").fetchone()
        has_data = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] > 0
        if has_data and (not known or known["value"] != fingerprint):
            snapshot_before_migrating(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            migrate(conn)
            if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
                seed(conn)
                migrate(conn)          # migrate() is idempotent: the steps that need the seeded reference data (SKU -> species links) now apply
            ensure_users(conn)
            assign_item_numbers(conn)
            ensure_coa_specs(conn)
            ensure_requisition_contact(conn)
            ensure_sgs_analyses(conn)
            ensure_reagent_types(conn)
            soft_step(conn, "foreign-key indexes", lambda: ensure_indexes(conn))
            soft_step(conn, "status and quantity guards", lambda: ensure_constraints(conn))
            soft_step(conn, "re-hash signed production logs", lambda: rebaseline_release_hashes(conn))
            soft_step(conn, "sample catalogue for finalized runs", lambda: sync_pending_samples(conn))

            def sample_defaults():
                # a Sample Point row never edited keeps blank type/description in the log while the dropdowns show Slurry / Microbial
                conn.execute("UPDATE samples SET description='Microbial' WHERE description IS NULL OR description=''")
                conn.execute("UPDATE samples SET type='Slurry' WHERE type IS NULL OR type=''")
            soft_step(conn, "sample defaults", sample_defaults)
            conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES ('code_fingerprint', ?)", (fingerprint,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------
# Reading text out of a lab-report PDF (stdlib only): enough of the PDF format to pull the positioned text of an ordinary
# text-based report (Flate streams, simple TrueType/Type1 fonts or Type0 fonts with a ToUnicode map). A scanned image has no text.
# ---------------------------------------------------------------------------------------
_PDF_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.S)
_PDF_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R\b")


def _inflate(raw, limit):
    """Inflate a Flate stream, giving up (b"") on anything that would expand beyond `limit` or is corrupt."""
    d = zlib.decompressobj()
    try:
        out = d.decompress(raw, limit + 1)
    except zlib.error:
        return b""
    return out if len(out) <= limit else b""


def _pdf_objects(data):
    objs = {}
    budget = 3 * MAX_PDF_STREAM_BYTES
    for m in _PDF_OBJ_RE.finditer(data):
        body = m.group(3)
        stream = None
        k = body.find(b"stream")
        head = body
        if k >= 0 and b"endstream" in body[k:]:
            head = body[:k]
            raw = body[k + 6:body.rfind(b"endstream")]
            raw = raw[2:] if raw.startswith(b"\r\n") else raw[1:] if raw[:1] in (b"\n", b"\r") else raw
            stream = raw
            if b"FlateDecode" in head:
                stream = _inflate(raw, min(MAX_PDF_STREAM_BYTES, budget))
                budget -= len(stream)
        objs[int(m.group(1))] = (head, stream)
    return objs


def _pdf_balanced(text, start):
    """text[start:] begins with '<<' -> the balanced '<<...>>' block."""
    depth, i = 0, start
    while i < len(text) - 1:
        two = text[i:i + 2]
        if two == b"<<":
            depth += 1
            i += 2
        elif two == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return text[start:i]
        else:
            i += 1
    return text[start:]


def _pdf_value(objs, head, key):
    """The value of /Key in a dict's text: a dict (inline or referenced), an array or a bare token, as bytes."""
    m = re.search(rb"/" + key + rb"(?![A-Za-z0-9])\s*", head)
    if not m:
        return None
    rest = head[m.end():]
    r = re.match(rb"(\d+)\s+\d+\s+R\b", rest)
    if r:
        o = objs.get(int(r.group(1)))
        return o[0] if o else None
    if rest[:2] == b"<<":
        return _pdf_balanced(rest, 0)
    if rest[:1] == b"[":
        return rest[:rest.find(b"]") + 1]
    t = re.match(rb"[^\s/<>\[\]]+|/[^\s/<>\[\]]+", rest)
    return t.group(0) if t else None


def _pdf_cmap(stream):
    """ToUnicode CMap -> ({code: text}, bytes per code)."""
    out, width = {}, 1
    s = stream.decode("latin-1")
    cs = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", s)
    if cs:
        width = len(cs.group(1)) // 2
    for blk in re.findall(r"beginbfchar(.*?)endbfchar", s, re.S):
        for a, b in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            out[int(a, 16)] = bytes.fromhex(b).decode("utf-16-be", "replace") if b else ""
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", s, re.S):
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", blk):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            dst = m.group(3)
            if dst.startswith("["):
                for i, h in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", dst)):
                    out[lo + i] = bytes.fromhex(h).decode("utf-16-be", "replace")
            else:
                base = int(dst[1:-1], 16)
                for i in range(hi - lo + 1):
                    out[lo + i] = chr(base + i)
    return out, width


class _PdfFont:
    def __init__(self, objs, head):
        sub = _pdf_value(objs, head, b"Subtype") or b""
        self.type0 = sub == b"/Type0"
        self.map, self.width = {}, 2 if self.type0 else 1
        self.mac = b"MacRomanEncoding" in head
        tu = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", head)
        if tu and objs.get(int(tu.group(1))) and objs[int(tu.group(1))][1]:
            self.map, self.width = _pdf_cmap(objs[int(tu.group(1))][1])
        self.first, self.widths, self.dw, self.cw = 0, [], 1000.0, {}
        wv = _pdf_value(objs, head, b"Widths")
        if wv:
            self.widths = [float(x) for x in re.findall(rb"-?\d+\.?\d*", wv)]
            fc = _pdf_value(objs, head, b"FirstChar")
            self.first = int(fc) if fc and fc.isdigit() else 0
        if self.type0:
            df = re.search(rb"/DescendantFonts\s*\[?\s*(\d+)\s+\d+\s+R", head)
            if df and objs.get(int(df.group(1))):
                dh = objs[int(df.group(1))][0]
                dw = _pdf_value(objs, dh, b"DW")
                self.dw = float(dw) if dw else 1000.0
                w = _pdf_value(objs, dh, b"W") or b""
                for m in re.finditer(rb"(\d+)\s*\[([^\]]*)\]|(\d+)\s+(\d+)\s+(-?\d+\.?\d*)", w):
                    if m.group(1):
                        for i, v in enumerate(re.findall(rb"-?\d+\.?\d*", m.group(2))):
                            self.cw[int(m.group(1)) + i] = float(v)
                    else:
                        for c in range(int(m.group(3)), int(m.group(4)) + 1):
                            self.cw[c] = float(m.group(5))

    def codes(self, b):
        n = self.width
        return [int.from_bytes(b[i:i + n], "big") for i in range(0, len(b) - n + 1, n)]

    def text(self, code):
        if code in self.map:
            return self.map[code]
        if self.type0:
            return ""
        return bytes([code]).decode("mac_roman" if self.mac else "cp1252", "replace")

    def adv(self, code):
        if self.type0:
            return self.cw.get(code, self.dw)
        i = code - self.first
        return self.widths[i] if 0 <= i < len(self.widths) else 500.0


_PDF_TOKEN_RE = re.compile(rb"\s*(?:(\((?:\\.|[^\\()])*(?:\((?:\\.|[^\\()])*\)(?:\\.|[^\\()])*)*\))|<([0-9A-Fa-f\s]*)>|(\[)|(\])|(/[^\s/<>\[\]()]*)|(-?\d*\.?\d+)|([A-Za-z'\"*]+)|(<<|>>))", re.S)


def _pdf_unescape(s):
    out, i = bytearray(), 1
    end = len(s) - 1
    while i < end:
        c = s[i]
        if c == 0x5C:
            i += 1
            n = s[i]
            if 0x30 <= n <= 0x37:
                j = i
                while j < min(i + 3, end) and 0x30 <= s[j] <= 0x37:
                    j += 1
                out.append(int(s[i:j], 8) & 255)
                i = j
                continue
            out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}.get(n, n))
        else:
            out.append(c)
        i += 1
    return bytes(out)


def _pdf_page_items(objs, content, fonts):
    """Positioned text runs [(x, y, x_end, size, text)] of one page's content stream."""
    items = []
    ctm, stack = (1, 0, 0, 1, 0, 0), []
    tm = tlm = (1, 0, 0, 1, 0, 0)
    font, size, lead, hscale = None, 1.0, 0.0, 1.0
    ops, arr = [], None

    def mul(a, b):
        return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
                a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])

    def show(parts):
        nonlocal tm
        if font is None:
            return
        txt, dx = "", 0.0
        trm = mul(tm, ctm)
        sx = trm[0] if trm[0] else 1.0
        for p in parts:
            if isinstance(p, (int, float)):
                if p < -180:
                    txt += " "
                dx -= p / 1000.0 * size * hscale
                continue
            for c in font.codes(p):
                txt += font.text(c)
                dx += font.adv(c) / 1000.0 * size * hscale
        x, y = trm[4], trm[5]
        items.append((x, y, x + dx * abs(sx), size * abs(trm[3] or 1.0), txt))
        tm = mul((1, 0, 0, 1, dx, 0), tm)

    for m in _PDF_TOKEN_RE.finditer(content):
        s, hx, lb, rb_, nm, nu, op, dd = m.groups()
        if s is not None:
            v = _pdf_unescape(s)
        elif hx is not None:
            h = re.sub(rb"\s", b"", hx)
            v = bytes.fromhex((h + b"0" if len(h) % 2 else h).decode())
        elif lb:
            arr = []
            continue
        elif rb_:
            if arr is not None:
                ops.append(arr)
                arr = None
            continue
        elif nm is not None:
            v = nm.decode("latin-1")
        elif nu is not None:
            v = float(nu)
        elif op is not None:
            o = op.decode("latin-1")
            a = [x for x in ops if not isinstance(x, list) or True]
            try:
                if o == "q":
                    stack.append(ctm)
                elif o == "Q" and stack:
                    ctm = stack.pop()
                elif o == "cm" and len(a) >= 6:
                    ctm = mul(tuple(a[-6:]), ctm)
                elif o == "BT":
                    tm = tlm = (1, 0, 0, 1, 0, 0)
                elif o == "Tf" and len(a) >= 2:
                    font, size = fonts.get(a[-2]), float(a[-1])
                elif o == "TL" and a:
                    lead = float(a[-1])
                elif o == "Tz" and a:
                    hscale = float(a[-1]) / 100.0
                elif o in ("Td", "TD") and len(a) >= 2:
                    if o == "TD":
                        lead = -float(a[-1])
                    tlm = mul((1, 0, 0, 1, float(a[-2]), float(a[-1])), tlm)
                    tm = tlm
                elif o == "Tm" and len(a) >= 6:
                    tm = tlm = tuple(float(x) for x in a[-6:])
                elif o == "T*":
                    tlm = mul((1, 0, 0, 1, 0, -lead), tlm)
                    tm = tlm
                elif o == "Tj" and a and isinstance(a[-1], bytes):
                    show([a[-1]])
                elif o == "TJ" and a and isinstance(a[-1], list):
                    show(a[-1])
                elif o in ("'", '"') and a and isinstance(a[-1], bytes):
                    tlm = mul((1, 0, 0, 1, 0, -lead), tlm)
                    tm = tlm
                    show([a[-1]])
            except (ValueError, TypeError, IndexError):
                pass
            ops = []
            continue
        else:
            continue
        if arr is not None:
            arr.append(v)
        else:
            ops.append(v)
    return items


def pdf_text_lines(data):
    """Lines of text (top to bottom, left to right) from every page of a text-based PDF. [] when there is no text."""
    objs = _pdf_objects(data)
    pages = []
    for num, (head, _s) in sorted(objs.items()):
        if re.search(rb"/Type\s*/Page(?![A-Za-z])", head):
            pages.append((num, head))
    out = []
    for num, head in pages:
        res, h = None, head
        for _ in range(6):
            res = _pdf_value(objs, h, b"Resources")
            if res or not re.search(rb"/Parent\s+\d+", h):
                break
            h = objs[int(re.search(rb"/Parent\s+(\d+)", h).group(1))][0]
        fonts = {}
        fd = _pdf_value(objs, res or b"", b"Font") or b""
        for name, ref in re.findall(rb"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R", fd):
            if int(ref) in objs:
                try:
                    fonts["/" + name.decode("latin-1")] = _PdfFont(objs, objs[int(ref)][0])
                except (ValueError, IndexError, KeyError):
                    pass
        cv = _pdf_value(objs, head, b"Contents")
        raw = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", head)
        content = b""
        if raw:
            for ref in _PDF_REF_RE.findall(raw.group(1)):
                if int(ref) in objs and objs[int(ref)][1]:
                    content += objs[int(ref)][1] + b"\n"
        items = _pdf_page_items(objs, content, fonts)
        rows = []
        for it in sorted(items, key=lambda t: -t[1]):
            if rows and abs(rows[-1][0] - it[1]) <= max(1.5, it[3] * 0.3):
                rows[-1][1].append(it)
            else:
                rows.append([it[1], [it]])
        for _y, row in rows:
            line, prev_end = "", None
            for x, _yy, xe, sz, tx in sorted(row, key=lambda t: t[0]):
                if not tx.strip():
                    prev_end = xe if prev_end is not None else prev_end
                    continue
                if prev_end is not None and line and x - prev_end > 0.22 * sz and not line.endswith(" "):
                    line += " "
                line += tx
                prev_end = xe
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                out.append(line)
    return out


# ---------------------------------------------------------------------------------------
# Certificate of Analysis: lab results per run, product specifications, conformance
# ---------------------------------------------------------------------------------------
# ---- Lab report scan: fills the "Add lab report" form from an uploaded PDF (the user always reviews before saving) ----
# analyte name as printed (letters only, lower case) -> spec code. A line is matched by its leading name.
LAB_ANALYTE_ALIASES = {
    "apc": ("aerobicplatecount", "totalplatecount", "aerobiccount", "totalaerobicplatecount", "standardplatecount", "apc"),
    "yeast": ("yeasts", "yeast"),
    "mold": ("molds", "mold", "moulds", "mould"),
    "fecal": ("fecalcoliforms", "fecalcoliform", "faecalcoliforms", "faecalcoliform"),
    "salm": ("salmonellaspp", "salmonella"),
    "as": ("arsenic",), "cd": ("cadmium",), "cr": ("chromium",), "co": ("cobalt",), "cu": ("copper",), "pb": ("lead",),
    "hg": ("mercury",), "mo": ("molybdenum",), "ni": ("nickel",), "se": ("selenium",), "zn": ("zinc",),
    "potash": ("solublepotash", "potash", "k2o"),
}
_LAB_VALUE_RE = re.compile(r"(?<![\w.\-/])([<>]=?\s*)?(\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?)(?![\w.\-/])")
_LAB_QUAL_RE = re.compile(r"\b(Negative|Positive|Absent|Present|Not\s+Detected|ND)\b", re.I)
_LAB_METHOD_RE = re.compile(r"\b[A-Z]{3,}-[A-Z0-9\-]*\d+(?:\s*\([A-Za-z0-9 \-]+\))?")
_LAB_UNIT_RE = re.compile(r"^(ppm|ppb|%|mg/kg|mg/l|ug/g|g/kg|cfu/g|cfu/ml|mpn/g|mpn/ml)(?![A-Za-z0-9/])", re.I)
_LAB_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def lab_parse_date(text):
    t = (text or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})[- ]([A-Za-z]{3})[a-z]*[- ,]+(\d{4})", t)           # 25-Aug-2026
    if m and m.group(2).lower() in _LAB_MONTHS:
        return "%s-%02d-%02d" % (m.group(3), _LAB_MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", t)           # August 20, 2026
    if m and m.group(1).lower() in _LAB_MONTHS:
        return "%s-%02d-%02d" % (m.group(3), _LAB_MONTHS[m.group(1).lower()], int(m.group(2)))
    return ""


def lab_match_alias(line):
    """-> (spec code, matched name length in normalised letters) for a line that starts with a known analyte, else (None, 0)."""
    norm = re.sub(r"[^a-z0-9]", "", line.lower())
    best, blen = None, 0
    for code, names in LAB_ANALYTE_ALIASES.items():
        for n in names:
            if norm.startswith(n) and len(n) > blen:
                best, blen = code, len(n)
    return best, blen


def parse_lab_report(lines):
    """Pick the report header fields and the result rows out of the text lines of a lab report. Layout-tolerant: header
    fields come from labelled lines; result rows are the lines of the results table (between its 'Analysis ... Result' header and
    the end of the table) that carry a value. Known analytes map to a specification; anything else is an additional analysis."""
    text = "\n".join(lines)
    low = text.lower()
    out = {"lab": "", "reportNumber": "", "reportDate": "", "sampleRef": "", "rows": [], "warnings": []}
    if "foodassure" in low or "food assure" in low:
        out["lab"] = "FoodAssure"
    elif re.search(r"\bsgs\b", low):
        out["lab"] = "SGS"
    for ln in lines:
        if not out["reportNumber"]:
            m = re.fullmatch(r"([A-Z]{2}\d{2}-\d{3,}\.\d+)", ln.strip())                 # SGS: VR26-05008.007
            if m:
                out["reportNumber"] = m.group(1)
        if not out["reportNumber"]:
            m = re.match(r"(?:Certificate|Report)?\s*(?:No\.?|Number|#)\s*:\s*([A-Za-z0-9][A-Za-z0-9.\-/]*)", ln)   # FoodAssure: Number: 26-AU-223.18A
            if m:
                out["reportNumber"] = m.group(1)
        if not out["reportDate"]:
            m = re.search(r"(?:Date of Report|Report Date|Date Reported|Date Issued|Issued|Completed)\s*:\s*(.+)", ln, re.I)
            if m:
                out["reportDate"] = lab_parse_date(m.group(1))
        if not out["sampleRef"]:
            m = re.search(r"(?:Client\s+)?Sample[ _]?ID\s*:\s*(.+)$", ln, re.I)
            if m:
                out["sampleRef"] = m.group(1).strip()
    # the results table
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"Analysis\b", ln) and re.search(r"\bResults?\b", ln):
            start = i + 1
            break
    region = []
    if start is not None:
        for ln in lines[start:]:
            if len(re.findall(r"[A-Za-z0-9]", ln)) < 3 or re.match(r"(Date Start|NOTE|Above results|Signed|End of Report)", ln, re.I):
                break
            region.append(ln)
    else:
        region = [ln for ln in lines if lab_match_alias(ln)[0]]
        out["warnings"].append("This report's layout was not recognised; only lines naming a known test were read. Check every value.")
    seen = set()
    for ln in region:
        code, n = lab_match_alias(ln)
        # the part of the line after the analyte's name
        name_part = re.split(r"\s*[(<>]|\s\d|\s+[A-Z]{3,}-", ln, 1)[0].strip().rstrip("%").strip()
        rest = ln[len(name_part):] if ln.startswith(name_part) else ln
        value, qual = None, None
        vm = _LAB_VALUE_RE.search(rest)
        qm = _LAB_QUAL_RE.search(rest)
        if vm and (not qm or vm.start() < qm.start()):
            value = (vm.group(1) or "").replace(" ", "") + vm.group(2)
            after = rest[vm.end():].strip()
        elif qm:
            value = {"nd": "Negative", "notdetected": "Negative"}.get(re.sub(r"\s", "", qm.group(1).lower()), qm.group(1).capitalize())
            after = rest[qm.end():].strip()
        else:
            continue
        um = _LAB_UNIT_RE.match(after)
        unit = um.group(1) if um else ""
        if not unit:
            pm = re.search(r"\(([^()]*)\)", rest)
            if pm and _LAB_UNIT_RE.match(pm.group(1).strip()):
                unit = pm.group(1).strip()
        mm = _LAB_METHOD_RE.search(rest)
        method = mm.group(0).strip() if mm else ""
        key = code or name_part.lower()
        if key in seen:
            continue
        seen.add(key)
        if code:
            out["rows"].append({"specCode": code, "analyte": None, "value": value, "unit": unit, "method": method})
        elif name_part:
            out["rows"].append({"specCode": None, "analyte": name_part, "value": value, "unit": unit, "method": method})
    if not out["rows"]:
        out["warnings"].append("No result rows were found. Is this a scanned image? Enter the values by hand.")
    return out



# basis: run = measured in-house (Packaging QC check), value = lab result compared as reported, metal = lab concentration
# converted to a loading (kg metal / ha) at the application rate x application periods and compared with the kg/ha limit,
# absent = qualitative (Negative / Positive).
COA_GROUPS = (("physical", "Physical & chemical"), ("metals", "Heavy metals"), ("microbial", "Microbiological"))
COA_SPEC_SEED = [
    # code, name, group, unit, basis, min, max, max_exclusive, required for release, sort, default method, regulatory limit kg/ha (metals)
    ("tds", "TDS", "physical", "%", "run", 1, 2, 0, 0, 10, "", None),
    ("ph", "pH", "physical", "", "run", 3.0, 4.0, 0, 0, 20, "", None),
    ("potash", "Soluble potash, K2O", "physical", "%", "value", 0.5, None, 0, 0, 30, "", None),
    ("as", "Arsenic (As)", "metals", "kg/ha", "metal", None, 15, 0, 0, 100, "ICP-MS", None),
    ("cd", "Cadmium (Cd)", "metals", "kg/ha", "metal", None, 4, 0, 0, 110, "ICP-MS", None),
    ("cr", "Chromium (Cr)", "metals", "kg/ha", "metal", None, 210, 0, 0, 120, "ICP-MS", None),
    ("co", "Cobalt (Co)", "metals", "kg/ha", "metal", None, 30, 0, 0, 130, "ICP-MS", None),
    ("cu", "Copper (Cu)", "metals", "kg/ha", "metal", None, 150, 0, 0, 140, "ICP-MS", None),
    ("pb", "Lead (Pb)", "metals", "kg/ha", "metal", None, 100, 0, 0, 150, "ICP-MS", None),
    ("hg", "Mercury (Hg)", "metals", "kg/ha", "metal", None, 1, 0, 0, 160, "ICP-MS", None),
    ("mo", "Molybdenum (Mo)", "metals", "kg/ha", "metal", None, 4, 0, 0, 170, "ICP-MS", None),
    ("ni", "Nickel (Ni)", "metals", "kg/ha", "metal", None, 36, 0, 0, 180, "ICP-MS", None),
    ("se", "Selenium (Se)", "metals", "kg/ha", "metal", None, 2.8, 0, 0, 190, "ICP-MS", None),
    ("zn", "Zinc (Zn)", "metals", "kg/ha", "metal", None, 370, 0, 0, 200, "ICP-MS", None),
    ("apc", "Aerobic plate count", "microbial", "cfu/g", "value", None, 500, 1, 1, 300, "MFHPB-18", None),
    ("yeast", "Yeast", "microbial", "cfu/g", "value", None, 20, 1, 1, 310, "MFHPB-22", None),
    ("mold", "Mold", "microbial", "cfu/g", "value", None, 20, 1, 1, 320, "MFHPB-22", None),
    ("fecal", "Fecal coliforms", "microbial", "MPN/g", "value", None, 1.8, 1, 1, 330, "MFHPB-19", None),
    ("salm", "Salmonella spp.", "microbial", "per 25 g", "absent", None, 1, 1, 1, 340, "MFHPB-20", None),
]
# Units a lab may report a metal in, as a multiplier to ppm (mg/kg). Product density is taken as 1 kg/L, so mg/L = mg/kg.
COA_PPM_FACTORS = {"ppm": 1.0, "mg/kg": 1.0, "mg/l": 1.0, "%": 10000.0, "ppb": 0.001, "ug/kg": 0.001, "g/kg": 1000.0}


# SGS: the analyses we can request. Minerals are picked individually (the 11 on the Certificate of Analysis are the "CFIA Heavy Metals" group); the
# requisition shows a single "Mineral Scan Up to 12 / 20" chosen from how many minerals were ticked, and lists the minerals themselves in the Notes.
MINERAL_ORDER = ["As", "Cd", "Cr", "Co", "Cu", "Pb", "Hg", "Mo", "Ni", "Se", "Zn", "Ca", "K", "Na", "P", "Mg", "Fe", "S"]
SGS_CFIA = "CFIA Heavy Metals"
SGS_MINERALS = [("Arsenic (As)", "As", SGS_CFIA), ("Cadmium (Cd)", "Cd", SGS_CFIA), ("Chromium (Cr)", "Cr", SGS_CFIA), ("Cobalt (Co)", "Co", SGS_CFIA),
                ("Copper (Cu)", "Cu", SGS_CFIA), ("Lead (Pb)", "Pb", SGS_CFIA), ("Mercury (Hg)", "Hg", SGS_CFIA), ("Molybdenum (Mo)", "Mo", SGS_CFIA),
                ("Nickel (Ni)", "Ni", SGS_CFIA), ("Selenium (Se)", "Se", SGS_CFIA), ("Zinc (Zn)", "Zn", SGS_CFIA),
                ("Calcium (Ca)", "Ca", "Other minerals"), ("Potassium (K)", "K", "Other minerals"), ("Sodium (Na)", "Na", "Other minerals"),
                ("Phosphorus (P)", "P", "Other minerals"), ("Magnesium (Mg)", "Mg", "Other minerals"), ("Iron (Fe)", "Fe", "Other minerals"),
                ("Sulfur (S)", "S", "Other minerals")]
SGS_DIRECT = ["Total Nitrogen", "Moisture - Vacuum Oven", "Proximate Analysis"]
SGS_SCANS = [("Mineral Scan Up to 12", 12), ("Mineral Scan Up to 20", 20)]
# analyses whose request always adds a line to the requisition Notes (an admin can edit these per analysis)
SGS_REQ_NOTES = {"Total Nitrogen": "Total Nitrogen expressed in percent",
                 "Proximate Analysis": "Ash, Crude_protein, Crude_fat (Crude_lipid), Crude_Fiber"}


def ensure_sgs_analyses(conn):
    """Once per SGS lab: make sure the requestable analyses exist and the 11 CoA minerals sit in the "CFIA Heavy Metals" group. Existing rows are
    kept (their ids may be in cart assignments); only rows still at their defaults are re-categorised."""
    for lab in conn.execute("SELECT id FROM labs WHERE LOWER(name) LIKE '%sgs%'").fetchall():
        flag = "sgs_analyses_%d" % lab["id"]
        if conn.execute("SELECT 1 FROM app_flags WHERE key=?", (flag,)).fetchone():
            continue
        have = {r["name"].lower(): r for r in conn.execute("SELECT * FROM lab_analyses WHERE lab_id=?", (lab["id"],))}

        def put(name, kind, category=None, symbol=None, capacity=None, method=None):
            r = have.get(name.lower())
            if r is None:
                conn.execute("INSERT INTO lab_analyses (lab_id,name,method,category,kind,symbol,capacity) VALUES (?,?,?,?,?,?,?)",
                             (lab["id"], name, method, category, kind, symbol, capacity))
            elif (r["kind"] or "analysis") == "analysis" and not r["category"] and kind != "analysis":
                conn.execute("UPDATE lab_analyses SET kind=?, category=?, symbol=?, capacity=?, method=COALESCE(method,?) WHERE id=?",
                             (kind, category, symbol, capacity, method, r["id"]))
        for name, symbol, category in SGS_MINERALS:
            put(name, "mineral", category, symbol, None, "ICP-MS")
        for name in SGS_DIRECT:
            put(name, "analysis")
        for name, cap in SGS_SCANS:
            put(name, "scan", None, None, cap, "ICP-MS")
        conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES (?,?)", (flag, now_iso()))
    # the SGS form lists a Sample ID once however many samples share it (once per lab, so an admin can switch it off)
    for lab in conn.execute("SELECT id FROM labs WHERE LOWER(name) LIKE '%sgs%'").fetchall():
        flag = "sgs_merge_ids_%d" % lab["id"]
        if not conn.execute("SELECT 1 FROM app_flags WHERE key=?", (flag,)).fetchone():
            conn.execute("UPDATE labs SET merge_ids=1 WHERE id=?", (lab["id"],))
            conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES (?,?)", (flag, now_iso()))
    # the standing notes: once per lab, and only onto an analysis that has none (an admin's wording is never overwritten)
    for lab in conn.execute("SELECT id FROM labs WHERE LOWER(name) LIKE '%sgs%'").fetchall():
        flag = "sgs_req_notes_%d" % lab["id"]
        if conn.execute("SELECT 1 FROM app_flags WHERE key=?", (flag,)).fetchone():
            continue
        for name, note in SGS_REQ_NOTES.items():
            conn.execute("UPDATE lab_analyses SET req_note=? WHERE lab_id=? AND LOWER(name)=? AND req_note IS NULL", (note, lab["id"], name.lower()))
        conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES (?,?)", (flag, now_iso()))


def mineral_scan_for(analyses, n):
    """The smallest active mineral scan of a lab that covers n minerals, or None."""
    scans = sorted([a for a in analyses if a["kind"] == "scan" and a["active"] and a["capacity"]], key=lambda a: a["capacity"])
    return next((a for a in scans if a["capacity"] >= n), None), (scans[-1]["capacity"] if scans else 0)


def mineral_notes(sample_rows):
    """Notes lines that spell out every mineral requested: one per distinct (scan, minerals) set, each once -- no Sample IDs. The scan name is
    derived from the count, but the individual minerals are always listed. `**x**` marks the part printed in bold (the analysis the line is about)."""
    out = []
    for sr in sample_rows:
        if sr.get("_minerals"):
            line = "**%s** requested (%d mineral%s): %s." % (sr["scan"], len(sr["_minerals"]), "" if len(sr["_minerals"]) == 1 else "s", ", ".join(sr["_minerals"]))
            if line not in out:
                out.append(line)
    return out


def analysis_notes(sample_rows, an):
    """One Notes line per requested analysis that carries a standing note (`lab_analyses.req_note`), in the lab's analysis order, each once however
    many samples request it. The analysis name is bold: a note that already starts with the name has that part bolded, else the name is put in front."""
    want = set()
    for sr in sample_rows:
        want.update(sr.get("_direct_ids", []))
    out = []
    for aid in sorted(want):
        note = ((an[aid]["req_note"] or "").strip() if aid in an else "").replace("**", "")
        if note:
            name = an[aid]["name"]
            line = ("**%s**%s" % (note[:len(name)], note[len(name):])) if note.lower().startswith(name.lower()) else "**%s:** %s" % (name, note)
            if line not in out:
                out.append(line)
    return out


def requisition_notes(sample_rows, an, user_notes):
    """The requisition Notes: every request is its own entry with a blank line between entries, each said once -- the minerals, then each analysis's standing note, then anything typed
    in the cart. `**x**` marks bold text (the analysis a line applies to); the rest of the notes is regular."""
    lines = mineral_notes(sample_rows) + analysis_notes(sample_rows, an)
    seen = {ln.replace("**", "") for ln in lines}
    for ln in (user_notes or "").replace("**", "").splitlines():
        if ln.strip() and ln.strip() not in seen:
            seen.add(ln.strip())
            lines.append(ln.strip())
    return "\n\n".join(lines)


REQ_CONTACT_DEFAULTS = (("submit_name", "Nathan Wrana"), ("submit_phone", "204-963-5023"), ("submit_email", "nwrana@cascadiaseaweed.com"),
                        ("email_1", "nwrana@cascadiaseaweed.com"), ("email_2", "dpedde@cascadiaseaweed.com"),
                        ("email_3", ""), ("email_4", ""), ("email_5", ""))


def ensure_requisition_contact(conn):
    for k, v in REQ_CONTACT_DEFAULTS:
        conn.execute("INSERT OR IGNORE INTO requisition_contact (key,value) VALUES (?,?)", (k, v))


def requisition_contact_get(conn):
    """The contact details printed on requisitions: 'submitter' {name, phone, email} ("Samples submitted by"; the name and phone are also the contact
    for results and the signature) and 'emails' [5] (where results are sent: "Send analysis results to" and the customer emails on the FoodAssure form)."""
    kv = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM requisition_contact")}
    g = lambda k: kv.get(k, "")
    return {"submitter": {"name": g("submit_name"), "phone": g("submit_phone"), "email": g("submit_email")},
            "emails": [g("email_%d" % i) for i in range(1, 6)]}


def _contact_email(e):
    e = str(e or "").strip()[:120]
    if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        raise ApiError(400, "\u201c%s\u201d is not a valid email address" % e)
    return e


def requisition_contact_clean(d, base=None):
    """Validate a contact payload (the admin defaults, or a per-requisition override from the cart). A part the payload leaves out
    keeps its value from `base`."""
    d = d or {}
    base = base or {"submitter": {"name": "", "phone": "", "email": ""}, "emails": [""] * 5}
    sub = d.get("submitter") or {}
    emails = base["emails"] if d.get("emails") is None else [_contact_email(x) for x in d["emails"]][:5]
    emails = list(emails) + [""] * (5 - len(emails))
    pick = lambda k: str(sub[k] if k in sub else base["submitter"][k] or "").strip()
    return {"submitter": {"name": pick("name")[:80], "phone": pick("phone")[:40],
                          "email": _contact_email(sub["email"]) if "email" in sub else base["submitter"]["email"]},
            "emails": emails}


def requisition_contact_pairs(c):
    """The requisition_contact (key, value) rows of a cleaned contact."""
    return ([("submit_name", c["submitter"]["name"]), ("submit_phone", c["submitter"]["phone"]), ("submit_email", c["submitter"]["email"])]
            + [("email_%d" % (i + 1), c["emails"][i]) for i in range(5)])


def ensure_coa_specs(conn):
    """Seed the product specifications (INSERT OR IGNORE: an admin's edits are never overwritten)."""
    for code, name, grp, unit, basis, mn, mx, excl, req, sort, method, kg in COA_SPEC_SEED:
        conn.execute("INSERT OR IGNORE INTO coa_specs (code,name,grp,unit,basis,min_val,max_val,max_exclusive,required,sort,method,limit_kg_ha)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (code, name, grp, unit, basis, mn, mx, excl, req, sort, method, kg))
    # an interim version judged metals against a ppm limit derived by a calculator: the limit is the kg/ha regulatory value again (once: it must
    # not overwrite a unit or limit an administrator sets afterwards)
    run_once(conn, "coa_metal_limits_kg_ha", lambda: conn.execute(
        "UPDATE coa_specs SET max_val=limit_kg_ha, unit='kg/ha' WHERE basis='metal' AND unit='ppm' AND limit_kg_ha IS NOT NULL"))
    for key, label, desc in ((k, l, d) for k, v, l, d in SETTINGS_DEFAULTS if k in ("coa_application_rate_kg_ha", "coa_application_periods")):
        conn.execute("UPDATE settings SET label=?, description=? WHERE key=?", (label, desc, key))


def coa_num(v, digits=6):
    if v is None:
        return ""
    s = ("%." + str(digits) + "g") % v
    if "e" in s:
        s = ("%." + str(digits) + "f") % v
    return s.rstrip("0").rstrip(".") if "." in s else s


def coa_loading_text(v, qual=""):
    """A metal loading in kg/ha for display ('<' marks an upper bound from a below-detection result)."""
    if v is None:
        return ""
    if v < 0.001:
        return "<0.001"
    return (qual if qual == "<" else "") + (("%.2f" % v) if v >= 10 else ("%.3g" % v))


def parse_result_value(text):
    """'<20', '100', '1,200', 'Negative' -> (qualifier, number, text). Raises ApiError when it is none of these."""
    t = (text or "").strip()
    m = re.fullmatch(r"([<>]=?)?\s*([0-9][0-9,]*\.?[0-9]*(?:[eE][-+]?[0-9]+)?|\.[0-9]+)", t)
    if m:
        qual = (m.group(1) or "")[:1]
        return qual, float(m.group(2).replace(",", "")), t
    low = t.lower()
    if low in ("negative", "neg", "absent", "not detected", "nd", "n/d"):
        return "", None, "Negative"
    if low in ("positive", "pos", "present", "detected"):
        return "", None, "Positive"
    raise ApiError(400, "Enter a result as a number, a '<' value (e.g. <20), or Negative / Positive")


def coa_result_text(x):
    if x is None:
        return ""
    if x["value_num"] is None:
        return x["value_text"] or ""
    return (x["qualifier"] or "") + coa_num(x["value_num"])


def coa_spec_text(s):
    u = (" " + s["unit"]) if s["unit"] else ""
    if s["code"] == "ph" and s["min_val"] is not None and s["max_val"] is not None:
        return "%.1f - %.1f" % (s["min_val"], s["max_val"])
    if s["basis"] == "absent":
        return "Negative (< %s %s)" % (coa_num(s["max_val"]), s["unit"])
    if s["min_val"] is not None and s["max_val"] is not None:
        return "%s - %s%s" % (coa_num(s["min_val"]), coa_num(s["max_val"]), u)
    if s["min_val"] is not None:
        return "min %s%s" % (coa_num(s["min_val"]), u)
    if s["max_val"] is not None:
        return ("< %s%s" if s["max_exclusive"] else "≤ %s%s") % (coa_num(s["max_val"]), u)
    return "-"


def _coa_judge(s, qual, num, text, rate):
    """-> (status, loading kg/ha or None, note). status: pass | fail | review | not_evaluated."""
    if s["basis"] == "absent":
        return ("pass" if text == "Negative" else "fail"), None, ""
    if num is None:
        return "review", None, "Expected a numeric result"
    mn, mx, excl = s["min_val"], s["max_val"], bool(s["max_exclusive"])
    loading = None
    if qual == "<":
        if mn is not None and num <= mn:
            return "fail", None, ""
        return ("pass" if (mx is None or num <= mx) else "review"), loading, ""
    if qual == ">":
        if mx is not None and num >= mx:
            return "fail", None, ""
        return ("pass" if (mn is None or num >= mn) else "review"), loading, ""
    if mn is not None and num < mn:
        return "fail", loading, ""
    if mx is not None and (num >= mx if excl else num > mx):
        return "fail", loading, ""
    return "pass", loading, ""


def coa_evaluate(conn, r):
    """Conformance of one run's results against the active specifications. Returns {rows, additional, summary}."""
    rate = float(get_setting_value(conn, "coa_application_rate_kg_ha", 0) or 0)
    periods = float(get_setting_value(conn, "coa_application_periods", 1) or 1)
    if periods <= 0:
        periods = 1.0
    latest, additional = {}, []
    for x in conn.execute("SELECT * FROM lab_results WHERE run_id=? AND voided_at IS NULL ORDER BY id", (r["id"],)):
        if x["spec_code"]:
            latest[x["spec_code"]] = x
        else:
            additional.append(x)
    rows = []
    for s in conn.execute("SELECT * FROM coa_specs ORDER BY sort, code"):      # every test, listed on the certificate or not
        row = {"listed": bool(s["active"]), "code": s["code"], "name": s["name"], "group": s["grp"], "unit": s["unit"], "basis": s["basis"],
               "required": bool(s["required"]), "specText": coa_spec_text(s), "result": None, "resultText": "", "status": None,
               "note": "", "loading": None, "loadingText": ""}
        if s["basis"] == "run":
            v = r["packaging_tds_pct"] if s["code"] == "tds" else r["packaging_qc_ph"] if s["code"] == "ph" else None
            if v is not None:
                row["resultText"] = coa_num(v, 4) + ((" " + s["unit"]) if s["unit"] else "")
                row["status"] = _coa_judge(s, "", float(v), "", rate)[0]
                row["source"] = "Production log (Packaging QC check)"
        elif s["code"] in latest:
            x = latest[s["code"]]
            row["result"] = {"id": x["id"], "labName": x["lab_name"], "reportNumber": x["report_number"], "reportDate": x["report_date"],
                             "method": x["method"], "attachmentId": x["attachment_id"], "enteredBy": x["entered_by"],
                             "enteredAt": x["entered_at"], "unit": x["unit"], "sampleRef": x["sample_ref"]}
            row["resultText"] = coa_result_text(x) + ((" " + x["unit"]) if x["unit"] and x["value_num"] is not None else "")
            if s["basis"] == "metal":
                f = COA_PPM_FACTORS.get((x["unit"] or "").strip().lower())
                if f is None or x["value_num"] is None:
                    row["status"], row["note"] = "review", "Unit not recognised"
                elif rate <= 0 or s["max_val"] is None:
                    row["status"], row["note"] = "not_evaluated", "Application rate not set" if rate <= 0 else "Limit not set"
                else:
                    # loading (kg metal / ha) = mg/kg x application rate (kg product / ha) x application periods / 1e6
                    loading = x["value_num"] * f * rate * periods / 1e6
                    mx = s["max_val"]
                    row["loading"], row["loadingText"] = loading, coa_loading_text(loading, x["qualifier"])
                    if x["qualifier"] == ">":
                        row["status"] = "fail" if loading >= mx else "review"
                    elif x["qualifier"] == "<":
                        # "below the detection limit": proves compliance only when the detection limit is itself within the limit
                        row["status"] = "pass" if loading <= mx else "review"
                        if row["status"] == "review":
                            row["note"] = "Detection limit is above the specification limit"
                    else:
                        row["status"] = "pass" if loading <= mx else "fail"
            else:
                row["status"] = _coa_judge(s, x["qualifier"] or "", x["value_num"], x["value_text"], rate)[0]
        row["tested"] = row["status"] is not None
        rows.append(row)
    req = [x for x in rows if x["required"]]
    summary = {"requiredTotal": len(req), "requiredReceived": sum(1 for x in req if x["result"]),
               "missingRequired": [x["name"] for x in req if not x["result"]],
               "failed": [x["name"] for x in rows if x["status"] == "fail"],
               "review": [x["name"] for x in rows if x["status"] in ("review", "not_evaluated")],
               "metalsReceived": sum(1 for x in rows if x["group"] == "metals" and x["result"]),
               "metalsTotal": sum(1 for x in rows if x["group"] == "metals"),
               "applicationRate": rate or None, "applicationPeriods": periods, "additionalCount": len(additional)}
    return {"rows": rows, "additional": [lab_result_public(x) for x in additional], "summary": summary}


def lab_result_public(x):
    return {"id": x["id"], "runId": x["run_id"], "specCode": x["spec_code"], "analyte": x["analyte"], "labId": x["lab_id"],
            "labName": x["lab_name"], "reportNumber": x["report_number"], "reportDate": x["report_date"], "sampleRef": x["sample_ref"],
            "method": x["method"], "qualifier": x["qualifier"], "valueNum": x["value_num"], "valueText": x["value_text"],
            "resultText": coa_result_text(x), "unit": x["unit"], "attachmentId": x["attachment_id"],
            "enteredBy": x["entered_by"], "enteredAt": x["entered_at"], "voidedAt": x["voided_at"], "voidedBy": x["voided_by"],
            "voidReason": x["void_reason"]}


_COA_STATUS = {"pass": ("PASS", "GREEN"), "fail": ("FAIL", "RED"), "review": ("REVIEW", "AMBER"),
               "not_evaluated": ("N/E", "AMBER")}


def build_coa_pdf(S, logo_path=None):
    """S: the dict from Handler._coa_data. A one-to-two page Certificate of Analysis: lot facts, then the results against the
    product specification, grouped (physical & chemical / heavy metals / microbiological / additional analyses)."""
    logo = _pdf_logo(logo_path) if logo_path else None
    pdf = PdfBuilder("Certificate of Analysis %s" % S["lot"], "%s  ·  Certificate of Analysis  ·  generated %s by %s" % (
        S["lot"], S["generatedAt"], S["generatedBy"] or "KelpWorks"), logo, S["lot"])
    M, W = pdf.M, pdf.W
    pdf.text(M, 84, "Certificate of Analysis", 20, True, pdf.TEAL)
    pdf.text(M, 102, S["lot"] + "  ·  " + (S["product"] or "-"), 11, True, (0.15, 0.15, 0.15))
    lab = "RELEASED" if S["released"] else "PRELIMINARY - NOT RELEASED"
    cw = pdf_text_width(_pdf_safe(lab), 8, True) + 14
    pdf.chip(W - M - cw, 72, lab, pdf.GREEN if S["released"] else pdf.AMBER)
    pdf.y = 112
    pdf.hline(M, W - M, pdf.y, pdf.LINE, 0.5)
    pdf.y += 6
    pdf.kv_grid([("Product", S["product"]), ("Processing lot", S["lot"]), ("Production date", S["runDate"]),
                 ("Release status", S["releaseLabel"]), ("Date issued", S["generatedAt"])], cols=3)
    pdf.note("Finished-goods lots: " + S["fgLots"], 8, (0.1, 0.1, 0.1), False)
    colors = {"GREEN": pdf.GREEN, "RED": pdf.RED, "AMBER": pdf.AMBER}
    for gcode, gname in COA_GROUPS:
        grp = [x for x in S["rows"] if x["group"] == gcode]
        if not grp:
            continue
        pdf.space(130)
        pdf.heading(gname)
        metals = gcode == "metals"
        headers = ["Test", "Specification", "Result (as reported)"] + (["Loading (kg/ha)"] if metals else []) + ["Method", "Laboratory / report", "Conformance"]
        widths = [24, 20, 20] + ([14] if metals else []) + [14, 26, 13]
        aligns = ["l", "l", "l"] + (["c"] if metals else []) + ["l", "l", "l"]
        out = []
        for x in grp:
            res = x["result"]
            rtxt = x["resultText"] or ("Not tested" if not x["required"] else "PENDING")
            if res:
                src = " - ".join(p for p in (res["labName"], res["reportNumber"]) if p)
                if res["reportDate"]:
                    src += "  (" + res["reportDate"] + ")"
            else:
                src = x.get("source") or ""
            if x["status"] in _COA_STATUS:
                t, c = _COA_STATUS[x["status"]]
                st = (t, {"bold": True, "color": colors[c]})
            else:
                st = ("Not tested" if not x["required"] else "Pending", {"color": pdf.GRAY})
            out.append([(x["name"], {"bold": True}), x["specText"], rtxt] + ([x["loadingText"] or "-"] if metals else [])
                        + [(res or {}).get("method") or "", src, st])
        pdf.table(headers, out, widths, aligns, size=7.8)
        if gcode == "metals":
            if S["applicationRate"]:
                pdf.note("Loading (kg metal per ha) = result (mg/kg; 1 %% = 10,000 mg/kg) x application rate (%s kg product per ha) x application periods (%s) / 1,000,000. "
                         "A result below the detection limit is compared at the limit." % (coa_num(S["applicationRate"]), coa_num(S["applicationPeriods"])))
            else:
                pdf.note("Heavy-metal results are shown as reported by the laboratory; they are not judged because the product application rate has not been set.")
        if gcode == "microbial":
            pdf.note("Microbial results are per gram of liquid product (Salmonella per 25 g), as reported by the laboratory; '<' = below the limit of quantitation.")
    if S["additional"]:
        pdf.heading("Additional analyses")
        pdf.table(["Test", "Result", "Method", "Laboratory / report"],
                  [[(x["analyte"], {"bold": True}), (x["resultText"] + ((" " + x["unit"]) if x["unit"] and x["valueNum"] is not None else "")),
                    x["method"] or "", " - ".join(p for p in (x["labName"], x["reportNumber"]) if p) +
                    (("  (" + x["reportDate"] + ")") if x["reportDate"] else "")] for x in S["additional"]],
                  [26, 20, 18, 36], size=7.8)
    pdf.heading("Release")
    if S["released"]:
        pdf.kv_grid([("Released by", S["releasedBy"]), ("Release date", S["releasedAt"]), ("Statement", "Conforms to specification")], cols=3)
    else:
        pdf.note("This certificate is preliminary. It becomes valid when the Quality Manager releases the lot.", 8.2, pdf.AMBER, False)
    if S["failed"]:
        pdf.note("Results outside specification: " + ", ".join(S["failed"]) + ".", 8.2, pdf.RED, False)
        if S["releasedComment"]:
            pdf.note("Released with results outside specification. Reason recorded by Quality: " + S["releasedComment"], 8.2, pdf.RED, False)
    pdf.space(30)
    pdf.note("Results relate only to the samples tested. Laboratory reports are retained with the production record. "
             "This certificate may not be reproduced except in full.")
    return pdf.build()


# ---------------------------------------------------------------------------------------
# Sample catalogue + lab requisitions
# ---------------------------------------------------------------------------------------
COMPANY_NAME = "Cascadia Seaweed Corp"
# Sample Point box (stage) -> (code abbreviation, label, production_runs column holding its collection time)
SAMPLE_STAGES = {
    "homogenization": ("HOM", "Homogenization", "homog_sample_collected_at"),
    "separation_solids": ("SEP", "Separation (solids)", "separation_solids_sample_collected_at"),
    "pasteurization_pre": ("PRE", "Pasteurization (pre)", "pasteurization_pre_sample_collected_at"),
    "pasteurization_post": ("PAS", "Pasteurization (post)", "pasteurization_post_sample_collected_at"),
    "packaging": ("PKG", "Packaging", "packaging_sample_collected_at"),
}


# What a sample LABEL calls a sample point (the catalogue keeps the process-stage name). Stages not listed use their stage name.
SAMPLE_LABEL_DEFAULT_NAMES = {"packaging": "Finished Product"}


def sample_label_name(conn, stage):
    r = conn.execute("SELECT name FROM sample_label_names WHERE stage=?", (stage,)).fetchone()
    if r and r["name"]:
        return r["name"]
    return SAMPLE_LABEL_DEFAULT_NAMES.get(stage) or sample_stage_info(stage)[1]


def sample_stage_info(stage):
    return SAMPLE_STAGES.get(stage or "homogenization") or ((stage or "OTH")[:3].upper(), stage or "Other", None)


def add_months(iso_date, months):
    """YYYY-MM-DD + whole months (day clamped to the month's length)."""
    try:
        d = datetime.date.fromisoformat((iso_date or "")[:10])
    except ValueError:
        return None
    m = d.month - 1 + int(months)
    y, m = d.year + m // 12, m % 12 + 1
    last = (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(d.day, last)).isoformat()


def sample_log(conn, sample_id, event_type, detail=None, user_name=None):
    conn.execute("INSERT INTO sample_events (sample_id,event_type,detail,user_name,created_at) VALUES (?,?,?,?,?)",
                 (sample_id, event_type, detail, user_name, now_iso()))


def lot_simplified(lot):
    """'PR-20261006-053' -> 'Lot-053': "Lot-" + the last 3 digits of the production run."""
    digits = re.search(r"(\d{3})\D*$", lot or "")
    return "Lot-" + (digits.group(1) if digits else (re.sub(r"\D", "", lot or "")[-3:] or (lot or "")))


def refresh_sample_ids(conn, run_id):
    """Set every sample's Sample ID Detailed / Simplified (and label type) from its Sample Point row: the first line of the Detailed label is the
    processing lot, of the Simplified label "Lot-" + the last 3 digits; both take "-<unit no>" when the point's labels are numbered. A sample whose point
    row no longer exists (history) keeps what it had. A sample that is already on a requisition is LOCKED: its IDs and label type never change again
    (the tube and the lab's paperwork carry them), whatever is later changed on the label window. Idempotent -- runs after every sync_samples, at boot
    and when label settings are saved."""
    run = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        return
    lot = run["processing_lot"]
    pts = {p["id"]: p for p in conn.execute("SELECT * FROM run_sample_points WHERE run_id=?", (run_id,))}
    for smp in conn.execute("SELECT id, sample_point_id, unit_no, id_detailed, requisition_id FROM samples WHERE run_id=?", (run_id,)).fetchall():
        if smp["requisition_id"] and smp["id_detailed"]:
            continue                      # locked: already on a requisition
        p = pts.get(smp["sample_point_id"])
        if p is None and smp["id_detailed"]:
            continue
        ltype = p["label_type"] if p is not None and p["label_type"] in ("detailed", "simplified") else "detailed"
        suffix = "-%d" % (smp["unit_no"] or 1) if (p is not None and p["label_numbered"]) else ""
        conn.execute("UPDATE samples SET id_detailed=?, id_simplified=?, label_type=? WHERE id=?",
                     (lot + suffix, lot_simplified(lot) + suffix, ltype, smp["id"]))


def sync_samples(conn, run_id, user_name=None):
    """Make the `samples` rows of a finalized run match its Sample Point rows: one sample per unit
    (qty), created once with a stable code; metadata follows later edits while the sample is still
    in inventory; units whose row was removed / shrunk are dropped only if they were never sent
    anywhere (a submitted / removed sample is history and stays)."""
    run = conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["status"] != "completed":
        return
    points = conn.execute("SELECT * FROM run_sample_points WHERE run_id=? ORDER BY created_at, id", (run_id,)).fetchall()
    existing = {(r["sample_point_id"], r["unit_no"]): r for r in conn.execute("SELECT * FROM samples WHERE run_id=?", (run_id,))}
    live = set()
    for pt in points:
        stage = pt["stage"] or "homogenization"
        abbr, _label, col = sample_stage_info(stage)
        collected = (run[col] if col and col in run.keys() else None) or pt["created_at"]
        for u in range(1, max(1, int(pt["qty"] or 1)) + 1):
            live.add((pt["id"], u))
            row = existing.get((pt["id"], u))
            if row:
                if row["status"] in ("available", "in_cart"):
                    conn.execute("UPDATE samples SET stage=?, type=?, description=?, container=?, collected_at=? WHERE id=?",
                                 (stage, pt["type"] or "Slurry", pt["description"] or "Microbial", pt["container"], collected, row["id"]))
                continue
            n = conn.execute("SELECT COUNT(*) c FROM samples WHERE run_id=? AND stage=?", (run_id, stage)).fetchone()["c"] + 1
            while True:
                code = "%s-%s-%02d" % (run["processing_lot"], abbr, n)
                if not conn.execute("SELECT 1 FROM samples WHERE sample_code=?", (code,)).fetchone():
                    break
                n += 1
            cur = conn.execute(
                "INSERT INTO samples (sample_code,run_id,sample_point_id,unit_no,stage,type,description,container,"
                "collected_at,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'available',?)",
                (code, run_id, pt["id"], u, stage, pt["type"] or "Slurry", pt["description"] or "Microbial", pt["container"], collected, now_iso()))
            sample_log(conn, cur.lastrowid, "created", "Logged in the production log (%s, %s)" % (
                sample_stage_info(stage)[1], pt["description"] or "Microbial"), user_name)
    refresh_sample_ids(conn, run_id)
    for key, row in existing.items():
        if key not in live and row["status"] in ("available", "in_cart"):
            conn.execute("DELETE FROM sample_cart WHERE sample_id=?", (row["id"],))
            conn.execute("DELETE FROM sample_events WHERE sample_id=?", (row["id"],))
            conn.execute("DELETE FROM samples WHERE id=?", (row["id"],))


def sync_pending_samples(conn, user_name=None):
    """Finalized runs that have Sample Point rows but no catalogue entries yet (just finalized, or
    finalized before the catalogue existed)."""
    for r in conn.execute(
            "SELECT id FROM production_runs WHERE status='completed' AND EXISTS "
            "(SELECT 1 FROM run_sample_points p WHERE p.run_id=production_runs.id) AND NOT EXISTS "
            "(SELECT 1 FROM samples s WHERE s.run_id=production_runs.id)").fetchall():
        sync_samples(conn, r["id"], user_name)


# ---- Word (.docx) requisition templates: filled with the standard library (zipfile + string/regex on the XML) ----
_W_P = re.compile(r"<w:p(?:\s[^>]*)?>.*?</w:p>", re.S)
_W_TR = re.compile(r"<w:tr(?:\s[^>]*)?>.*?</w:tr>", re.S)
_W_T = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)
_TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
REQ_SCALAR_TOKENS = ["req_number", "date", "date_long", "po_number", "po_check", "company", "lab_name", "lab_contact", "lab_email", "lab_phone", "lab_address",
                     "customer_phone", "customer_email_1", "customer_email_2", "customer_email_3", "customer_email_4", "customer_email_5",
                     "submitter_name", "submitter_phone", "submitter_email", "results_name", "results_phone", "results_emails", "br",
                     "results_email_1", "results_email_2", "results_email_3", "results_email_4", "results_email_5",
                     "processing_lot", "run_date", "sku", "product", "requested_by", "requested_by_email",
                     "sample_count", "analyses", "notes"]
REQ_ANALYSIS_KEYS = ["name", "code", "method", "count", "n"]
REQ_SAMPLE_KEYS = ["n", "id", "id_detailed", "id_simplified", "code", "container_qty", "volume_text", "minerals", "scan", "report_description", "stage", "type", "description", "container", "collected", "analyses", "analyses_lines", "methods", "methods_lines", "location", "notes"]


def _x_unescape(t):
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _x_escape(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_text(xml):
    return "".join(_x_unescape(m.group(2)) for m in _W_T.finditer(xml))


def _run_props(rpr, bold):
    """A run-properties block with bold switched on or off (the rest of the formatting is kept)."""
    base = re.sub(r"<w:b(?:Cs)?(?:\s[^>]*)?/>", "", rpr or "")
    if not bold:
        return base
    if not base:
        return "<w:rPr><w:b/><w:bCs/></w:rPr>"
    fonts = re.search(r"<w:rFonts[^>]*/>", base)
    return base[:fonts.end()] + "<w:b/><w:bCs/>" + base[fonts.end():] if fonts else base.replace("<w:rPr>", "<w:rPr><w:b/><w:bCs/>", 1)


def _bold_runs(text, pxml, t0):
    """The replacement for a paragraph's first text element when the filled text uses `**bold**` markup: the original run is left empty
    and the text follows as separate runs, bold inside the markers and regular outside (formatting otherwise copied from the original run).
    The original run's closing tag is left to close the last new run."""
    starts = [m.start() for m in re.finditer(r"<w:r(?=[\s>])", pxml[:t0.start()])]
    rpr_m = re.search(r"<w:rPr>.*?</w:rPr>", pxml[starts[-1]:t0.start()], re.S) if starts else None
    rpr = rpr_m.group(0) if rpr_m else ""
    runs = []
    for n, seg in enumerate(text.split("**")):
        if seg:
            runs.append("<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (
                _run_props(rpr, n % 2 == 1), '</w:t><w:br/><w:t xml:space="preserve">'.join(_x_escape(x) for x in seg.split("\n"))))
    if not runs:
        return '<w:t xml:space="preserve"></w:t>'
    return '<w:t xml:space="preserve"></w:t></w:r>' + "".join(runs)[:-len("</w:r>")]


def _fill_paragraphs(xml, resolver):
    """Replace {{tokens}} paragraph by paragraph. Word often splits a token across several runs, so the
    paragraph's text is joined, replaced, and written back into its first text run."""
    def para(m):
        pxml = m.group(0)
        ts = list(_W_T.finditer(pxml))
        if not ts:
            return pxml
        full = "".join(_x_unescape(t.group(2)) for t in ts)
        if "{{" not in full:
            return pxml
        new = _TOKEN.sub(lambda mm: str(resolver(mm.group(1).strip())), full)
        out, pos = [], 0
        for i, t in enumerate(ts):
            out.append(pxml[pos:t.start()])
            if i == 0 and "**" in new:
                out.append(_bold_runs(new, pxml, t))
            else:
                out.append('<w:t xml:space="preserve">%s</w:t>' % '</w:t><w:br/><w:t xml:space="preserve">'.join(
                    _x_escape(part) for part in new.split("\n")) if i == 0 else t.group(1) + "</w:t>")
            pos = t.end()
        out.append(pxml[pos:])
        return "".join(out)
    return _W_P.sub(para, xml)


def docx_fill(template, scalars, samples, analyses=None):
    """template: .docx bytes. scalars: {token: value}. samples: [{n,id,stage,...,'analysis_names': [..]}].
    Any table row containing a {{sample.*}} token is repeated once per sample; any paragraph containing an
    {{analysis.*}} token is repeated once per requested analysis (analyses: [{name, code, count, n}])."""
    analyses = analyses or []

    def resolve(tok, s=None, a=None):
        if tok.startswith("analysis."):
            return (a or {}).get(tok[9:], "")
        if tok.startswith("sample."):
            if s is None:
                return ""
            key = tok[7:]
            if key.startswith("check:"):
                want = key[6:].strip().lower()
                return "\u2612" if want in [a.lower() for a in s.get("analysis_names", []) + s.get("analysis_codes", [])] else "\u2610"
            return s.get(key, "")
        return scalars.get(tok, "")
    zin = zipfile.ZipFile(io.BytesIO(template))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zip_read(zin, item.filename, 4 * MAX_ZIP_MEMBER_BYTES)
            if item.filename == "word/document.xml" or re.match(r"word/(header|footer)\d*\.xml$", item.filename):
                xml = data.decode("utf-8")
                if item.filename == "word/document.xml":
                    def row(m):
                        r = m.group(0)
                        if "{{sample." not in _xml_text(r).replace(" ", ""):
                            return r
                        def uniq(rx, k):
                            rr = re.sub(r'(<w:perm(?:Start|End)\s[^>]*?w:id=")(\d+)(")', lambda m: m.group(1) + str(int(m.group(2)) + k * 100000) + m.group(3), rx)
                            return re.sub(r'(<w:id w:val=")(-?\d+)("/>)', lambda m: m.group(1) + str(int(m.group(2)) + k * 100000) + m.group(3), rr)
                        return "".join(_fill_paragraphs(uniq(r, k), lambda tok, s=s: resolve(tok, s)) for k, s in enumerate(samples))
                    xml = _W_TR.sub(row, xml)

                    def per_analysis(m):
                        pxml = m.group(0)
                        if "{{analysis." not in _xml_text(pxml).replace(" ", ""):
                            return pxml
                        return "".join(_fill_paragraphs(pxml, lambda tok, a=a: resolve(tok, None, a)) for a in analyses)
                    xml = _W_P.sub(per_analysis, xml)
                xml = _fill_paragraphs(xml, resolve)
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return out.getvalue()


def docx_inspect(raw):
    """Validate an uploaded template and report what it contains."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        xml = zip_read(z, "word/document.xml").decode("utf-8")
    except Exception:
        raise ApiError(400, "That file is not a Word (.docx) document")
    texts = [_xml_text(m.group(0)) for m in _W_P.finditer(xml)]
    for name in z.namelist():
        if re.match(r"word/(header|footer)\d*\.xml$", name):
            texts += [_xml_text(m.group(0)) for m in _W_P.finditer(zip_read(z, name).decode("utf-8"))]
    tokens = sorted({t.strip() for tx in texts for t in _TOKEN.findall(tx)})
    has_row = any("{{sample." in _xml_text(m.group(0)).replace(" ", "") for m in _W_TR.finditer(xml))
    return tokens, has_row


def _docx_para(text, bold=False, size=None):
    rpr = ("<w:rPr>%s%s</w:rPr>" % ("<w:b/>" if bold else "", '<w:sz w:val="%d"/>' % size if size else "")) if (bold or size) else ""
    return '<w:p><w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (rpr, _x_escape(text))


def build_starter_docx():
    """A plain requisition layout (the same {{token}} format a lab's own template uses) that is used
    for labs without an uploaded template, and offered to admins as a starting point."""
    def cell(text, w, bold=False):
        return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>%s</w:tc>' % (w, _docx_para(text, bold)))
    cols = [("#", 500, "{{sample.n}}"), ("Sample ID", 2600, "{{sample.id}}"), ("Process point", 1700, "{{sample.stage}}"),
            ("Type", 900, "{{sample.type}}"), ("Description", 1500, "{{sample.description}}"),
            ("Container", 1500, "{{sample.container}}"), ("Collected", 1500, "{{sample.collected}}"),
            ("Analyses requested", 2800, "{{sample.analyses}}")]
    borders = ('<w:tblBorders>' + "".join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="808080"/>' % b
                                         for b in ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>')
    tbl = ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>%s</w:tblPr><w:tblGrid>%s</w:tblGrid>' % (
        borders, "".join('<w:gridCol w:w="%d"/>' % c[1] for c in cols))
        + "<w:tr>" + "".join(cell(c[0], c[1], True) for c in cols) + "</w:tr>"
        + "<w:tr>" + "".join(cell(c[2], c[1]) for c in cols) + "</w:tr></w:tbl>")
    body = "".join([
        _docx_para("{{company}}", True, 28), _docx_para("Laboratory analysis requisition", True, 36),
        _docx_para("Requisition no.: {{req_number}}        Date: {{date}}"),
        _docx_para("To: {{lab_name}}"), _docx_para("Attn: {{lab_contact}}    {{lab_email}}    {{lab_phone}}"),
        _docx_para("{{lab_address}}"), _docx_para(""),
        _docx_para("Production run: {{processing_lot}}        Run date: {{run_date}}        Product: {{product}}"),
        _docx_para("Requested by: {{requested_by}} ({{requested_by_email}})"),
        _docx_para("Samples submitted: {{sample_count}}"), _docx_para(""), tbl, _docx_para(""),
        _docx_para("Notes: {{notes}}"),
        '<w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/><w:pgMar w:top="1000" w:right="900" w:bottom="1000" w:left="900"/></w:sectPr>'])
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % body)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    return out.getvalue()


# ---------------------------------------------------------------------------------------
# Quality Control charts: the measurements that can be charted, and the process sections they are taken at
# ---------------------------------------------------------------------------------------
# section key -> (label, order along the process); feedstock comes first, laboratory results last
QC_SECTIONS = {
    "feedstock": ("Feedstock (per tote)", 0), "homogenization": ("Homogenization", 10), "extraction": ("Extraction", 20),
    "separation_filtrate": ("Separation - filtrate", 30), "separation_solids": ("Separation - solids", 31),
    "dilution": ("Dilution & preservation", 40), "packaging": ("Final product (packaging)", 50), "lab": ("Laboratory result", 60),
}
# QC_FIELD_REGISTRY label -> measurement key; measurement key -> (display name, unit, can only be >= 0)
QC_MEASURE_KEYS = {
    "pH": "ph", "TDS (%)": "tds", "Brix (%)": "brix", "Mannitol (%)": "mannitol", "TSliquid (%)": "ts_liquid", "ρliquid (g/mL)": "rho_liquid",
    "TSslurry (%)": "ts_slurry", "ρslurry (g/mL)": "rho_slurry", "%Moisture<sub>solids</sub>": "moisture_solids", "Solids Loading (%)": "solids_loading",
    "%Moisture<sub>centrifuge_solids</sub>": "moisture_centrifuge", "%Moisture<sub>screw_solids</sub>": "moisture_screw",
}
QC_MEASURE_INFO = {
    "ph": ("pH", "", True), "tds": ("TDS", "%", True), "brix": ("Brix", "%", True), "mannitol": ("Mannitol", "%", True),
    "ts_liquid": ("Total solids, liquid", "%", True), "rho_liquid": ("Density, liquid", "g/mL", True), "ts_slurry": ("Total solids, slurry", "%", True),
    "rho_slurry": ("Density, slurry", "g/mL", True), "moisture_solids": ("Moisture, solids", "%", True), "solids_loading": ("Solids loading", "%", True),
    "moisture_centrifuge": ("Moisture, centrifuge solids", "%", True), "moisture_screw": ("Moisture, screw solids", "%", True),
    "volume_variance": ("Dilution final volume variance", "%", False), "extraction_eff": ("Extraction efficiency (TDS gain)", "%", False),
}


def qc_section_of(stage, subtitle):
    if stage == "separation":
        return "separation_solids" if (subtitle or "").lower().startswith("solids") else "separation_filtrate"
    return stage if stage in QC_SECTIONS else "homogenization"


# ---- Previews: a lightweight HTML rendering of a Word / Excel document (stdlib only). Content-faithful (text, bold, tables, merged
# cells), not layout-faithful -- logos and exact fonts are left out. The result is shown in a sandboxed frame; every text is escaped.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _wq(tag):
    return "{%s}%s" % (_W_NS, tag)


def _html_esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def docx_to_html(raw):
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            root = ET.fromstring(zip_read(z, "word/document.xml"))
    except ValueError:
        return "<p><i>This document is too large to preview.</i></p>"
    except (KeyError, zipfile.BadZipFile, ET.ParseError):
        return "<p><i>This document could not be previewed.</i></p>"
    body = root.find(_wq("body"))

    def flag(rpr, name):
        e = rpr.find(_wq(name)) if rpr is not None else None
        return e is not None and e.get(_wq("val"), "1") not in ("0", "false")

    def para(p):
        out = []
        for r in p.iter(_wq("r")):
            rpr = r.find(_wq("rPr"))
            txt = ""
            for ch in r:
                if ch.tag == _wq("t"):
                    txt += _html_esc(ch.text or "")
                elif ch.tag == _wq("tab"):
                    txt += "&emsp;"
                elif ch.tag in (_wq("br"), _wq("cr")):
                    txt += "<br>"
                elif ch.tag == _wq("sym"):
                    txt += _html_esc(chr(int(ch.get(_wq("char"), "25A1"), 16))) if ch.get(_wq("char")) else ""
            if not txt:
                continue
            if flag(rpr, "b"):
                txt = "<b>%s</b>" % txt
            if flag(rpr, "i"):
                txt = "<i>%s</i>" % txt
            if flag(rpr, "u"):
                txt = "<u>%s</u>" % txt
            out.append(txt)
        ppr = p.find(_wq("pPr"))
        style, bullet = "", ""
        if ppr is not None:
            jc = ppr.find(_wq("jc"))
            if jc is not None and jc.get(_wq("val")) in ("center", "right"):
                style = ' style="text-align:%s"' % jc.get(_wq("val"))
            if ppr.find(_wq("numPr")) is not None:
                bullet = "&bull; "
        inner = "".join(out)
        return "<p%s>%s%s</p>" % (style, bullet, inner) if inner.strip() else '<p class="e">&nbsp;</p>'

    def table(t):
        rows = []
        for tr in t.findall(_wq("tr")):
            cells = []
            for tc in tr.findall(_wq("tc")):
                pr = tc.find(_wq("tcPr"))
                span, vm = 1, None
                if pr is not None:
                    gs = pr.find(_wq("gridSpan"))
                    span = int(gs.get(_wq("val"), "1")) if gs is not None else 1
                    vm = pr.find(_wq("vMerge"))
                if vm is not None and vm.get(_wq("val")) != "restart":
                    continue                        # the continuation of a vertically merged cell
                cells.append("<td%s>%s</td>" % (' colspan="%d"' % span if span > 1 else "", blocks(tc)))
            rows.append("<tr>%s</tr>" % "".join(cells))
        return "<table>%s</table>" % "".join(rows)

    def blocks(el):
        out = []
        for ch in el:
            if ch.tag == _wq("p"):
                out.append(para(ch))
            elif ch.tag == _wq("tbl"):
                out.append(table(ch))
            elif ch.tag == _wq("sdt"):
                c = ch.find(_wq("sdtContent"))
                if c is not None:
                    out.append(blocks(c))
        return "".join(out)
    return cap_html(blocks(body) if body is not None else "")


def cap_html(html):
    """A preview is shown in the browser: never send an unbounded page."""
    if len(html) <= MAX_PREVIEW_HTML_BYTES:
        return html
    return "<p><i>This preview was cut short because the document is very large. Download it to read it all.</i></p>"


def zip_read(z, name, limit=None):
    """Read one member of an untrusted zip without ever holding more than `limit` bytes (a tiny file can inflate to hundreds of MB)."""
    limit = limit or MAX_ZIP_MEMBER_BYTES
    info = z.getinfo(name)
    if info.file_size > limit:
        raise ValueError("%s is larger than %d MB when unpacked" % (name, limit // (1024 * 1024)))
    with z.open(info) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError("%s is larger than %d MB when unpacked" % (name, limit // (1024 * 1024)))
    return data


def xlsx_to_html(raw):
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(zip_read(z, "xl/sharedStrings.xml")).findall(ns + "si"):
                shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
        wb = ET.fromstring(zip_read(z, "xl/workbook.xml"))
        sheet_names = [sh.get("name") for sh in wb.iter(ns + "sheet")]
        files = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))[:20]
    except ValueError:
        return "<p><i>This workbook is too large to preview.</i></p>"
    except (KeyError, zipfile.BadZipFile, ET.ParseError):
        return "<p><i>This workbook could not be previewed.</i></p>"

    def col(ref):
        n = 0
        for ch in re.match(r"[A-Z]+", ref).group(0):
            n = n * 26 + ord(ch) - 64
        return n
    out, cut = [], False
    for idx, fn in enumerate(files):
        rows, width = {}, 0
        try:
            sheet_xml = ET.fromstring(zip_read(z, fn))
        except (ValueError, ET.ParseError):
            out.append("<p><i>A sheet could not be previewed.</i></p>")
            continue
        for c in sheet_xml.iter(ns + "c"):
            ref = c.get("r") or ""
            if not re.match(r"[A-Z]{1,3}\d+$", ref):
                continue
            t = c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(ns + "t"))
            else:
                v = c.find(ns + "v")
                val = (v.text or "") if v is not None else ""
                if t == "s" and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
            ci, ri = col(ref), int(re.search(r"\d+", ref).group(0))
            if ri > MAX_PREVIEW_ROWS or ci > MAX_PREVIEW_COLS:      # a lone cell far away must not make a huge empty grid
                cut = True
                continue
            rows.setdefault(ri, {})[ci] = val
            width = max(width, ci)
        if idx < len(sheet_names):
            out.append("<h4>%s</h4>" % _html_esc(sheet_names[idx]))
        body = []
        for ri in range(1, (max(rows) if rows else 0) + 1):
            cells = rows.get(ri, {})
            body.append("<tr>%s</tr>" % "".join("<td>%s</td>" % _html_esc(cells.get(ci, "")) for ci in range(1, width + 1)))
        out.append("<table class=\"x\">%s</table>" % "".join(body))
    if cut:
        out.append("<p><i>Only the first %d rows and %d columns are shown in this preview.</i></p>" % (MAX_PREVIEW_ROWS, MAX_PREVIEW_COLS))
    return cap_html("".join(out))


def container_volume(litres_each, name):
    """(amount, unit) one sample container holds: its litres_each (as mL) when set, else the amount in its name ('100 g sample bag' -> 100 g)."""
    if litres_each:
        return float(litres_each) * 1000.0, "mL"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mL|ml|L|g|kg)\b", name or "")
    if not m:
        return None
    amt, unit = float(m.group(1)), m.group(2)
    return {"ml": (amt, "mL"), "l": (amt * 1000.0, "mL"), "g": (amt, "g"), "kg": (amt * 1000.0, "g")}[unit.lower()]


def volume_text(parts):
    """[(amount, unit)] -> '200 mL' / '1.5 L' / '300 g' (summed per unit; mixed units are joined with ' + '); '' when unknown."""
    tot = {}
    for part in parts:
        if part:
            tot[part[1]] = tot.get(part[1], 0.0) + part[0]
    out = []
    for unit in ("mL", "g"):
        if unit in tot:
            v = tot[unit]
            big = {"mL": ("L", 1000.0), "g": ("kg", 1000.0)}[unit]
            out.append(("%s %s" % (coa_num(v / big[1], 4), big[0])) if v >= 1000 else ("%s %s" % (coa_num(v, 4), unit)))
    return " + ".join(out)


def consolidate_sample_rows(sample_rows, merge=None):
    """One line per Sample ID (+ the same description and requested tests): the lab gets a single line with the number of containers and their
    total volume instead of the same ID repeated. Lines are in the order the IDs first appear.
    merge: a function (analysis ids) -> the analysis fields of a row. When given (labs that list a Sample ID once), samples sharing an ID are merged
    whatever they request: the line asks for the union of their tests (so a mineral scan is sized for the union) and is numbered 1, 2, ..."""
    groups = {}
    for sr in sample_rows:
        key = sr["id"] if merge else (sr["id"], sr["report_description"], tuple(sorted(x.lower() for x in sr["analysis_names"])), tuple(sr.get("_minerals", [])))
        g = groups.setdefault(key, {"row": sr, "containers": {}, "vols": [], "qty": 0, "ids": []})
        g["qty"] += 1
        if sr["container"]:
            g["containers"][sr["container"]] = g["containers"].get(sr["container"], 0) + 1
        g["vols"].append(sr.get("volume"))
        g["ids"] += [i for i in sr.get("_ids", []) if i not in g["ids"]]
    out = []
    for n, g in enumerate(groups.values(), 1):
        row = dict(g["row"], container=", ".join(g["containers"]) if g["containers"] else "", container_qty=g["qty"], volume_total=volume_text(g["vols"]))
        if merge:
            row.update(merge(sorted(g["ids"])), n=str(n), volume_text=row["volume_total"], container_qty=str(g["qty"]))
        out.append(row)
    return out


def requisition_id_conflicts(sample_rows):
    """Sample IDs shared by DIFFERENT samples (another process point or other tests) -- these cannot be merged into one line."""
    variants = {}
    for sr in sample_rows:
        variants.setdefault(sr["id"], set()).add((sr["report_description"], tuple(sorted(x.lower() for x in sr["analysis_names"]))))
    return sorted(i for i, v in variants.items() if len(v) > 1)


def build_sample_sheet_xlsx(scalars, sample_rows, analyses, lines=None):
    """The "attached spreadsheet" some labs (e.g. Food Assure) ask for: one line per Sample ID (samples sharing an ID are consolidated)
    with the description to use on the report, the number of containers and their total volume, and an X under each test requested."""
    T = lambda v, st=0: ("t", v, st)
    lines = lines if lines is not None else consolidate_sample_rows(sample_rows)
    s = XlsxSheet("Samples")
    with_min = any(sr.get("_minerals") for sr in lines)
    s.set_widths([30, 52, 18, 10, 18, 14, 18] + ([46] if with_min else []) + [18] * len(analyses))
    span = 7 + (1 if with_min else 0) + len(analyses)
    s.title("Sample list - %s" % scalars.get("req_number", ""), span)
    s.row([T("Company", 7), T(scalars.get("company", ""))])
    s.row([T("Lab", 7), T(scalars.get("lab_name", ""))])
    s.row([T("Date submitted", 7), T(scalars.get("date_long", ""))])
    s.row([T("PO#", 7), T(scalars.get("po_number", ""))])
    s.row([T("Production run", 7), T(scalars.get("processing_lot", ""))])
    s.row([])
    s.row([T(h, 3) for h in ["Sample ID", "Sample description (as it should appear on the report)", "Collected", "Type", "Container", "Container Qty", "Total sample volume"]
                          + (["Minerals requested"] if with_min else [])]
          + [T(a["name"], 3) for a in analyses])
    for sr in lines:
        chosen = {x.lower() for x in sr["analysis_names"]}
        # the sheet describes a sample by lot + process point (no product), the collected DATE only, and a whole number of containers
        s.row([T(sr["id"]), T(sr["sheet_description"]), T((sr["collected"] or "")[:10]), T(sr["type"]), T(sr["container"]),
               ("n", int(round(float(sr["container_qty"] or 0))), 0), T(sr["volume_total"])]
              + ([T(sr.get("minerals", ""))] if with_min else [])
              + [T("X" if a["name"].lower() in chosen else "") for a in analyses])
    return xlsx_build([s])


# ---------------------------------------------------------------------------------------
# Production Log Summary PDF (standard library only: a small PDF writer using the built-in Helvetica fonts)
# ---------------------------------------------------------------------------------------
_HELV_W = {}
_HELV_B_W = {}


def _init_pdf_widths():
    chars = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
    reg = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
           556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015,
           667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
           278, 278, 278, 469, 556, 333,
           556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
           334, 260, 334, 584]
    bold = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
            556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975,
            722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
            333, 278, 333, 584, 556, 333,
            556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
            389, 280, 389, 584]
    for i, ch in enumerate(chars):
        _HELV_W[ch] = reg[i]
        _HELV_B_W[ch] = bold[i]
    for ch, w in (("°", 400), ("µ", 556), ("×", 584), ("–", 556), ("—", 1000), ("•", 350),
                  ("±", 584), ("·", 278), ("’", 222)):
        _HELV_W[ch] = w
        _HELV_B_W[ch] = w if ch != "—" else 1000


_init_pdf_widths()
_PDF_SUBS = {"≥": "¥", "≤": "¤", "→": "->", "✓": "", "⚠": "!", "ρ": "rho", "‐": "-", "‑": "-",
             "−": "-", " ": " ", "‘": "'", "“": '"', "”": '"', "…": "..."}


def _pdf_safe(s):
    s = "" if s is None else str(s)
    for k, v in _PDF_SUBS.items():
        s = s.replace(k, v)
    return s.encode("cp1252", "replace").decode("cp1252")


def pdf_text_width(s, size, bold=False):
    tbl = _HELV_B_W if bold else _HELV_W
    return sum(tbl.get(ch, 556) for ch in s) * size / 1000.0


def pdf_wrap(s, width, size, bold=False):
    s = _pdf_safe(s)
    out = []
    for para in s.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = word if not line else line + " " + word
            if pdf_text_width(cand, size, bold) <= width or not line:
                line = cand
                while pdf_text_width(line, size, bold) > width and len(line) > 1:     # a single over-long word: hard split
                    k = len(line)
                    while k > 1 and pdf_text_width(line[:k], size, bold) > width:
                        k -= 1
                    out.append(line[:k])
                    line = line[k:]
            else:
                out.append(line)
                line = word
        out.append(line)
    return out or [""]


_LOGO_CACHE = {}


def _pdf_logo(path, step=4):
    """The company logo (RGBA PNG) decoded and subsampled once, as (w, h, rgb bytes, alpha bytes); None if unavailable."""
    if path in _LOGO_CACHE:
        return _LOGO_CACHE[path]
    res = None
    try:
        d = open(path, "rb").read()
        pos, idat = 8, b""
        w = h = bd = ct = il = None
        while pos < len(d):
            ln = struct.unpack(">I", d[pos:pos + 4])[0]
            typ, data = d[pos + 4:pos + 8], d[pos + 8:pos + 8 + ln]
            pos += 12 + ln
            if typ == b"IHDR":
                w, h, bd, ct, _c, _f, il = struct.unpack(">IIBBBBB", data)
            elif typ == b"IDAT":
                idat += data
        if bd == 8 and ct == 6 and il == 0:
            raw = zlib.decompress(idat)
            bpp, stride = 4, w * 4
            prev = bytearray(stride)
            rgb, alpha, p = bytearray(), bytearray(), 0
            ow = (w + step - 1) // step
            oh = 0
            for y in range(h):
                ft = raw[p]
                line = bytearray(raw[p + 1:p + 1 + stride])
                p += 1 + stride
                if ft == 1:
                    for i in range(bpp, stride):
                        line[i] = (line[i] + line[i - bpp]) & 255
                elif ft == 2:
                    line = bytearray((a + b) & 255 for a, b in zip(line, prev))
                elif ft == 3:
                    for i in range(stride):
                        left = line[i - bpp] if i >= bpp else 0
                        line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
                elif ft == 4:
                    for i in range(stride):
                        a = line[i - bpp] if i >= bpp else 0
                        b = prev[i]
                        c = prev[i - bpp] if i >= bpp else 0
                        pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                        pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                        line[i] = (line[i] + pr) & 255
                prev = line
                if y % step == 0:
                    oh += 1
                    for x in range(0, w, step):
                        o = x * 4
                        rgb += line[o:o + 3]
                        alpha.append(line[o + 3])
            res = (ow, oh, bytes(rgb), bytes(alpha))
    except Exception:
        res = None
    _LOGO_CACHE[path] = res
    return res


class PdfBuilder:
    W, H, M = 612.0, 792.0, 40.0
    TEAL = (0.10, 0.42, 0.39)
    TEAL_LIGHT = (0.90, 0.95, 0.94)
    GRAY = (0.42, 0.46, 0.46)
    LINE = (0.80, 0.85, 0.84)
    RED = (0.70, 0.16, 0.12)
    AMBER = (0.70, 0.45, 0.05)
    GREEN = (0.12, 0.50, 0.28)

    def __init__(self, title, footer_left, logo=None, header_right=""):
        self.title, self.footer_left, self.logo, self.header_right = title, footer_left, logo, header_right
        self.pages = []
        self.y = 0.0
        self.add_page()

    # -- low level -- #
    @staticmethod
    def _col(c):
        return "%.3f %.3f %.3f" % c

    def _emit(self, s):
        self.pages[-1].append(s)

    def text(self, x, y, s, size=9, bold=False, color=(0, 0, 0), align="l", width=None, italic=False):
        s = _pdf_safe(s)
        if align in ("r", "c") and width is not None:
            tw = pdf_text_width(s, size, bold)
            x = x + width - tw if align == "r" else x + (width - tw) / 2.0
        font = "F2" if bold else ("F3" if italic else "F1")
        esc = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self._emit("BT /%s %.2f Tf %s rg %.2f %.2f Td (%s) Tj ET" % (font, size, self._col(color), x, self.H - y, esc))

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.6):
        ops = ["q"]
        if fill:
            ops.append("%s rg" % self._col(fill))
        if stroke:
            ops.append("%s RG %.2f w" % (self._col(stroke), lw))
        ops.append("%.2f %.2f %.2f %.2f re %s Q" % (x, self.H - y - h, w, h, "B" if fill and stroke else ("f" if fill else "S")))
        self._emit(" ".join(ops))

    def hline(self, x1, x2, y, color=None, lw=0.6):
        self._emit("q %s RG %.2f w %.2f %.2f m %.2f %.2f l S Q" % (self._col(color or self.LINE), lw, x1, self.H - y, x2, self.H - y))

    # -- pages -- #
    def add_page(self):
        self.pages.append([])
        n = len(self.pages)
        self.y = self.M + (46 if n == 1 else 26)
        return n

    def space(self, h):
        if self.y + h > self.H - self.M - 18:
            self.add_page()

    # -- components -- #
    def heading(self, s):
        self.space(72)          # keep a heading together with the start of what follows
        self.y += 8
        self.text(self.M, self.y + 9, s.upper(), 9.5, True, self.TEAL)
        self.hline(self.M, self.W - self.M, self.y + 14, self.TEAL, 0.9)
        self.y += 22

    def note(self, s, size=7.6, color=None, italic=True):
        for ln in pdf_wrap(s, self.W - 2 * self.M, size):
            self.space(size + 4)
            self.text(self.M, self.y + size, ln, size, False, color or self.GRAY, italic=italic)
            self.y += size + 3

    def kv_grid(self, pairs, cols=2, size=8.4):
        cw = (self.W - 2 * self.M) / cols
        for i in range(0, len(pairs), cols):
            self.space(26)
            for j, (k, v) in enumerate(pairs[i:i + cols]):
                x = self.M + j * cw
                self.text(x, self.y + 7, k.upper(), 6.4, True, self.GRAY)
                lines = pdf_wrap(v if v not in (None, "") else "-", cw - 10, size)
                self.text(x, self.y + 18, lines[0] if len(lines) == 1 else lines[0], size, False, (0.1, 0.1, 0.1))
            self.y += 26

    def kpis(self, cards):
        """cards: [(label, value, sub)]"""
        n = len(cards)
        gap = 6.0 if n > 5 else 8.0
        cw = (self.W - 2 * self.M - gap * (n - 1)) / n
        pad, lsize, vsize, ssize = (6.5, 5.6, 12.5, 6.0) if n > 5 else (9, 6.2, 15, 6.6)
        self.space(54)
        for i, (label, value, sub) in enumerate(cards):
            x = self.M + i * (cw + gap)
            self.rect(x, self.y, cw, 46, fill=self.TEAL_LIGHT)
            self.rect(x, self.y, 2.5, 46, fill=self.TEAL)
            self.text(x + pad, self.y + 11, label.upper(), lsize, True, self.GRAY)
            self.text(x + pad, self.y + 29, value, vsize, True, self.TEAL)
            if sub:
                self.text(x + pad, self.y + 40, sub, ssize, False, self.GRAY)
        self.y += 56

    def table(self, headers, rows, widths, aligns=None, size=7.8, zebra=True, bold_first=False, head_fill=None):
        """rows: list of lists; a cell is a string or (string, {'bold':bool,'color':rgb}). Header repeats on a new page."""
        total = sum(widths)
        scale = (self.W - 2 * self.M) / total
        widths = [w * scale for w in widths]
        aligns = aligns or ["l"] * len(widths)
        pad, lh = 3.2, size + 2.2

        def head():
            hsize = size - 0.4
            wrapped = [pdf_wrap(h, w - 2 * pad, hsize, True) for h, w in zip(headers, widths)]
            hh = max(len(x) for x in wrapped) * (hsize + 2.0) + 2 * pad - 1
            self.space(hh + lh + 2 * pad + 14)
            self.rect(self.M, self.y, sum(widths), hh, fill=head_fill or self.TEAL)
            x = self.M
            for lines, w, a in zip(wrapped, widths, aligns):
                for li, ln in enumerate(lines):
                    self.text(x + pad, self.y + pad + hsize - 1.0 + li * (hsize + 2.0), ln, hsize, True, (1, 1, 1), a, w - 2 * pad)
                x += w
            self.y += hh
        head()
        for ri, row in enumerate(rows):
            cells = []
            for ci, c in enumerate(row):
                txt, st = (c if isinstance(c, tuple) else (c, {}))
                bold = st.get("bold", bold_first and ci == 0)
                cells.append((pdf_wrap("-" if txt in (None, "") else txt, widths[ci] - 2 * pad, size, bold), bold, st.get("color", (0.1, 0.1, 0.1))))
            h = max(len(c[0]) for c in cells) * lh + 2 * pad - 1
            if self.y + h > self.H - self.M - 18:
                self.add_page()
                head()
            if zebra and ri % 2 == 1:
                self.rect(self.M, self.y, sum(widths), h, fill=(0.965, 0.975, 0.972))
            x = self.M
            for (lines, bold, color), w, a in zip(cells, widths, aligns):
                for li, ln in enumerate(lines):
                    self.text(x + pad, self.y + pad + size - 1.2 + li * lh, ln, size, bold, color, a, w - 2 * pad)
                x += w
            self.y += h
            self.hline(self.M, self.M + sum(widths), self.y, self.LINE, 0.4)
        self.y += 6

    def chip(self, x, y, s, color):
        w = pdf_text_width(_pdf_safe(s), 8, True) + 14
        self.rect(x, y, w, 15, fill=color)
        self.text(x + 7, y + 10.8, s, 8, True, (1, 1, 1))
        return w

    # -- output -- #
    def build(self):
        n = len(self.pages)
        # per-page header + footer
        for i in range(n):
            cur = self.pages[i]
            saved = self.pages
            self.pages = [cur]
            top = self.M - 6
            if self.logo:
                self._emit("q 20 0 0 21 %.2f %.2f cm /Im1 Do Q" % (self.M, self.H - top - 19))
            self.text(self.M + (26 if self.logo else 0), top + 8, "CASCADIA SEAWEED", 8.5, True, self.TEAL)
            self.text(self.M + (26 if self.logo else 0), top + 17, "Manufacturing records", 6.8, False, self.GRAY)
            self.text(self.M, top + 8, self.header_right, 8.5, True, (0.2, 0.2, 0.2), "r", self.W - 2 * self.M)
            self.hline(self.M, self.W - self.M, top + 24, self.TEAL, 1.0)
            self.hline(self.M, self.W - self.M, self.H - self.M + 2, self.LINE, 0.5)
            self.text(self.M, self.H - self.M + 13, self.footer_left, 6.8, False, self.GRAY)
            self.text(self.M, self.H - self.M + 13, "Page %d of %d" % (i + 1, n), 6.8, False, self.GRAY, "r", self.W - 2 * self.M)
            self.pages = saved
        objs = {}

        def put(i, b):
            objs[i] = b if isinstance(b, bytes) else b.encode("latin-1")
        put(1, "<< /Type /Catalog /Pages 2 0 R >>")
        put(3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding 6 0 R >>")
        put(4, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding 6 0 R >>")
        put(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding 6 0 R >>")
        put(6, "<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [164 /lessequal 165 /greaterequal] >>")
        nxt = 7
        xobj = ""
        if self.logo:
            w, h, rgb, alpha = self.logo
            img_id, mask_id = nxt, nxt + 1
            nxt += 2
            z = zlib.compress(rgb)
            put(img_id, ("<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                         "/SMask %d 0 R /Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, mask_id, len(z))).encode("latin-1") + z + b"\nendstream")
            za = zlib.compress(alpha)
            put(mask_id, ("<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
                          "/Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, len(za))).encode("latin-1") + za + b"\nendstream")
            xobj = " /XObject << /Im1 %d 0 R >>" % img_id
        kids = []
        for pg in self.pages:
            data = zlib.compress("\n".join(pg).encode("cp1252", "replace"))      # WinAnsiEncoding == cp1252
            cid, pid = nxt, nxt + 1
            nxt += 2
            put(cid, ("<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)).encode("latin-1") + data + b"\nendstream")
            put(pid, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] /Contents %d 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >>%s >> >>"
                % (self.W, self.H, cid, xobj))
            kids.append("%d 0 R" % pid)
        put(2, "<< /Type /Pages /Count %d /Kids [%s] >>" % (len(kids), " ".join(kids)))
        info = nxt
        put(info, "<< /Title (%s) /Producer (KelpWorks ERP) >>" % _pdf_safe(self.title).replace("(", "").replace(")", ""))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for i in range(1, info + 1):
            offsets[i] = len(out)
            out += ("%d 0 obj\n" % i).encode("latin-1") + objs[i] + b"\nendobj\n"
        xref = len(out)
        out += ("xref\n0 %d\n0000000000 65535 f \n" % (info + 1)).encode("latin-1")
        for i in range(1, info + 1):
            out += ("%010d 00000 n \n" % offsets[i]).encode("latin-1")
        out += ("trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (info + 1, info, xref)).encode("latin-1")
        return bytes(out)


def _pf(v, d=1, unit="", signed=False):
    """number -> text ('-' when missing)"""
    if v is None or v == "":
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = ("{:+,.%df}" if signed else "{:,.%df}") % d
    return s.format(x) + (" " + unit if unit else "")


def build_run_summary_pdf(S, logo_path=None):
    """S: the dict produced by Handler._run_summary_data. One clean document: header facts, headline numbers, a stage-by-stage
    table of key results, a quality scorecard (metric x stage), a sample summary (process point x sample type), materials,
    output and sign-off. Only key results -- the full production log remains the record."""
    st = S["stages"]
    logo = _pdf_logo(logo_path) if logo_path else None
    pdf = PdfBuilder("Production Log Summary %s" % S["lot"], "%s  ·  Production Log Summary  ·  generated %s by %s" % (
        S["lot"], S["generatedAt"], S["generatedBy"] or "KelpWorks"), logo, S["lot"])
    M, W = pdf.M, pdf.W
    # ---- title block
    pdf.text(M, 84, "Production Log Summary", 20, True, pdf.TEAL)
    pdf.text(M, 102, S["lot"] + "  ·  " + (S["product"] or "-"), 11, True, (0.15, 0.15, 0.15))
    state_col = {"released": pdf.GREEN, "legacy": pdf.GREEN, "pending_review": pdf.AMBER, "pending_release": pdf.AMBER,
                 "amending": pdf.AMBER, "rejected": pdf.RED}.get(S["releaseState"], pdf.GRAY)
    lab = S["releaseLabel"] or "-"
    cw = pdf_text_width(_pdf_safe(lab), 8, True) + 14
    pdf.chip(W - M - cw, 72, lab, state_col)
    pdf.y = 112
    pdf.hline(M, W - M, pdf.y, pdf.LINE, 0.5)
    pdf.y += 6
    pdf.kv_grid([("Run date", S["runDate"]), ("Location", S["location"]),
                 ("Operators", S["operators"]), ("Species / feedstock", S["feedstockSummary"]),
                 ("Finalized", S["finalizedText"]), ("Log revision", S["revisionText"])], cols=3)
    # ---- headline numbers
    final_ph, final_tds = st["packaging"].get("qcPh"), st["packaging"].get("tdsPct")
    conv = (S["outputL"] / S["measuredKg"]) if (S["outputL"] and S["measuredKg"]) else None
    he0, ex0 = st["homogenization"], st["extraction"]
    eff0 = None
    if ex0.get("tdsPct") not in (None, "") and he0.get("tdsPct"):
        eff0 = (ex0["tdsPct"] - he0["tdsPct"]) / he0["tdsPct"] * 100.0
    pdf.kpis([
        ("Feedstock", _pf(S["measuredKg"], 0, "kg"), "%d tote%s" % (S["toteCount"], "" if S["toteCount"] == 1 else "s")),
        ("Output", _pf(S["outputL"], 0, "L"), "%s units" % _pf(S["unitsPackaged"], 0)),
        ("Conversion", _pf(conv, 2, "L/kg"), "output / feedstock"),
        ("Extraction eff.", _pf(eff0, 1, "%", True), "TDS gain"),
        ("Final pH", _pf(final_ph, 2), ("target " + _pf(S["targetPh"], 1)) if S["targetPh"] is not None else ""),
        ("Final TDS", _pf(final_tds, 2, "%"), ("target " + _pf(S["targetTds"], 2) + " %") if S["targetTds"] is not None else ""),
        ("Dilution water", _pf(S["totalWaterL"], 0, "L"), "total added"),
    ])

    # ---- process at a glance (key results per stage)
    pdf.heading("Process at a glance")
    he, ex, sp, pa, di, pk = st["homogenization"], st["extraction"], st["separation"], st["pasteurization"], st["dilution"], st["packaging"]

    def join(parts):
        return "  ·  ".join(p for p in parts if p) or "Not recorded"

    def when(iso):
        return (iso or "").replace("T", " ")[:16] or "-"

    def has(v):
        return v is not None and v != ""
    eff = None
    if has(ex.get("tdsPct")) and has(he.get("tdsPct")) and he["tdsPct"]:
        eff = (ex["tdsPct"] - he["tdsPct"]) / he["tdsPct"] * 100.0
    rows = [
        ["Feedstock", when(S["firstLoaded"]),
         join([S["feedstockLine"], ("pH %s" % S["phRange"]) if S["phRange"] else "", S["orpLine"],
               ("%d rejected" % S["rejectedCount"]) if S["rejectedCount"] else "all accepted"])],
        ["Homogenization", when(he.get("startedAt")),
         join([("slurry " + _pf(he.get("slurryL"), 0, "L")) if has(he.get("slurryL")) else "",
               ("output " + _pf(he.get("outputL"), 0, "L")) if has(he.get("outputL")) else "",
               ("solids loading " + _pf(he.get("solidsLoadingPct"), 1, "%")) if has(he.get("solidsLoadingPct")) else
               (("wet solids " + _pf(he.get("pctWetSolids"), 1, "%")) if has(he.get("pctWetSolids")) else ""),
               ("initial pH " + _pf(he.get("initialPh"), 2)) if has(he.get("initialPh")) else ""])],
        ["Extraction", when(ex.get("startedAt")),
         join([("amplitude " + _pf(ex.get("amplitudePct"), 0, "%")) if has(ex.get("amplitudePct")) else "",
               ("flow " + _pf(ex.get("flowrateLpm"), 0, "L/min")) if has(ex.get("flowrateLpm")) else "",
               ("pressure " + _pf(ex.get("pressurePsi"), 0, "psi")) if has(ex.get("pressurePsi")) else "",
               ("power " + _pf(ex.get("startingPowerW"), 0, "W")) if has(ex.get("startingPowerW")) else ""])],
        ["Separation", when(sp.get("startedAt")),
         join([("wet solids " + _pf(sp.get("wetSolidsWtKg"), 1, "kg")) if has(sp.get("wetSolidsWtKg")) else "",
               ("moisture " + _pf(sp.get("pctMoisture"), 1, "%")) if has(sp.get("pctMoisture")) else "",
               ("screw moisture " + _pf(sp.get("pctMoistureScrew"), 1, "%")) if has(sp.get("pctMoistureScrew")) else "",
               ("flow " + _pf(sp.get("flowrateLpm"), 0, "L/min")) if has(sp.get("flowrateLpm")) else "",
               ("mesh " + _pf(sp.get("meshMicron"), 0, "µm")) if has(sp.get("meshMicron")) else ""])],
        ["Pasteurization", when(pa.get("startedAt")),
         join([("product " + _pf(pa.get("productSetpointC"), 0, "°C")) if has(pa.get("productSetpointC")) else "",
               ("boiler " + _pf(pa.get("boilerSetpointC"), 0, "°C")) if has(pa.get("boilerSetpointC")) else "",
               ("volume " + _pf(pa.get("totalVolumeL"), 0, "L")) if has(pa.get("totalVolumeL")) else "",
               ("TDS " + _pf(pa.get("tdsPct"), 2, "%")) if has(pa.get("tdsPct")) else ""])],
        ["Dilution & preservation", "",
         join([("transferred " + _pf(di.get("productTransferredL"), 0, "L")) if has(di.get("productTransferredL")) else "",
               ("final volume " + _pf(di.get("fillLevelTank6abL"), 0, "L")) if has(di.get("fillLevelTank6abL")) else "",
               ("variance " + _pf(di.get("finalVariancePct"), 1, "%", True)) if has(di.get("finalVariancePct")) else "",
               ("pH " + _pf(di.get("measuredPh"), 1)) if has(di.get("measuredPh")) else "",
               S["reagentLine"], ("%d extra dilution pass(es)" % S["extraPasses"]) if S["extraPasses"] else ""])],
        ["Packaging", when(pk.get("packagedAt")), join([S["packagingLine"], ("QC pH " + _pf(pk.get("qcPh"), 2)) if has(pk.get("qcPh")) else ""])],
    ]
    pdf.table(["Stage", "Started", "Key results"], rows, [92, 78, 362], ["l", "l", "l"], size=7.8, bold_first=True)

    # ---- quality scorecard: one matrix, metric x stage
    pdf.heading("Quality scorecard")
    cols = ["Homog.", "Extraction", "Separation", "Pasteur.", "Dilution", "Packaging"]

    def metric(label, unit, vals, target=None, dec=2):
        v = [(_pf(x, dec) if has(x) else "") for x in vals]
        if not any(v):
            return None
        final = vals[5]
        delta = ""
        if has(final) and target is not None:
            d = final - target
            delta = ("%+.*f" % (dec, d)) + (" (on target)" if abs(d) < 10 ** (-dec) / 2 else "")
        return [(label + ((" (" + unit + ")") if unit else ""), {"bold": True}), _pf(target, dec) if target is not None else "", *v, delta]
    qrows = [r for r in (
        metric("pH", "", [he.get("qcPh"), ex.get("qcPh"), sp.get("liquidQcPh"), None, di.get("measuredPh"), pk.get("qcPh")], S["targetPh"], 2),
        metric("TDS", "%", [he.get("tdsPct"), ex.get("tdsPct"), sp.get("liquidTdsPct"), pa.get("tdsPct"), None, pk.get("tdsPct")], S["targetTds"], 2),
        metric("Brix", "%", [he.get("brixPct"), ex.get("brixPct"), sp.get("liquidBrixPct"), None, None, pk.get("brixPct")], None, 2),
        metric("Mannitol", "%", [he.get("mannitolPct"), ex.get("mannitolPct"), sp.get("liquidMannitolPct"), None, None, pk.get("mannitolPct")], None, 2),
        metric("Total solids, liquid", "%", [he.get("tsLiquidPct"), ex.get("tsLiquidPct"), sp.get("liquidTsLiquidPct"), None, None, pk.get("tsLiquidPct")], None, 2),
        metric("Density, liquid", "g/mL", [he.get("rhoLiquidGMl"), ex.get("rhoLiquidGMl"), sp.get("liquidRhoLiquidGMl"), None, None, pk.get("rhoLiquidGMl")], None, 3),
        metric("Total solids, slurry", "%", [he.get("tsSlurryPct"), ex.get("tsSlurryPct"), None, None, None, None], None, 2),
        metric("Total solids, solids", "%", [he.get("tsSolidsPct"), ex.get("tsSolidsPct"), None, None, None, None], None, 2),
    ) if r]
    if qrows:
        pdf.table(["Metric", "Target"] + cols + ["Final vs target"], qrows, [112, 38, 50, 52, 52, 46, 46, 52, 70],
                  ["l", "r", "r", "r", "r", "r", "r", "r", "r"], size=7.6)
    else:
        pdf.note("No quality readings were recorded for this run.")
    qc = S.get("qcRecorded")
    pdf.note("Readings are listed under the stage they were taken at (blank = not measured there). %s%s" % (
        ("%d of %d QC check fields recorded. " % (qc[0], qc[1])) if qc else "",
        ("Feedstock pH: %s." % S["phRange"]) if S["phRange"] else ""))

    # ---- samples, grouped: process point x sample type
    pdf.heading("Samples & lab work")
    smp = S["samples"]
    if smp:
        descs = ["Microbial", "Metals & Nutrients", "Retention", "Proximate Analysis", "R&D", "Other"]
        shown = [d for d in descs if any(s["description"] == d for s in smp)] or descs[:1]
        extra = any(s["description"] not in descs for s in smp)
        order = []
        for s in smp:
            if s["stageLabel"] not in order:
                order.append(s["stageLabel"])
        srows = []
        for stg in order:
            grp = [s for s in smp if s["stageLabel"] == stg]
            cnt = lambda d: sum(1 for s in grp if s["description"] == d)
            srows.append([(stg, {"bold": True}), when(min((s["collectedAt"] or "") for s in grp))]
                         + [str(cnt(d)) if cnt(d) else " " for d in shown]
                         + ([str(sum(1 for s in grp if s["description"] not in descs))] if extra else [])
                         + [(str(len(grp)), {"bold": True})])
        tot = ["Total", " "] + [str(sum(1 for s in smp if s["description"] == d)) for d in shown] \
            + ([str(sum(1 for s in smp if s["description"] not in descs))] if extra else []) + [str(len(smp))]
        srows.append([(c, {"bold": True}) if isinstance(c, str) else c for c in tot])
        n = len(shown) + (1 if extra else 0)
        pdf.table(["Process point", "Collected"] + shown + (["Other types"] if extra else []) + ["Total"], srows,
                  [112, 82] + [58] * n + [40], ["l", "l"] + ["r"] * (n + 1), size=7.6)
        byst = {}
        for s in smp:
            byst[s["status"]] = byst.get(s["status"], 0) + 1
        label = {"available": "in inventory", "in_cart": "in the lab cart", "submitted": "sent to a lab", "removed": "removed"}
        cont = {}
        for s in smp:
            if s["container"]:
                cont[s["container"]] = cont.get(s["container"], 0) + 1
        pdf.note("%d samples: %s.  Containers: %s." % (len(smp), ", ".join("%d %s" % (v, label.get(k, k)) for k, v in byst.items()),
                                                      ", ".join("%d x %s" % (v, k) for k, v in cont.items()) or "-"), italic=False)
    else:
        pdf.note("No samples were logged for this run.")
    if S["requisitions"]:
        pdf.table(["Requisition", "Lab", "PO #", "Samples", "Analyses requested", "Date"],
                  [[(q["reqNumber"], {"bold": True}), q["labName"], q["poNumber"] or "", str(q["nSamples"]), q["analyses"], q["date"]]
                   for q in S["requisitions"]], [78, 96, 52, 42, 200, 64], ["l", "l", "l", "r", "l", "l"], size=7.4)

    # ---- materials + output
    pdf.heading("Materials used")
    if S["materials"]:
        seen_cat = set()
        mrows = []
        for c, i, q, u in S["materials"]:
            mrows.append([(c if c not in seen_cat else " ", {"bold": True}), i, _pf(q, 1 if q % 1 else 0), u])
            seen_cat.add(c)
        pdf.table(["Category", "Item", "Used", "Unit"], mrows,
                  [100, 250, 80, 60], ["l", "l", "r", "l"], size=7.6)
        pdf.note("From the inventory ledger for this run (net of refunds).")
    else:
        pdf.note("No inventory usage is recorded against this run.")
    pdf.heading("Finished product")
    if S["fgLots"]:
        pdf.table(["Finished-goods lot", "Package", "Units", "Litres", "Location", "Status"],
                  [[(f["lot"], {"bold": True}), f["packageSize"], _pf(f["qty"], 0), _pf(f["litres"], 0), f["location"] or "", f["statusLabel"]]
                   for f in S["fgLots"]], [180, 100, 40, 50, 100, 62], ["l", "l", "r", "r", "l", "l"], size=7.6)
    else:
        pdf.note("No finished-goods lots were created.")

    # ---- sign-off
    pdf.heading("Sign-off & record")
    pdf.kv_grid([("Finalized", S["finalizedText"]), ("Production review", S["reviewText"]), ("Quality release", S["releaseText"]),
                 ("Required fields", S["completenessText"]), ("Revisions", S["revisionText"]), ("Documents on file", str(S["nDocuments"]))], cols=3)
    pdf.note("This summary shows key results only. The complete production log, photos and amendment history remain the record of this run.")
    return pdf.build()


# What a production-log sign-off hashes (Handler._release_snapshot) is versioned. If you
# change that snapshot's content or format, BUMP this: on the next boot every run that is
# currently reviewed/released is re-hashed (one SYSTEM audit event each), so a format
# change is never mistaken for someone altering a signed log.
RELEASE_SNAPSHOT_VERSION = 5


def rebaseline_release_hashes(conn):
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver >= RELEASE_SNAPSHOT_VERSION:
        return
    h = Handler.__new__(Handler)   # snapshot helpers only need a connection, not a request
    for r in conn.execute("SELECT id, release_state FROM production_runs"
                          " WHERE release_state IN ('pending_release','released') AND release_review_hash IS NOT NULL").fetchall():
        new_hash = h._release_snapshot_hash(conn, r["id"])
        conn.execute("UPDATE production_runs SET release_review_hash=? WHERE id=?", (new_hash, r["id"]))
        release_log(conn, r["id"], "rebaseline", None, capacity="System",
                    meaning="Production-log snapshot format changed (version %d); the signed log hash was recomputed "
                            "from the current log." % RELEASE_SNAPSHOT_VERSION,
                    log_hash=new_hash, detail={"snapshotVersion": RELEASE_SNAPSHOT_VERSION, "state": r["release_state"]})
    conn.execute("PRAGMA user_version=%d" % RELEASE_SNAPSHOT_VERSION)


# Item # nomenclature: <CATEGORY>-<3-digit sequence>, e.g. RGT-001. The category prefix tells people what kind of item it is at a
# glance; the number is a plain sequence that is assigned once, never reused and never describes the item (names, sizes and
# suppliers change -- the number doesn't). Everything an item IS lives in its name / type / unit fields instead.
#   RGT reagent / chemical   CIP cleaning agent   PKG packaging container   SMP sample container   LBL finished-good label
ITEM_PREFIX_ORDER = ("RGT", "CIP", "PKG", "SMP", "LBL")


def item_category(row):
    if row["label_sku_code"]:
        return "LBL"
    if row["is_container"]:
        return "SMP" if row["is_sample_container"] else "PKG"
    return "CIP" if row["is_cip_agent"] else "RGT"


def next_item_number(conn, prefix):
    n = 0
    for r in conn.execute("SELECT item_number FROM consumables WHERE item_number LIKE ?", (prefix + "-%",)):
        m = re.fullmatch(re.escape(prefix) + r"-(\d+)", r["item_number"] or "")
        if m:
            n = max(n, int(m.group(1)))
    return "%s-%03d" % (prefix, n + 1)


def assign_item_numbers(conn):
    """Give every inventory item that has no Item # the next number in its category (idempotent; runs at boot and when an
    item is created). Existing items are numbered in a tidy order: reagents by type then name, labels by SKU then package."""
    sku_names = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM fg_skus")}
    rows = conn.execute("SELECT * FROM consumables WHERE item_number IS NULL OR TRIM(item_number)=''").fetchall()

    def key(r):
        cat = item_category(r)
        if cat == "LBL":
            sub = (sku_names.get(r["label_sku_code"], r["label_sku_code"] or "").lower(), (r["label_package"] or "").lower())
        elif cat == "RGT":
            sub = ((r["reagent_type"] or r["name"] or "").lower(), (r["name"] or "").lower())
        else:
            sub = ((r["name"] or "").lower(), "")
        return (ITEM_PREFIX_ORDER.index(cat), sub)
    for r in sorted(rows, key=key):
        conn.execute("UPDATE consumables SET item_number=? WHERE id=?", (next_item_number(conn, item_category(r)), r["id"]))


def _rename_consumable(conn, old_name, new_name):
    """One-time, idempotent rename of a consumable, propagated to every place
    that stores its name by value instead of by id (Packaging table entries,
    Sample Point containers, past FG lots). A no-op if old_name doesn't exist
    or new_name is already taken (e.g. an admin renamed it there first)."""
    if (old_name != new_name
            and conn.execute("SELECT 1 FROM consumables WHERE name=?", (old_name,)).fetchone()
            and not conn.execute("SELECT 1 FROM consumables WHERE name=?", (new_name,)).fetchone()):
        conn.execute("UPDATE consumables SET name=? WHERE name=?", (new_name, old_name))
        conn.execute("UPDATE run_packaging_entries SET container_unit=? WHERE container_unit=?",
                     (new_name, old_name))
        conn.execute("UPDATE run_sample_points SET container=? WHERE container=?", (new_name, old_name))
        conn.execute("UPDATE fg_lots SET package_size=? WHERE package_size=?", (new_name, old_name))


def _merge_consumable(conn, from_name, into_name):
    """One-time, idempotent merge of two consumable rows that turned out to
    be the same physical item -- sums on-hand, keeps whichever
    litres_each/is_container/is_sample_container value is set on either row,
    re-points every reference and past ledger entry from from_name's row to
    into_name's, then drops the duplicate. A no-op if either name is missing
    (e.g. already merged, or from_name never existed here)."""
    src = conn.execute("SELECT * FROM consumables WHERE name=?", (from_name,)).fetchone()
    dst = conn.execute("SELECT * FROM consumables WHERE name=?", (into_name,)).fetchone()
    if not src or not dst:
        return
    conn.execute(
        "UPDATE consumables SET on_hand=on_hand+?, litres_each=COALESCE(litres_each,?),"
        " is_sample_container=MAX(is_sample_container,?), is_container=MAX(is_container,?) WHERE id=?",
        (src["on_hand"], src["litres_each"], src["is_sample_container"], src["is_container"], dst["id"]))
    conn.execute("UPDATE consumable_txns SET consumable_id=? WHERE consumable_id=?", (dst["id"], src["id"]))
    conn.execute("UPDATE run_packaging_entries SET container_unit=? WHERE container_unit=?",
                 (into_name, from_name))
    conn.execute("UPDATE run_sample_points SET container=? WHERE container=?", (into_name, from_name))
    conn.execute("UPDATE fg_lots SET package_size=? WHERE package_size=?", (into_name, from_name))
    conn.execute("DELETE FROM consumables WHERE id=?", (src["id"],))


def migrate(conn):
    """Idempotent schema migrations for databases created before a column existed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tote_lots)")}
    if "ph_updated" not in cols:
        conn.execute("ALTER TABLE tote_lots ADD COLUMN ph_updated TEXT")
    if "disposed_date" not in cols:
        conn.execute("ALTER TABLE tote_lots ADD COLUMN disposed_date TEXT")
    for col, decl in [
        ("stabilization_method", "TEXT DEFAULT 'Citric acid'"), ("storage_unit", "TEXT DEFAULT 'Tote'"),
        ("storage_source", "TEXT"), ("orp", "REAL"), ("orp_updated", "TEXT"), ("notes", "TEXT"),
        ("received_date", "TEXT"),
        ("grind", "TEXT DEFAULT 'Coarse'"), ("preproc_batch_id", "INTEGER"), ("solids_pct", "REAL"),
    ]:
        if col not in cols:
            conn.execute("ALTER TABLE tote_lots ADD COLUMN %s %s" % (col, decl))
    tscols = {r["name"] for r in conn.execute("PRAGMA table_info(tote_stability_log)")}
    for col, decl in [("run_id", "INTEGER"), ("attachment_id", "INTEGER")]:
        if col not in tscols:
            conn.execute("ALTER TABLE tote_stability_log ADD COLUMN %s %s" % (col, decl))
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
    # Product-release sign-off permissions (independent of the admin/user role).
    if "token_version" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    if "is_production_manager" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN is_production_manager INTEGER NOT NULL DEFAULT 0")
    if "is_quality_manager" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN is_quality_manager INTEGER NOT NULL DEFAULT 0")
    if "can_amend_log" not in ucols:
        # Production Log Amender: may open/edit/submit an amendment on a finalized run. Anyone who
        # already held a Production or Quality Manager permission keeps the ability they had.
        conn.execute("ALTER TABLE users ADD COLUMN can_amend_log INTEGER NOT NULL DEFAULT 0")
        for u in conn.execute("SELECT id, email FROM users WHERE is_production_manager=1 OR is_quality_manager=1").fetchall():
            conn.execute("UPDATE users SET can_amend_log=1 WHERE id=?", (u["id"],))
            conn.execute("INSERT INTO user_permission_log (user_id,user_email,permission,old_value,new_value,changed_by,changed_at)"
                         " VALUES (?,?,?,?,?,?,?)", (u["id"], u["email"], "Production Log Amender", 0, 1,
                                                    "SYSTEM (migration: existing managers)", now_iso()))
    prcols = {r["name"] for r in conn.execute("PRAGMA table_info(production_runs)")}
    if "status" not in prcols:
        conn.execute("ALTER TABLE production_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")
    if "draft_data" not in prcols:
        conn.execute("ALTER TABLE production_runs ADD COLUMN draft_data TEXT")
    for col, decl in [
        ("operators", "TEXT"),
        ("homog_rinsing_water_l", "REAL"), ("homog_slurry_l", "REAL"),
        ("homog_dilution_water_l", "REAL"), ("homog_citric_kg", "REAL"),
        ("homog_output_l", "REAL"), ("homog_started_at", "TEXT"),
        ("homog_wet_solids_wt_g", "REAL"), ("homog_liquid_wt_g", "REAL"), ("homog_pct_wet_solids", "REAL"),
        ("homog_post_dilution_tank_l", "REAL"), ("homog_lot_pct_solids", "REAL"),
        # Tank 5A/5B -> 6A/6B dilution: plan (Pasteurization In) ...
        ("pasteurization_tank5a_l", "REAL"), ("pasteurization_tank5b_l", "REAL"),
        ("pasteurization_receiving", "TEXT"),        # 6A | 6B | 6AB (connected)
        ("pasteurization_tank6a_start_l", "REAL"), ("pasteurization_tank6b_start_l", "REAL"),
        ("pasteurization_max_transfer_l", "REAL"), ("pasteurization_transfer_rec_l", "REAL"),
        ("pasteurization_water_rec_l", "REAL"),
        # ... and actuals (Dilution & Preservation), with per-tank preservative additions
        ("dilution_product_transferred_l", "REAL"), ("dilution_water_added_l", "REAL"),
        ("dilution_tank6a_final_l", "REAL"), ("dilution_tank6b_final_l", "REAL"),
        ("dilution_ksorbate_added_l_6a", "REAL"), ("dilution_ksorbate_added_l_6b", "REAL"),
        ("dilution_nabenzoate_added_l_6a", "REAL"), ("dilution_nabenzoate_added_l_6b", "REAL"),
        ("dilution_final_variance_pct", "REAL"),
        ("homog_initial_ph", "REAL"), ("homog_target_pct_wet_solids", "REAL"),
        ("homog_dilution_water_target_l", "REAL"), ("homog_final_ph", "REAL"),
        ("homog_qc_ph", "REAL"), ("homog_sample_collected_at", "TEXT"),
        ("homog_tds_pct", "REAL"), ("homog_brix_pct", "REAL"), ("homog_mannitol_pct", "REAL"),
        ("homog_ts_liquid_pct", "REAL"), ("homog_rho_liquid_g_ml", "REAL"),
        ("homog_ts_slurry_pct", "REAL"), ("homog_rho_slurry_g_ml", "REAL"), ("homog_ts_solids_pct", "REAL"),
        ("homog_solids_loading_pct", "REAL"),
        ("homog_sample_slurry_microbial", "INTEGER DEFAULT 0"),
        ("homog_sample_slurry_retention", "INTEGER DEFAULT 0"),
        ("homog_sample_liquid_metals", "INTEGER DEFAULT 0"),
        ("homog_sample_solids_proximate", "INTEGER DEFAULT 0"),
        ("extraction_amplitude_pct", "REAL"), ("extraction_flowrate_lpm", "REAL"),
        ("extraction_pressure_psi", "REAL"), ("extraction_starting_power_w", "REAL"),
        ("extraction_started_at", "TEXT"),
        ("extraction_qc_ph", "REAL"), ("extraction_tds_pct", "REAL"), ("extraction_brix_pct", "REAL"),
        ("extraction_mannitol_pct", "REAL"), ("extraction_ts_liquid_pct", "REAL"),
        ("extraction_rho_liquid_g_ml", "REAL"), ("extraction_ts_slurry_pct", "REAL"),
        ("extraction_rho_slurry_g_ml", "REAL"), ("extraction_ts_solids_pct", "REAL"),
        ("separation_flowrate_lpm", "REAL"), ("separation_mesh_micron", "REAL"),
        ("separation_water_addition_l", "REAL"), ("separation_started_at", "TEXT"),
        ("separation_wet_solids_wt_kg", "REAL"), ("separation_pct_moisture", "REAL"),
        ("separation_pct_moisture_screw", "REAL"),
        ("separation_liquid_qc_ph", "REAL"), ("separation_liquid_tds_pct", "REAL"),
        ("separation_liquid_brix_pct", "REAL"), ("separation_liquid_mannitol_pct", "REAL"),
        ("separation_liquid_ts_liquid_pct", "REAL"), ("separation_liquid_rho_liquid_g_ml", "REAL"),
        ("separation_solids_sample_collected_at", "TEXT"),
        ("pasteurization_product_setpoint_c", "REAL"), ("pasteurization_boiler_setpoint_c", "REAL"),
        ("pasteurization_total_volume_l", "REAL"), ("pasteurization_started_at", "TEXT"),
        ("pasteurization_pre_sample_collected_at", "TEXT"), ("pasteurization_post_sample_collected_at", "TEXT"),
        ("pasteurization_tds_pct", "REAL"),
        ("dilution_fill_level_tank_6ab_l", "REAL"), ("dilution_measured_ph", "REAL"),
        ("dilution_citric_kg", "REAL"), ("dilution_ksorbate_stock_pct", "REAL"),
        ("dilution_citric_item_id", "INTEGER"), ("dilution_ksorbate_item_id", "INTEGER"), ("dilution_nabenzoate_item_id", "INTEGER"),
        ("dilution_ksorbate_added_l", "REAL"),
        ("dilution_nabenzoate_stock_pct", "REAL"), ("dilution_nabenzoate_added_l", "REAL"),
        ("nabenzoate_kg", "REAL DEFAULT 0"),
        ("exclude_from_stats", "INTEGER NOT NULL DEFAULT 0"), ("exclude_reason", "TEXT"),
        ("finalized_at", "TEXT"),
        ("finalized_by", "TEXT"),
        ("release_state", "TEXT"),          # pending_review | pending_release | released | returned | rejected | legacy
        ("release_review_hash", "TEXT"),    # log snapshot hash the approving review signed
        ("packaging_started_at", "TEXT"), ("packaging_packaged_at", "TEXT"),
        ("packaging_qc_ph", "REAL"), ("packaging_tds_pct", "REAL"), ("packaging_brix_pct", "REAL"),
        ("packaging_mannitol_pct", "REAL"), ("packaging_ts_liquid_pct", "REAL"),
        ("packaging_rho_liquid_g_ml", "REAL"), ("packaging_sample_collected_at", "TEXT"),
        ("rejected_feedstock_json", "TEXT"),
    ]:
        if col not in prcols:
            conn.execute("ALTER TABLE production_runs ADD COLUMN %s %s" % (col, decl))
    cucols = {r["name"] for r in conn.execute("PRAGMA table_info(container_units)")}
    if "is_ibc" not in cucols:
        conn.execute("ALTER TABLE container_units ADD COLUMN is_ibc INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE container_units SET is_ibc=1 WHERE code='IBC'")
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(consumables)")}
    for col, decl in [("is_container", "INTEGER NOT NULL DEFAULT 0"), ("litres_each", "REAL"),
                       ("is_sample_container", "INTEGER NOT NULL DEFAULT 0"),
                       ("item_number", "TEXT"), ("label_sku_code", "TEXT"), ("label_package", "TEXT"),
                       ("is_cip_agent", "INTEGER NOT NULL DEFAULT 0")]:
        if col not in ccols:
            conn.execute("ALTER TABLE consumables ADD COLUMN %s %s" % (col, decl))
    ctcols = {r["name"] for r in conn.execute("PRAGMA table_info(consumable_txns)")}
    if "user_name" not in ctcols:
        conn.execute("ALTER TABLE consumable_txns ADD COLUMN user_name TEXT")
    spcols = {r["name"] for r in conn.execute("PRAGMA table_info(run_sample_points)")}
    if "stage" not in spcols:
        conn.execute("ALTER TABLE run_sample_points ADD COLUMN stage TEXT")
    # Every Sample Point row created before this column existed belongs to
    # the (only, at the time) Homogenization Sample Point box.
    conn.execute("UPDATE run_sample_points SET stage='homogenization' WHERE stage IS NULL")
    # Homogenization's dilution calculation no longer uses a tank max level (c1V1=c2V2 on the
    # measured Tank level), so that setting is retired; the default-target setting was renamed.
    conn.execute("DELETE FROM settings WHERE key='homog_tank_2ab_max_level_l'")
    # The old single "Tank 6A/B max level" meant one combined figure; the dilution plan needs a
    # per-tank capacity (connected = double), so it is replaced by a new, clearly-named setting.
    conn.execute("DELETE FROM settings WHERE key='dilution_tank_6ab_max_level_l'")
    conn.execute("UPDATE settings SET label=?, description=? WHERE key='homog_default_target_pct_wet_solids'",
                 ("Homogenization default Target %Solids Loading, (w/w)",
                  "Pre-filled value for a new run's Target %Solids Loading, (w/w) field."))
    dpcols = {r["name"] for r in conn.execute("PRAGMA table_info(run_dilution_passes)")}
    for col in ("measured_ph", "citric_kg"):
        if col not in dpcols:
            conn.execute("ALTER TABLE run_dilution_passes ADD COLUMN %s REAL" % col)
    conn.execute("DELETE FROM settings WHERE key='preproc_variance_flag_pct'")
    # Reagent type: groups inventory items that are the same reagent (e.g. several Citric Acid grades / suppliers)
    # so the production log can offer them all. Backfilled once, from the item names, when the column is added.
    rtcols = {r["name"] for r in conn.execute("PRAGMA table_info(consumables)")}
    if "reagent_type" not in rtcols:
        conn.execute("ALTER TABLE consumables ADD COLUMN reagent_type TEXT")
        for t in ("Citric Acid", "Potassium Sorbate", "Sodium Benzoate"):
            conn.execute("UPDATE consumables SET reagent_type=? WHERE name LIKE ? AND COALESCE(is_container,0)=0 "
                         "AND label_sku_code IS NULL AND COALESCE(is_cip_agent,0)=0", (t, t + "%"))
    rccols = {r["name"] for r in conn.execute("PRAGMA table_info(run_reagent_commits)")}
    if "consumable_id" not in rccols:
        conn.execute("ALTER TABLE run_reagent_commits ADD COLUMN consumable_id INTEGER")
    picols = {r["name"] for r in conn.execute("PRAGMA table_info(preproc_inputs)")}
    if "volume_l" not in picols:
        conn.execute("ALTER TABLE preproc_inputs ADD COLUMN volume_l REAL")
    # the default target blend solids moved from 10 % to 50 %: update the stored setting if nobody had changed it,
    # and draft batches still sitting on the old default
    def retarget_blend_solids():
        conn.execute("UPDATE settings SET value=50 WHERE key='preproc_target_solids_pct' AND value=10")
        conn.execute("UPDATE preproc_batches SET target_solids_pct=50 WHERE status='draft' AND target_solids_pct=10")
    run_once(conn, "preproc_target_solids_50", retarget_blend_solids)          # once: an administrator may deliberately choose 10 later
    pbcols = {r["name"] for r in conn.execute("PRAGMA table_info(preproc_batches)")}
    if "citric_item_id" not in pbcols:
        conn.execute("ALTER TABLE preproc_batches ADD COLUMN citric_item_id INTEGER")
    labcols = {r["name"] for r in conn.execute("PRAGMA table_info(labs)")}
    if "sample_sheet" not in labcols:
        conn.execute("ALTER TABLE labs ADD COLUMN sample_sheet INTEGER NOT NULL DEFAULT 0")
    if "merge_ids" not in labcols:
        conn.execute("ALTER TABLE labs ADD COLUMN merge_ids INTEGER NOT NULL DEFAULT 0")
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(samples)")}
    for col in ("short_id", "id_detailed", "id_simplified", "label_type"):
        if col not in scols:
            conn.execute("ALTER TABLE samples ADD COLUMN %s TEXT" % col)
    spcols = {r["name"] for r in conn.execute("PRAGMA table_info(run_sample_points)")}
    if "label_type" not in spcols:
        conn.execute("ALTER TABLE run_sample_points ADD COLUMN label_type TEXT")
    if "label_numbered" not in spcols:
        conn.execute("ALTER TABLE run_sample_points ADD COLUMN label_numbered INTEGER NOT NULL DEFAULT 0")
    if "label_name" not in spcols:
        conn.execute("ALTER TABLE run_sample_points ADD COLUMN label_name TEXT")
    def refresh_all_sample_ids():
        for r_ in conn.execute("SELECT DISTINCT run_id FROM samples").fetchall():
            refresh_sample_ids(conn, r_["run_id"])
    soft_step(conn, "refresh sample ids", refresh_all_sample_ids)
    if "limit_kg_ha" not in {r["name"] for r in conn.execute("PRAGMA table_info(coa_specs)")}:
        conn.execute("ALTER TABLE coa_specs ADD COLUMN limit_kg_ha REAL")
    lacols = {r["name"] for r in conn.execute("PRAGMA table_info(lab_analyses)")}
    if "method" not in lacols:
        conn.execute("ALTER TABLE lab_analyses ADD COLUMN method TEXT")
    for col, ddl in (("category", "TEXT"), ("kind", "TEXT NOT NULL DEFAULT 'analysis'"), ("symbol", "TEXT"), ("capacity", "INTEGER"), ("req_note", "TEXT")):
        if col not in lacols:
            conn.execute("ALTER TABLE lab_analyses ADD COLUMN %s %s" % (col, ddl))
    reqcols = {r["name"] for r in conn.execute("PRAGMA table_info(lab_requisitions)")}
    for col in ("po_number", "sheet_attachment_id"):
        if col not in reqcols:
            conn.execute("ALTER TABLE lab_requisitions ADD COLUMN %s %s" % (col, "INTEGER" if col.endswith("_id") else "TEXT"))
    rrcols = {r["name"] for r in conn.execute("PRAGMA table_info(run_revisions)")}
    for col, decl in (("category", "TEXT"), ("reason", "TEXT"), ("amendment_id", "INTEGER")):
        if col not in rrcols:
            conn.execute("ALTER TABLE run_revisions ADD COLUMN %s %s" % (col, decl))
    ricols = {r["name"] for r in conn.execute("PRAGMA table_info(run_inputs)")}
    if "decision_set" not in ricols:
        # The accept/reject decision is now a required, explicit choice. The
        # column itself can't be blank (NOT NULL DEFAULT 'accepted'), so this
        # flag records whether an operator actually chose. Every input of an
        # already-finalized run is treated as decided.
        conn.execute("ALTER TABLE run_inputs ADD COLUMN decision_set INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE run_inputs SET decision_set=1 WHERE run_id IN"
                     " (SELECT id FROM production_runs WHERE status='completed')")
    for col, decl in [
        ("loaded_at", "TEXT"), ("surface_photo", "TEXT"), ("striation_photo", "TEXT"),
        ("ph", "REAL"), ("ph_measured_at", "TEXT"), ("orp", "REAL"), ("orp_range", "TEXT"),
        ("odour", "TEXT"), ("odour_other", "TEXT"), ("odour_intensity", "TEXT"),
        ("weight_kg", "REAL"), ("volume_l", "REAL"), ("density_kg_l", "REAL"),
        ("decision", "TEXT NOT NULL DEFAULT 'accepted'"), ("rejection_reason", "TEXT"),
        ("notes", "TEXT"),
    ]:
        if col not in ricols:
            conn.execute("ALTER TABLE run_inputs ADD COLUMN %s %s" % (col, decl))
    skucols = {r["name"] for r in conn.execute("PRAGMA table_info(fg_skus)")}
    for col, decl in [
        ("tds_target", "REAL"), ("ph_target", "REAL"), ("ksorbate_target", "REAL"),
        ("nabenzoate_target", "REAL"), ("active", "INTEGER NOT NULL DEFAULT 1"),
    ]:
        if col not in skucols:
            conn.execute("ALTER TABLE fg_skus ADD COLUMN %s %s" % (col, decl))
    qc_info = list(conn.execute("PRAGMA table_info(qc_logs)"))
    qccols = {r["name"] for r in qc_info}
    if "sample_location" not in qccols:
        conn.execute("ALTER TABLE qc_logs ADD COLUMN sample_location TEXT")
    if "sample_type" not in qccols:
        conn.execute("ALTER TABLE qc_logs ADD COLUMN sample_type TEXT")
    value_col = next((r for r in qc_info if r["name"] == "value"), None)
    if value_col and value_col["type"].upper() != "REAL":
        # SQLite can't ALTER a column's type in place — rebuild the table.
        conn.execute("""
            CREATE TABLE qc_logs_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
                sample_location TEXT,
                sample_type     TEXT,
                metric          TEXT NOT NULL,
                value           REAL NOT NULL,
                unit            TEXT,
                notes           TEXT,
                recorded_by     TEXT,
                recorded_at     TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO qc_logs_new (id,run_id,sample_location,sample_type,metric,value,unit,notes,"
            "recorded_by,recorded_at) SELECT id,run_id,sample_location,sample_type,metric,"
            "CAST(value AS REAL),unit,notes,recorded_by,recorded_at FROM qc_logs")
        conn.execute("DROP TABLE qc_logs")
        conn.execute("ALTER TABLE qc_logs_new RENAME TO qc_logs")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qc_run ON qc_logs(run_id)")
    # Renamed from "Saccharina Liquid Kelp Extract" — fix up rows seeded under
    # the old name so already-running databases pick up the new one too.
    conn.execute("UPDATE fg_skus SET name='Sugar Kelp Extract'"
                 " WHERE code='SACC-LKE' AND name != 'Sugar Kelp Extract'")
    # REF_productSKU.xlsx (2026-09-22): the real product line, replacing the
    # 2 placeholder SKUs. The old ones stay (real historical runs reference
    # them) but are deactivated so they drop out of the picker.
    for code in ("SACC-LKE", "MACRO-LKE"):
        conn.execute("UPDATE fg_skus SET active=0 WHERE code=?", (code,))
    for code, name, tds, ph, ksorb, nabenz, species_codes in [
        ("KELPIVEX", "Kelpivex", 1.8, 3.7, 0.0025, 0, ("SL",)),
        ("REGENAKELP", "RegenaKelp", 1.8, 3.7, 0.0025, 0, ("MT",)),
        ("FIELDKELP", "FieldKelp", 1.8, 3.7, 0.0025, 0, ("SL", "MT")),
        ("KELPIVEX-O", "Kelpivex - O", 1.8, 3.7, 0.0025, 0, ("SL",)),
        ("REGENAKELP-O", "RegenaKelp - O", 1.8, 3.7, 0.0025, 0, ("MT",)),
        ("FIELDKELP-O", "FieldKelp - O", 1.8, 3.7, 0.0025, 0, ("SL", "MT")),
    ]:
        conn.execute(
            "INSERT INTO fg_skus (code,name,tds_target,ph_target,ksorbate_target,"
            "nabenzoate_target,active) VALUES (?,?,?,?,?,?,1)"
            " ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
            " tds_target=excluded.tds_target, ph_target=excluded.ph_target,"
            " ksorbate_target=excluded.ksorbate_target,"
            " nabenzoate_target=excluded.nabenzoate_target, active=1",
            (code, name, tds, ph, ksorb, nabenz))
        for sp in species_codes:
            # a brand-new database has no species yet (seed() adds them after migrate()); init_db() runs this again once they exist
            conn.execute("INSERT OR IGNORE INTO fg_sku_species (sku_code,species_code)"
                         " SELECT ?, code FROM species WHERE code=?", (code, sp))
    conn.execute("INSERT OR IGNORE INTO locations (name) VALUES ('QAQC Hold')")
    # Backfill: totes already sitting at QAQC Hold from before the 'hold'
    # status existed should carry that status now.
    conn.execute("UPDATE tote_lots SET status='hold' WHERE location=? AND status='in_stock'",
                 (QAQC_HOLD_LOCATION,))
    # Drafts created before reserved numbering shipped are still on the old
    # "DRAFT-<token>" placeholder — give them a real number now.
    for r in conn.execute("SELECT id, created_at FROM production_runs"
                          " WHERE status='draft' AND processing_lot LIKE 'DRAFT-%'"):
        conn.execute("UPDATE production_runs SET processing_lot=? WHERE id=?",
                     (lot_number_for(r["created_at"], r["id"]), r["id"]))
    # Backfill qc_field_log for QC Check values that already existed before
    # this table did -- one row per (run, field) with a non-null value today.
    # recorded_by is left NULL (unknown who entered historical data);
    # recorded_at falls back to the run's created_at, the only timestamp
    # production_runs has that applies regardless of draft/completed status.
    # INSERT OR IGNORE + the UNIQUE(run_id, field_id) constraint make this a
    # no-op after the first boot that runs it.
    def backfill_qc_field_log():
        qc_cols = ", ".join(f[0] for f in QC_FIELD_REGISTRY)
        for r in conn.execute("SELECT id, created_at, %s FROM production_runs" % qc_cols):
            for field_id, _stage, _stage_label, _subtitle, _label, _unit in QC_FIELD_REGISTRY:
                val = r[field_id]
                if val is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO qc_field_log (run_id, field_id, value, recorded_by, recorded_at)"
                    " VALUES (?,?,?,?,?)", (r["id"], field_id, val, None, r["created_at"]))
    soft_step(conn, "backfill qc_field_log", backfill_qc_field_log)
    sopcols = {r["name"] for r in conn.execute("PRAGMA table_info(sop_documents)")}
    if "key" not in sopcols:
        conn.execute("ALTER TABLE sop_documents ADD COLUMN key TEXT")
    # Seed the one SOP the Homogenization QC Check links to (by key, not
    # name, so an admin can freely rename it later without breaking that
    # link) -- an admin still has to upload the actual file via Admin > SOP Documents.
    conn.execute("INSERT OR IGNORE INTO sop_documents (name, key, updated_at) VALUES (?, ?, ?)",
                 ("Determining % Wet Solids SOP", "wet_solids_sop", now_iso()))
    conn.execute("UPDATE sop_documents SET key='wet_solids_sop'"
                 " WHERE name='Determining % Wet Solids SOP' AND key IS NULL")
    # Seed every known calculation constant -- INSERT OR IGNORE so an admin's
    # already-edited value is never overwritten, but a newly-added constant
    # (from a later code update) still appears for existing deployments.
    for key, value, label, desc in SETTINGS_DEFAULTS:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key,value,label,description,updated_at)"
            " VALUES (?,?,?,?,?)", (key, value, label, desc, now_iso()))
    # homog_dilution_density_kg_per_l was replaced by homog_tank_2ab_max_level_l
    # when the dilution-target formula changed to solve for a fill level
    # instead of a water-to-add amount -- no formula reads it anymore, so
    # drop the row rather than leave a stale constant in the admin table.
    conn.execute("DELETE FROM settings WHERE key='homog_dilution_density_kg_per_l'")
    # package_size_*_l were replaced by container types living in consumables
    # (admin can now add/rename/remove them like any other consumable, with
    # real on-hand/reorder/cost tracking) when FG-lot creation at finalize
    # switched from the old Bottling/packaging output grid to the Packaging
    # table -- no formula reads them anymore.
    for key in ("package_size_ibc_l", "package_size_4l_l", "package_size_1l_l", "package_size_250ml_l"):
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
    # "Empty New IBC Tote" (or "1000 L IBC", if an earlier boot already
    # renamed it to match a customized Container Units entry) *is* the
    # Packaging table's IBC container option (the pool of empty totes ready
    # to be filled) -- both are renamed to "New 1,000 L IBC Tote" here, and
    # "Empty Used IBC Tote" to "Used 1,000 L IBC Tote", for clearer names in
    # the new Packaging section. Renaming is purely cosmetic (consumable_txns/
    # reports key off the row's id, not its name) and only runs once, so an
    # admin renaming either again later sticks.
    _rename_consumable(conn, "Empty New IBC Tote", "New 1,000 L IBC Tote")
    _rename_consumable(conn, "1000 L IBC", "New 1,000 L IBC Tote")
    _rename_consumable(conn, "Empty Used IBC Tote", "Used 1,000 L IBC Tote")
    conn.execute("UPDATE consumables SET is_container=1, litres_each=1000"
                 " WHERE name='New 1,000 L IBC Tote' AND litres_each IS NULL")
    conn.execute("UPDATE consumables SET is_container=1 WHERE name='Used 1,000 L IBC Tote'")
    # Carry over any OTHER container units an admin already added/edited in
    # the now-retired Container Units table before this round's rework (the
    # is_ibc-flagged one, whatever it's named, was just handled above).
    def carry_over_container_units():
        for r in conn.execute("SELECT code, litres_each FROM container_units WHERE is_ibc=0"):
            if not conn.execute("SELECT 1 FROM consumables WHERE name=?", (r["code"],)).fetchone():
                conn.execute(
                    "INSERT INTO consumables (name,unit,on_hand,reorder_level,is_container,litres_each)"
                    " VALUES (?,'unit',0,0,1,?)", (r["code"], r["litres_each"]))
    run_once(conn, "container_units_carry_over", carry_over_container_units)    # once: re-running it re-created the retired rows at every start
    # Seed the Sample Point boxes' container options -- INSERT OR IGNORE so an
    # admin's already-edited counts are never overwritten. On-hand starts at 0
    # since these are being tracked for the first time; the plant enters its
    # real starting counts via Inventory Items -> Packaging.
    for name in ("50 mL falcon tube", "100 g sample bag", "1 L bottle", "2 L bottle"):
        if not conn.execute("SELECT 1 FROM consumables WHERE name=?", (name,)).fetchone():
            conn.execute(
                "INSERT INTO consumables (name,unit,on_hand,reorder_level,is_container,is_sample_container)"
                " VALUES (?,'ea',0,0,1,1)", (name,))
    # "1 L Bottle"/"2 L Bottle" (Packaging output units, carried over above
    # from the old Container Units table) turned out to be the exact same
    # physical item as "1 L bottle"/"2 L bottle" (Sample Point vessels) --
    # merge each pair into one container valid for both purposes.
    run_once(conn, "merge_bottle_containers", lambda: (_merge_consumable(conn, "1 L Bottle", "1 L bottle"), _merge_consumable(conn, "2 L Bottle", "2 L bottle")))
    # Sodium Benzoate (a reagent, like Citric Acid / Potassium Sorbate) and the
    # 55 gallon drum FG package (55 US gal = 208.2 L; unit must not be 'tote',
    # which the harvest check-in source list filters on). Insert-only so an
    # admin's later edits stick; on-hand starts at 0 for the plant to count in.
    if not conn.execute("SELECT 1 FROM consumables WHERE name='Sodium Benzoate'").fetchone():
        conn.execute("INSERT INTO consumables (name,unit,on_hand,reorder_level)"
                     " VALUES ('Sodium Benzoate','kg',0,0)")
    # The three CIP (Clean In Place) cleaning agents -- reagents in litres,
    # flagged is_cip_agent so they're the ones offered on a CIP log line.
    # Named "CIP ..." so Acid can't be confused with Citric Acid.
    for cip_name in ("CIP Acid", "CIP Caustic", "CIP Sanitizer"):
        if not conn.execute("SELECT 1 FROM consumables WHERE name=?", (cip_name,)).fetchone():
            conn.execute("INSERT INTO consumables (name,unit,on_hand,reorder_level,is_cip_agent)"
                         " VALUES (?,'L',0,0,1)", (cip_name,))
    if not conn.execute("SELECT 1 FROM consumables WHERE name='55 gallon drum'").fetchone():
        conn.execute("INSERT INTO consumables (name,unit,on_hand,reorder_level,is_container,litres_each)"
                     " VALUES ('55 gallon drum','drum',0,0,1,208.2)")
    # A finished-good label item for every product SKU in each of the two bulk
    # package types (1,000 L IBC and 55 gal drum); on-hand starts at 0 to be
    # counted in. Insert-only, so an existing label (and its stock) is never
    # touched and a SKU added later gets its labels on the next boot.
    for pkg in ("New 1,000 L IBC Tote", "55 gallon drum"):
        if not conn.execute("SELECT 1 FROM consumables WHERE is_container=1 AND name=?", (pkg,)).fetchone():
            continue
        for sku in conn.execute("SELECT code, name FROM fg_skus ORDER BY code").fetchall():
            if conn.execute("SELECT 1 FROM consumables WHERE label_sku_code=? AND label_package=?",
                            (sku["code"], pkg)).fetchone():
                continue
            name = "FG Label - %s - %s" % (sku["name"], pkg)
            if conn.execute("SELECT 1 FROM consumables WHERE name=?", (name,)).fetchone():
                continue
            conn.execute("INSERT INTO consumables (name,unit,on_hand,reorder_level,label_sku_code,label_package)"
                         " VALUES (?,'ea',0,0,?,?)", (name, sku["code"], pkg))
    # Product release: every run finalized before the process existed is
    # grandfathered as released (one SYSTEM audit event each, no review hash),
    # so lots already in stock stay sellable. Runs finalized from now on get a
    # release_state at finalize, so this only ever touches the pre-process runs.
    def grandfather_legacy_runs():
        for r in conn.execute("SELECT id, processing_lot FROM production_runs"
                              " WHERE status='completed' AND release_state IS NULL ORDER BY id").fetchall():
            conn.execute("UPDATE production_runs SET release_state='legacy' WHERE id=?", (r["id"],))
            release_log(conn, r["id"], "legacy_release", None, capacity="System",
                        meaning="Finalized before the product release process existed; grandfathered as released "
                                "without a production-log review or Quality sign-off.",
                        detail={"to": "legacy", "lot": r["processing_lot"]})
    soft_step(conn, "grandfather legacy runs", grandfather_legacy_runs)


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
            (email.split("@")[0], email, hash_password(INITIAL_USER_PASSWORD or secrets.token_urlsafe(24)), "user", ts))
        if not INITIAL_USER_PASSWORD:
            print("Created account %s with no usable password: set one under Admin > Users > Reset password." % email)


def seed(conn):
    """First-run seed: admin user + reference data and the stabilized tote lots
    extracted from the 202605 inventory workbook (seed.json)."""
    try:
        with open(SEED_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        # An unreadable seed file used to give an empty database with an admin account that then never reseeded. Stop the start instead:
        # the whole first-start transaction is rolled back (see init_db) and the next start tries again.
        raise RuntimeError("Cannot read the first-run reference data (%s): %s" % (SEED_FILE, e))
    ts = now_iso()
    cur = conn.cursor()
    admin_pw = ADMIN_PASSWORD
    if not admin_pw:
        admin_pw = secrets.token_urlsafe(12)
        print("=" * 72)
        print("FIRST START: administrator account %s  password: %s" % (ADMIN_EMAIL, admin_pw))
        print("This is shown once. Sign in and change it, or set KELP_ERP_ADMIN_PASSWORD.")
        print("=" * 72)
    cur.execute("INSERT INTO users (name,email,password_hash,role,created_at) VALUES (?,?,?,'admin',?)",
                ("Plant Admin", ADMIN_EMAIL.strip().lower(), hash_password(admin_pw), ts))
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


# --------------------------------------------------------------------------- #
# Auth tokens
# --------------------------------------------------------------------------- #
def _b64(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id, token_version=0):
    payload = {"uid": user_id, "exp": int(time.time()) + TOKEN_TTL, "tv": int(token_version or 0)}
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
            "harvestDate": r["checkin_date"], "receivedDate": r["received_date"],
            "toteNumber": r["tote_number"],
            "volumeL": r["volume_l"], "ph": r["ph"], "phUpdated": r["ph_updated"],
            "avgWeightKg": r["avg_weight_kg"],
            "location": r["location"], "description": r["description"],
            "status": r["status"], "runId": r["run_id"], "disposedDate": r["disposed_date"],
            "stabilizationMethod": r["stabilization_method"], "storageUnit": r["storage_unit"],
            "storageSource": r["storage_source"], "orp": r["orp"], "orpUpdated": r["orp_updated"],
            "notes": r["notes"], "grind": r["grind"] or "Coarse", "preprocBatchId": r["preproc_batch_id"],
            "solidsPct": r["solids_pct"]}


def _canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def release_entry_hash(prev_hash, run_id, event_type, user_id, user_name, capacity, meaning, comment,
                       log_hash, detail, created_at):
    payload = "\x1f".join("" if x is None else str(x) for x in (
        prev_hash, run_id, event_type, user_id, user_name, capacity, meaning, comment,
        log_hash, detail, created_at))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def release_log(conn, run_id, event_type, user, capacity=None, meaning=None, comment=None,
                log_hash=None, detail=None):
    """Append one event to the tamper-evident release audit trail (never
    edited or deleted afterwards). `user` is a users row, or None for SYSTEM."""
    last = conn.execute("SELECT entry_hash FROM release_events ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = last["entry_hash"] if last else ""
    ts = now_iso()
    uid = user["id"] if user else None
    uname = user["name"] if user else "SYSTEM"
    uemail = user["email"] if user else None
    detail_s = _canon(detail) if detail else None
    entry = release_entry_hash(prev_hash, run_id, event_type, uid, uname, capacity, meaning, comment,
                               log_hash, detail_s, ts)
    conn.execute(
        "INSERT INTO release_events (run_id,event_type,user_id,user_name,user_email,capacity,meaning,"
        "comment,log_hash,detail,created_at,prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, event_type, uid, uname, uemail, capacity, meaning, comment, log_hash, detail_s, ts,
         prev_hash, entry))


def release_verify_chain(conn):
    """Recompute every hash in order; report the first event whose stored hash
    or link to its predecessor doesn't match."""
    prev = ""
    n = 0
    for r in conn.execute("SELECT * FROM release_events ORDER BY id"):
        n += 1
        expect = release_entry_hash(prev, r["run_id"], r["event_type"], r["user_id"], r["user_name"],
                                    r["capacity"], r["meaning"], r["comment"], r["log_hash"], r["detail"],
                                    r["created_at"])
        if r["prev_hash"] != prev or r["entry_hash"] != expect:
            return {"ok": False, "events": n, "brokenAtEventId": r["id"], "runId": r["run_id"]}
        prev = r["entry_hash"]
    return {"ok": True, "events": n, "brokenAtEventId": None, "runId": None}


RELEASE_LABELS = {
    "pending_review": "Pending review", "pending_release": "Awaiting QA release", "released": "Released",
    "returned": "Returned for correction", "rejected": "Rejected - on hold", "legacy": "Released (pre-process)",
    "amending": "Under amendment",
}


def fg_public(r):
    return {"id": r["id"], "lot": r["fg_lot_number"], "sku": r["sku_code"],
            "runId": r["run_id"], "packageSize": r["package_size"], "qty": r["qty"],
            "litresEach": r["litres_each"], "litres": round((r["qty"] or 0) * (r["litres_each"] or 0), 2),
            "producedDate": r["produced_date"], "tds": r["tds"], "location": r["location"],
            "status": r["status"]}


def run_public(r):
    d = {"id": r["id"], "processingLot": r["processing_lot"], "runDate": r["run_date"],
         "species": r["species_code"], "sku": r["sku_code"], "inputKg": r["input_kg"],
         "targetTds": r["target_tds"], "outputLitres": r["output_litres"],
         "citricKg": r["citric_kg"], "sorbateKg": r["sorbate_kg"],
         "nabenzoateKg": r["nabenzoate_kg"], "ibcUsed": r["ibc_used"],
         "excludeFromStats": bool(r["exclude_from_stats"]), "excludeReason": r["exclude_reason"],
         "finalizedAt": r["finalized_at"],
         "location": r["location"], "notes": r["notes"], "status": r["status"],
         "operators": r["operators"], "createdAt": r["created_at"]}
    try:
        d["rejectedFeedstock"] = json.loads(r["rejected_feedstock_json"]) if r["rejected_feedstock_json"] else []
    except ValueError:
        d["rejectedFeedstock"] = []
    d["stages"] = {
        "homogenization": {
            "startedAt": r["homog_started_at"], "rinsingWaterL": r["homog_rinsing_water_l"],
            "slurryL": r["homog_slurry_l"], "dilutionWaterL": r["homog_dilution_water_l"],
            "citricKg": r["homog_citric_kg"], "outputL": r["homog_output_l"],
            "wetSolidsWtG": r["homog_wet_solids_wt_g"], "liquidWtG": r["homog_liquid_wt_g"],
            "pctWetSolids": r["homog_pct_wet_solids"], "initialPh": r["homog_initial_ph"],
            "postDilutionTankL": r["homog_post_dilution_tank_l"], "lotPctSolids": r["homog_lot_pct_solids"],
            "targetPctWetSolids": r["homog_target_pct_wet_solids"],
            "dilutionWaterTargetL": r["homog_dilution_water_target_l"],
            "finalPh": r["homog_final_ph"],
            "qcPh": r["homog_qc_ph"], "sampleCollectedAt": r["homog_sample_collected_at"],
            "tdsPct": r["homog_tds_pct"], "brixPct": r["homog_brix_pct"],
            "mannitolPct": r["homog_mannitol_pct"], "tsLiquidPct": r["homog_ts_liquid_pct"],
            "rhoLiquidGMl": r["homog_rho_liquid_g_ml"], "tsSlurryPct": r["homog_ts_slurry_pct"],
            "rhoSlurryGMl": r["homog_rho_slurry_g_ml"], "tsSolidsPct": r["homog_ts_solids_pct"],
            "solidsLoadingPct": r["homog_solids_loading_pct"],
            "sampleSlurryMicrobial": bool(r["homog_sample_slurry_microbial"]),
            "sampleSlurryRetention": bool(r["homog_sample_slurry_retention"]),
            "sampleLiquidMetals": bool(r["homog_sample_liquid_metals"]),
            "sampleSolidsProximate": bool(r["homog_sample_solids_proximate"])},
        "extraction": {
            "startedAt": r["extraction_started_at"], "amplitudePct": r["extraction_amplitude_pct"],
            "flowrateLpm": r["extraction_flowrate_lpm"], "pressurePsi": r["extraction_pressure_psi"],
            "startingPowerW": r["extraction_starting_power_w"],
            "qcPh": r["extraction_qc_ph"], "tdsPct": r["extraction_tds_pct"],
            "brixPct": r["extraction_brix_pct"], "mannitolPct": r["extraction_mannitol_pct"],
            "tsLiquidPct": r["extraction_ts_liquid_pct"], "rhoLiquidGMl": r["extraction_rho_liquid_g_ml"],
            "tsSlurryPct": r["extraction_ts_slurry_pct"], "rhoSlurryGMl": r["extraction_rho_slurry_g_ml"],
            "tsSolidsPct": r["extraction_ts_solids_pct"]},
        "separation": {
            "startedAt": r["separation_started_at"], "flowrateLpm": r["separation_flowrate_lpm"],
            "meshMicron": r["separation_mesh_micron"], "waterAdditionL": r["separation_water_addition_l"],
            "wetSolidsWtKg": r["separation_wet_solids_wt_kg"], "pctMoisture": r["separation_pct_moisture"],
            "pctMoistureScrew": r["separation_pct_moisture_screw"],
            "liquidQcPh": r["separation_liquid_qc_ph"], "liquidTdsPct": r["separation_liquid_tds_pct"],
            "liquidBrixPct": r["separation_liquid_brix_pct"],
            "liquidMannitolPct": r["separation_liquid_mannitol_pct"],
            "liquidTsLiquidPct": r["separation_liquid_ts_liquid_pct"],
            "liquidRhoLiquidGMl": r["separation_liquid_rho_liquid_g_ml"],
            "solidsSampleCollectedAt": r["separation_solids_sample_collected_at"]},
        "pasteurization": {
            "startedAt": r["pasteurization_started_at"],
            "productSetpointC": r["pasteurization_product_setpoint_c"],
            "boilerSetpointC": r["pasteurization_boiler_setpoint_c"],
            "totalVolumeL": r["pasteurization_total_volume_l"],
            "preSampleCollectedAt": r["pasteurization_pre_sample_collected_at"],
            "postSampleCollectedAt": r["pasteurization_post_sample_collected_at"],
            "tdsPct": r["pasteurization_tds_pct"],
            "tank5aL": r["pasteurization_tank5a_l"], "tank5bL": r["pasteurization_tank5b_l"],
            "receivingTanks": r["pasteurization_receiving"],
            "tank6aStartL": r["pasteurization_tank6a_start_l"], "tank6bStartL": r["pasteurization_tank6b_start_l"],
            "maxTransferL": r["pasteurization_max_transfer_l"], "recommendedTransferL": r["pasteurization_transfer_rec_l"],
            "recommendedWaterL": r["pasteurization_water_rec_l"]},
        "dilution": {
            "fillLevelTank6abL": r["dilution_fill_level_tank_6ab_l"],
            "measuredPh": r["dilution_measured_ph"],
            "citricKg": r["dilution_citric_kg"],
            "ksorbateStockPct": r["dilution_ksorbate_stock_pct"],
            "ksorbateAddedL": r["dilution_ksorbate_added_l"],
            "nabenzoateStockPct": r["dilution_nabenzoate_stock_pct"],
            "nabenzoateAddedL": r["dilution_nabenzoate_added_l"],
            "productTransferredL": r["dilution_product_transferred_l"], "waterAddedL": r["dilution_water_added_l"],
            "tank6aFinalL": r["dilution_tank6a_final_l"], "tank6bFinalL": r["dilution_tank6b_final_l"],
            "ksorbateAddedL6a": r["dilution_ksorbate_added_l_6a"], "ksorbateAddedL6b": r["dilution_ksorbate_added_l_6b"],
            "nabenzoateAddedL6a": r["dilution_nabenzoate_added_l_6a"], "nabenzoateAddedL6b": r["dilution_nabenzoate_added_l_6b"],
            "finalVariancePct": r["dilution_final_variance_pct"]},
        "packaging": {
            "packagedAt": r["packaging_packaged_at"],
            "qcPh": r["packaging_qc_ph"], "tdsPct": r["packaging_tds_pct"],
            "brixPct": r["packaging_brix_pct"], "mannitolPct": r["packaging_mannitol_pct"],
            "tsLiquidPct": r["packaging_ts_liquid_pct"], "rhoLiquidGMl": r["packaging_rho_liquid_g_ml"],
            "sampleCollectedAt": r["packaging_sample_collected_at"]},
    }
    for key, col in (("citricItemId", "dilution_citric_item_id"), ("ksorbateItemId", "dilution_ksorbate_item_id"),
                     ("nabenzoateItemId", "dilution_nabenzoate_item_id")):
        if r[col] is not None:
            d["stages"]["dilution"][key] = r[col]
    if r["status"] == "draft":
        try:
            dd = json.loads(r["draft_data"]) if r["draft_data"] else {}
        except ValueError:
            dd = {}
        d["toteIds"] = dd.get("toteIds") or []
        d["feedstockDetails"] = dd.get("feedstockDetails") or {}
    return d


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #
# ---------------------------------------------------------------------------------------
# Records archive + nightly backups (batch 9)
#
# The ERP decides WHAT is archived and HOW it is named; a small standard-library script (tools/kelpworks_archive_sync.py) running on an office PC
# copies it into the synced SharePoint library. `archive_manifest` is a pure function of the database (plus the fixed folder a run was given the first
# time it was archived), so the script only has to compare versions. Nothing here deletes anything: an archive only grows.
# ---------------------------------------------------------------------------------------
ARCHIVE_ROOT_NAME = "KelpWorks-Records"
ARCHIVE_KEY = os.environ.get("KELP_ERP_ARCHIVE_KEY", "").strip()      # lets the sync script read the archive and backups WITHOUT an administrator password
ARCHIVE_KEY_MIN = 24                                                  # a shorter key is ignored (and reported on the Admin page)
ARCHIVE_PDF_AUTHOR = "KelpWorks records archive"


def _env_int(name, default, low, high):
    try:
        return max(low, min(high, int(os.environ.get(name, "") or default)))
    except ValueError:
        return default


_NB = os.environ.get("KELP_ERP_NIGHTLY_BACKUP", "").strip()
NIGHTLY_BACKUP = _NB == "1" or (_NB == "" and ENV_NAME == "production")   # on for the live service; off in development and staging unless asked for
BACKUP_HOUR_UTC = _env_int("KELP_ERP_BACKUP_HOUR_UTC", 10, 0, 23)         # 10:00 UTC = 3 am Pacific
BACKUP_FIRST_CHECK_SECONDS = _env_int("KELP_ERP_BACKUP_FIRST_CHECK_SECONDS", 30, 1, 600)   # (tests shorten this)
BACKUP_KEEP = _env_int("KELP_ERP_BACKUP_KEEP", 2, 1, 30)                  # nightly zips kept on the server disk (small: the sync script keeps the long history)
NIGHTLY_RE = re.compile(r"^kelp_erp_(\d{4}-\d{2}-\d{2})\.zip$")
PREMIGRATE_RE = re.compile(r"^pre-migrate-[0-9A-Za-z-]+\.db\.gz$")

ARCHIVE_SKELETON = [
    "01_System-Backups/daily", "01_System-Backups/weekly", "01_System-Backups/monthly", "01_System-Backups/pre-migrate",
    "02_Production-Runs/_Index",
    "03_SOPs/Current", "03_SOPs/Superseded",
    "04_Safety-Data-Sheets/Current", "04_Safety-Data-Sheets/Superseded",
    "05_Fulfillment",
]
ARCHIVE_README_PATH = "README_Naming-Convention.txt"
ARCHIVE_INDEX_PATH = "02_Production-Runs/_Index/runs_index.csv"
ARCHIVE_README = """KelpWorks-Records: folders and file names
==========================================

01_System-Backups   Nightly copies of the KelpWorks database and documents (daily / weekly / monthly) and the snapshot taken before each
                    software update. For restoring the system; administrators only.
02_Production-Runs  One folder per finalized production run, written automatically by KelpWorks. Do not edit or rename (it is not read back).
                    <year>/<year-month>/<processing lot>_<product>_<run date>/
                      01_Report-and-CoA   Production summary (rev1, rev2 ... after each amendment) and the Certificate of Analysis
                                          (PRELIMINARY until the run is released, then RELEASED)
                      02_Lab              Lab requisitions, sample lists and the lab reports
                      03_Photos           Feedstock and process photos
                      04_Other            Any other document attached to the run
                    _superseded folders hold earlier versions of a file that changed. runs_index.csv (in _Index) lists every run.
03_SOPs             Controlled procedures, kept by Quality. Current/ holds what is in force, Superseded/ the older revisions.
04_Safety-Data-Sheets  Controlled safety data sheets, same Current / Superseded layout.
05_Fulfillment      Shipping and sales documents (bill of lading, commercial invoice ...), one folder per shipment.

File names
  Letters, numbers, hyphens and underscores only (no spaces). Fields are separated by an underscore, dates are 2026-10-10 style,
  and the key (lot or document number) comes first so a search for it finds everything.
  SOPs        SOP-<number>_<Title-In-Hyphens>_rev<n>.pdf                  e.g. SOP-012_Homogenization_rev3.pdf
  SDS         SDS_<Chemical-Name>_<Supplier>_<issue date>.pdf             e.g. SDS_Citric-Acid_Univar_2025-03-14.pdf
  Run files   <processing lot>_<document type>_<detail>.<extension>       e.g. PR-20261006-053_CoA_RELEASED.pdf
  Shipments   <shipment number>_<document type>_<detail>.pdf              e.g. SH-20261010-001_BOL.pdf
"""

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tif", "tiff"}


def archive_slug(text, maxlen=40, default="x"):
    """A file-name-safe piece of text: letters, numbers and single hyphens."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")[:maxlen].strip("-")
    return s or default


def archive_ext(filename):
    ext = re.sub(r"[^a-z0-9]", "", os.path.splitext(filename or "")[1].lower())[:5]
    return ext or "bin"


def archive_content_key(S):
    """What a generated PDF says, without the line that records when / by whom it was generated: the version of the document."""
    d = {k: v for k, v in S.items() if k not in ("generatedAt", "generatedBy")}
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def archive_handler():
    """The report builders are Handler methods that only read the database; they never touch a request, so an unconnected instance serves them."""
    return Handler.__new__(Handler)


def archive_run_folder(conn, run):
    """The run's folder inside the records library. Fixed the first time the run is archived: a later amendment of its date or product never moves it."""
    row = conn.execute("SELECT folder FROM archive_runs WHERE run_id=?", (run["id"],)).fetchone()
    if row:
        return row["folder"]
    day = (run["run_date"] or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = (run["finalized_at"] or "")[:10]
    year, month = (day[:4], day[:7]) if re.match(r"^\d{4}-\d{2}-\d{2}$", day) else ("undated", "undated")
    name = "%s_%s_%s" % (run["processing_lot"], archive_slug(run["sku_code"], 24, "SKU"), day or "undated")
    folder = "02_Production-Runs/%s/%s/%s" % (year, month, name)
    conn.execute("INSERT OR IGNORE INTO archive_runs (run_id, folder, created_at) VALUES (?,?,?)", (run["id"], folder, now_iso()))
    return folder


def archive_run_entries(conn, run, handler=None):
    """(entries, problems) for one finalized run. An entry is {path, kind, version, size, runId, src, supersedes}; `version` changes when the file's
    content does, `src` says where its bytes come from (see archive_file_bytes)."""
    handler = handler or archive_handler()
    rid, lot = run["id"], run["processing_lot"]
    folder = archive_run_folder(conn, run)
    entries, problems = [], []

    def add(sub, name, kind, version, size, src, supersedes=()):
        entries.append({"path": "%s/%s/%s" % (folder, sub, name), "kind": kind, "version": version, "size": size, "runId": rid, "src": src,
                        "supersedes": ["%s/%s/%s" % (folder, sub, s) for s in supersedes]})

    rev = conn.execute("SELECT COALESCE(MAX(rev_no),1) n FROM run_revisions WHERE run_id=?", (rid,)).fetchone()["n"]
    pdf_user = {"name": ARCHIVE_PDF_AUTHOR}
    try:
        add("01_Report-and-CoA", "%s_Production-Summary_rev%d.pdf" % (lot, rev), "summary",
            archive_content_key(handler._run_summary_data(conn, rid, pdf_user)), None, "summary")
    except Exception as e:
        logger.warning("Archive: no production summary for %s: %s", lot, e)
        problems.append({"run": lot, "what": "production summary", "error": str(e)})
    try:
        S = handler._coa_data(conn, rid, pdf_user)
        label = "RELEASED" if S.get("released") else "PRELIMINARY"
        add("01_Report-and-CoA", "%s_CoA_%s.pdf" % (lot, label), "coa", archive_content_key(S) + "-" + label, None, "coa",
            supersedes=("%s_CoA_PRELIMINARY.pdf" % lot,) if label == "RELEASED" else ())
    except Exception as e:
        logger.warning("Archive: no certificate of analysis for %s: %s", lot, e)
        problems.append({"run": lot, "what": "certificate of analysis", "error": str(e)})

    taken = {e["path"].lower() for e in entries}

    def unique(sub, name, uid):
        base, ext = os.path.splitext(name)
        cand = name
        n = 1
        while ("%s/%s/%s" % (folder, sub, cand)).lower() in taken:
            cand = "%s_%s%s" % (base, uid if n == 1 else "%s-%d" % (uid, n), ext)
            n += 1
        taken.add(("%s/%s/%s" % (folder, sub, cand)).lower())
        return cand

    requisition = {}
    for r in conn.execute("SELECT * FROM lab_requisitions WHERE run_id=? ORDER BY id", (rid,)).fetchall():
        if r["attachment_id"]:
            requisition[r["attachment_id"]] = ("Requisition", r)
        if r["sheet_attachment_id"]:
            requisition[r["sheet_attachment_id"]] = ("Requisition-Samples", r)
    report = {}
    for r in conn.execute("SELECT attachment_id, lab_name, report_number FROM lab_results WHERE run_id=? AND attachment_id IS NOT NULL ORDER BY id", (rid,)):
        report.setdefault(r["attachment_id"], (r["lab_name"], r["report_number"]))
    photo = {}
    for r in conn.execute("SELECT i.surface_photo, i.striation_photo, t.lot_number FROM run_inputs i JOIN tote_lots t ON t.id=i.tote_lot_id WHERE i.run_id=?", (rid,)):
        for col, label in (("surface_photo", "surface"), ("striation_photo", "striation")):
            v = str(r[col] or "").strip()
            if v.isdigit():
                photo[int(v)] = (r["lot_number"], label)

    for a in conn.execute("SELECT * FROM run_attachments WHERE run_id=? ORDER BY id", (rid,)).fetchall():
        if not os.path.isfile(os.path.join(UPLOAD_DIR, a["stored_name"])):
            problems.append({"run": lot, "what": "document %s" % a["filename"], "error": "the file is missing on the server disk"})
            continue
        ext = archive_ext(a["filename"])
        stem = archive_slug(os.path.splitext(a["filename"] or "")[0], 40, "file")
        if a["id"] in requisition:
            doc, rq = requisition[a["id"]]
            sub, name = "02_Lab", "%s_%s_%s_%s.%s" % (lot, doc, archive_slug(rq["lab_name"], 28, "Lab"), rq["req_number"], ext)
            kind = "requisition" if doc == "Requisition" else "sample-list"
        elif a["id"] in report:
            lab, number = report[a["id"]]
            sub, name = "02_Lab", "%s_Lab-Report_%s_%s.%s" % (lot, archive_slug(lab, 28, "Lab"), archive_slug(number, 30, stem), ext)
            kind = "lab-report"
        elif a["id"] in photo:
            tote, which = photo[a["id"]]
            sub, name, kind = "03_Photos", "%s_Photo_%s_%s.%s" % (lot, archive_slug(tote, 30), which, ext), "photo"
        elif ext in IMAGE_EXTS:
            sub, name, kind = "03_Photos", "%s_Photo_%s.%s" % (lot, stem, ext), "photo"
        else:
            sub, name, kind = "04_Other", "%s_Document_%s.%s" % (lot, stem, ext), "document"
        add(sub, unique(sub, name, a["id"]), kind, "att-%d" % a["id"], a["size"], "att:%d" % a["id"])

    totes = conn.execute("SELECT DISTINCT t.id, t.lot_number FROM run_inputs i JOIN tote_lots t ON t.id=i.tote_lot_id WHERE i.run_id=? ORDER BY t.id", (rid,)).fetchall()
    for t in totes:
        for a in conn.execute("SELECT * FROM tote_attachments WHERE tote_lot_id=? ORDER BY id", (t["id"],)).fetchall():
            if not os.path.isfile(os.path.join(UPLOAD_DIR, a["stored_name"])):
                continue
            ext = archive_ext(a["filename"])
            stem = archive_slug(os.path.splitext(a["filename"] or "")[0], 40, "file")
            if ext in IMAGE_EXTS:
                sub, name, kind = "03_Photos", "%s_Tote-Photo_%s_%s.%s" % (lot, archive_slug(t["lot_number"], 30), stem, ext), "photo"
            else:
                sub, name, kind = "04_Other", "%s_Tote-Document_%s_%s.%s" % (lot, archive_slug(t["lot_number"], 30), stem, ext), "document"
            add(sub, unique(sub, name, "t%d" % a["id"]), kind, "tote-att-%d" % a["id"], a["size"], "tote_att:%d" % a["id"])
    return entries, problems


def archive_index_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["processing_lot", "product", "run_date", "finalized_at", "release_status", "log_revision", "files", "folder"])
    for r in rows:
        w.writerow([r["lot"], r["product"], r["runDate"], r["finalizedAt"], r["release"], r["rev"], r["files"], r["folder"]])
    return buf.getvalue().encode("utf-8-sig")                 # the BOM makes Excel read the accents correctly


def archive_manifest(conn, only_run=None, handler=None):
    """Everything the records library should hold right now. `only_run` limits it to one run's files (used to serve a single file)."""
    return _archive_build(conn, only_run, handler)[0]


def _archive_build(conn, only_run=None, handler=None):
    """(manifest, runs_index.csv bytes)."""
    handler = handler or archive_handler()
    sql = "SELECT * FROM production_runs WHERE status='completed'" + (" AND id=?" if only_run else "") + " ORDER BY id"
    skus = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM fg_skus")}
    files, problems, rows = [], [], []
    for run in conn.execute(sql, (only_run,) if only_run else ()).fetchall():
        entries, probs = archive_run_entries(conn, run, handler)
        files += entries
        problems += probs
        folder = archive_run_folder(conn, run)
        rev = conn.execute("SELECT COALESCE(MAX(rev_no),1) n FROM run_revisions WHERE run_id=?", (run["id"],)).fetchone()["n"]
        rows.append({"lot": run["processing_lot"], "product": skus.get(run["sku_code"], run["sku_code"]), "runDate": run["run_date"],
                     "finalizedAt": run["finalized_at"], "release": RELEASE_LABELS.get(run["release_state"], run["release_state"] or ""),
                     "rev": rev, "files": len(entries), "folder": folder})
    index = archive_index_csv(rows)
    if not only_run:
        readme = ARCHIVE_README.encode("utf-8")
        files.insert(0, {"path": ARCHIVE_README_PATH, "kind": "readme", "version": hashlib.sha256(readme).hexdigest()[:16], "size": len(readme),
                         "runId": None, "src": "readme", "supersedes": []})
        files.insert(1, {"path": ARCHIVE_INDEX_PATH, "kind": "index", "version": hashlib.sha256(index).hexdigest()[:16], "size": len(index),
                         "runId": None, "src": "index", "supersedes": []})
    return ({"format": "kelpworks-records-manifest", "version": 1, "generatedAt": now_iso(), "root": ARCHIVE_ROOT_NAME, "env": ENV_NAME,
             "skeleton": ARCHIVE_SKELETON, "runs": len(rows), "files": files, "problems": problems}, index)


def archive_file_bytes(conn, run_id, path, handler=None):
    """(bytes, version, content type) for one archive path, or None when it is not (or no longer) part of the archive."""
    handler = handler or archive_handler()
    if run_id is None:
        if path == ARCHIVE_README_PATH:
            data = ARCHIVE_README.encode("utf-8")
            return data, hashlib.sha256(data).hexdigest()[:16], "text/plain; charset=utf-8"
        if path == ARCHIVE_INDEX_PATH:
            data = _archive_build(conn, None, handler)[1]
            return data, hashlib.sha256(data).hexdigest()[:16], "text/csv; charset=utf-8"
        return None
    run = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='completed'", (run_id,)).fetchone()
    if not run:
        return None
    ent = next((e for e in archive_run_entries(conn, run, handler)[0] if e["path"] == path), None)
    if not ent:
        return None
    if ent["src"] in ("summary", "coa"):
        fn = handler._run_summary_data if ent["src"] == "summary" else handler._coa_data
        S = fn(conn, run_id, {"name": ARCHIVE_PDF_AUTHOR})
        version = archive_content_key(S)
        if ent["src"] == "coa":
            version += "-" + ("RELEASED" if S.get("released") else "PRELIMINARY")
        S = dict(S, generatedAt=today_iso(), generatedBy=ARCHIVE_PDF_AUTHOR)
        build = build_coa_pdf if ent["src"] == "coa" else build_run_summary_pdf
        return build(S, os.path.join(PUBLIC_DIR, "logo.png")), version, "application/pdf"
    kind, _, ident = ent["src"].partition(":")
    table = "run_attachments" if kind == "att" else "tote_attachments"
    row = conn.execute("SELECT * FROM %s WHERE id=?" % table, (int(ident),)).fetchone()
    if not row:
        return None
    full = os.path.join(UPLOAD_DIR, row["stored_name"])
    if not os.path.isfile(full):
        return None
    with open(full, "rb") as f:
        return f.read(), ent["version"], served_type(row["filename"])[0]


# ---- backups on the server disk ----

def nightly_dir():
    return os.path.join(backups_dir(), "nightly")


def list_backups():
    """The backup files on the server disk, newest first: nightly full backups (.zip) and the snapshots taken before an update (.db.gz)."""
    out = []
    for kind, folder, pattern in (("nightly", nightly_dir(), NIGHTLY_RE), ("pre-migrate", backups_dir(), PREMIGRATE_RE)):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            full = os.path.join(folder, n)
            if pattern.match(n) and os.path.isfile(full):
                st = os.stat(full)
                out.append({"kind": kind, "name": n, "size": st.st_size, "modified": datetime.datetime.utcfromtimestamp(st.st_mtime).replace(microsecond=0).isoformat() + "Z",
                            "path": full})
    return sorted(out, key=lambda b: (b["modified"], b["name"]), reverse=True)


def _folder_bytes(folder):
    total = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def run_nightly_backup(conn, today=None, keep=None, force=False):
    """Write today's full backup (database + documents) to backups/nightly/kelp_erp_<date>.zip and keep only the newest `keep`.
    Returns {"created": name} or {"skipped": reason}. The caller commits (the result is also noted in app_flags)."""
    today = today or datetime.datetime.utcnow().date().isoformat()
    keep = keep or BACKUP_KEEP
    folder = nightly_dir()
    os.makedirs(folder, exist_ok=True)
    for stray in [n for n in os.listdir(folder) if n.endswith(".zip.tmp")]:       # half-written by a stopped / crashed process
        try:
            os.remove(os.path.join(folder, stray))
        except OSError:
            pass
    name = "kelp_erp_%s.zip" % today
    final = os.path.join(folder, name)
    if os.path.exists(final) and not force:
        return {"skipped": "today's backup already exists"}
    need = int((os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0) + sum(_folder_bytes(d) for _n, d in backup_dirs())) + 100 * 1024 * 1024
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = need
    if free < need:
        msg = "not enough free disk space for a backup (%d MB free, about %d MB needed)" % (free // 1048576, need // 1048576)
        logger.error("Nightly backup skipped: %s", msg)
        conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES ('last_backup_error', ?)", ("%s: %s" % (now_iso(), msg),))
        return {"skipped": msg}
    tmp = final + ".tmp"
    try:
        build_full_backup(conn, tmp)
        os.replace(tmp, final)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        logger.error("Nightly backup failed: %s", e, exc_info=True)
        conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES ('last_backup_error', ?)", ("%s: %s" % (now_iso(), e),))
        return {"skipped": "the backup failed (see the server log)"}
    mine = sorted(n for n in os.listdir(folder) if NIGHTLY_RE.match(n))
    for old in mine[:-keep]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES ('last_backup', ?)", ("%s %s %d" % (now_iso(), name, os.path.getsize(final)),))
    conn.execute("DELETE FROM app_flags WHERE key='last_backup_error'")
    logger.info("Nightly backup written: %s (%d MB)", name, os.path.getsize(final) // 1048576)
    return {"created": name}


def backup_due(now=None):
    now = now or datetime.datetime.utcnow()
    return NIGHTLY_BACKUP and now.hour >= BACKUP_HOUR_UTC and not os.path.exists(os.path.join(nightly_dir(), "kelp_erp_%s.zip" % now.date().isoformat()))


def backup_scheduler(stop):
    """Background thread: check every few minutes whether tonight's backup is due (it also catches up after a restart or a deploy)."""
    first = True
    while not stop.wait(BACKUP_FIRST_CHECK_SECONDS if first else 300):
        first = False
        try:
            if backup_due():
                conn = db()
                try:
                    run_nightly_backup(conn)
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            logger.error("The nightly backup check failed.", exc_info=True)


def archive_status(conn):
    flag = lambda k: (conn.execute("SELECT value FROM app_flags WHERE key=?", (k,)).fetchone() or {"value": None})["value"]
    backups = [{k: v for k, v in b.items() if k != "path"} for b in list_backups()]
    return {"nightlyBackup": NIGHTLY_BACKUP, "backupHourUtc": BACKUP_HOUR_UTC, "backupKeep": BACKUP_KEEP,
            "archiveKeyConfigured": len(ARCHIVE_KEY) >= ARCHIVE_KEY_MIN, "archiveKeyTooShort": 0 < len(ARCHIVE_KEY) < ARCHIVE_KEY_MIN,
            "lastBackup": flag("last_backup"), "lastBackupError": flag("last_backup_error"), "backups": backups,
            "runsToArchive": conn.execute("SELECT COUNT(*) c FROM production_runs WHERE status='completed'").fetchone()["c"]}


class ApiError(Exception):
    def __init__(self, status, message, code=None):
        self.status = status
        self.message = message
        self.code = code


# Served inline (the browser shows them): only types that cannot run script. Everything else is a download with a server-chosen type.
SAFE_INLINE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
DOWNLOAD_TYPES = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  ".doc": "application/msword", ".xls": "application/vnd.ms-excel", ".csv": "text/csv", ".txt": "text/plain", ".zip": "application/zip"}
# File types that can carry script or markup for the browser: refused at upload.
BLOCKED_UPLOAD_EXTENSIONS = {".html", ".htm", ".xhtml", ".shtml", ".svg", ".svgz", ".xml", ".xsl", ".xslt", ".js", ".mjs", ".jsx", ".swf", ".hta",
                             ".php", ".jsp", ".asp", ".aspx", ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".vbs", ".ps1", ".sh", ".jar"}
MAX_UPLOAD_TOTAL_BYTES = int(os.environ.get("KELP_ERP_MAX_UPLOADS_MB", "600")) * 1024 * 1024     # all uploaded documents together (the disk is 1 GB)
MIN_FREE_DISK_BYTES = 100 * 1024 * 1024
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
           "connect-src 'self'; frame-src 'self' about: blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def served_type(filename):
    """(Content-Type, may be shown inline) for a stored file, from its extension only."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in SAFE_INLINE_TYPES:
        return SAFE_INLINE_TYPES[ext], True
    return DOWNLOAD_TYPES.get(ext, "application/octet-stream"), False


def content_disposition(filename, inline):
    """A Content-Disposition header value that is safe for any filename: no control characters or quotes, an ASCII fallback, and the real
    name percent-encoded (RFC 5987) so names with accents, dashes or other scripts download correctly."""
    name = "".join(ch for ch in (filename or "") if ch >= " " and ch != "\x7f").strip() or "download"
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120] or "download"
    return "%s; filename=\"%s\"; filename*=UTF-8''%s" % ("inline" if inline else "attachment", ascii_name, quote(name, safe=""))


def check_upload_filename(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in BLOCKED_UPLOAD_EXTENSIONS:
        raise ApiError(400, "Files of type %s cannot be uploaded (documents, spreadsheets, PDFs and images are fine)." % ext)


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".csv": "text/csv; charset=utf-8",
}


# ---------------------------------------------------------------------------------------
# Full backup (database + documents) and the staging-only restore
# ---------------------------------------------------------------------------------------
RESTORE_LOCK = threading.Lock()


def backup_dirs():
    """(name inside the backup, folder on disk) for every folder of files that belongs to the data."""
    return [("uploads", UPLOAD_DIR), ("lab_templates", LAB_DIR), ("sop_documents", SOP_DIR)]


def build_full_backup(conn, out_path):
    """Write a .zip with a consistent snapshot of the database (sqlite3's backup API, safe while writers are active), every uploaded document,
    lab template and SOP, and a manifest. The file holds password hashes and all business records: treat it as sensitive."""
    fd, snap = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(snap)
        try:
            conn.backup(dst)
        finally:
            dst.close()
        counts = {}
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.write(snap, "kelp_erp.db")
            for arc, folder in backup_dirs():
                counts[arc] = 0
                for root, _dirs, files in os.walk(folder):
                    for fn in files:
                        full = os.path.join(root, fn)
                        zf.write(full, arc + "/" + os.path.relpath(full, folder).replace(os.sep, "/"))
                        counts[arc] += 1
            zf.writestr("manifest.json", json.dumps({"format": BACKUP_FORMAT, "version": 1, "createdAt": now_iso(), "env": ENV_NAME,
                                                     "files": counts}, indent=2))
    finally:
        try:
            os.remove(snap)
        except OSError:
            pass


def _safe_member(name):
    """The (folder, relative path) a backup member belongs to, or None when the name is not one we write (absolute paths, `..`, odd folders)."""
    n = name.replace("\\", "/")
    parts = n.split("/")
    if n.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
        return None
    if n in ("kelp_erp.db", "manifest.json"):
        return (n, "")
    for arc, _folder in backup_dirs():
        if parts[0] == arc and len(parts) > 1 and parts[-1]:
            return (arc, "/".join(parts[1:]))
    return None


def scrub_for_staging(conn):
    """Make a restored copy of live data safe to test on: no live password works, and the copy knows it is a restore."""
    conn.execute("UPDATE users SET password_hash=?, must_change_password=0", (hash_password(STAGING_PASSWORD),))
    conn.execute("INSERT OR REPLACE INTO app_flags (key,value) VALUES ('staging_restored_at', ?)", (now_iso(),))


def _copy_exact(src, dst, expected):
    """Copy a zip member, refusing one that inflates past the size the zip declared for it (a lying header is how a zip bomb hides)."""
    left = expected + 1
    while left > 0:
        chunk = src.read(min(1 << 20, left))
        if not chunk:
            return
        dst.write(chunk)
        left -= len(chunk)
    raise ApiError(400, "The backup is damaged (a file is larger than its header says); nothing was restored.")


def restore_backup(zip_path):
    """Replace this (staging) server's data with a full backup .zip, migrate it to this version of the code, and reset every password.
    Everything is validated before anything is changed."""
    if not ALLOW_RESTORE:
        raise ApiError(403, "Restore is disabled on this server (it is only available on a staging server).")
    if len(STAGING_PASSWORD) < MIN_PASSWORD_LEN:
        raise ApiError(400, "Set KELP_ERP_STAGING_PASSWORD (at least %d characters) on this server before restoring." % MIN_PASSWORD_LEN)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise ApiError(400, "That file is not a KelpWorks full backup (.zip).")
    with zf:
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (KeyError, ValueError):
            raise ApiError(400, "That zip has no manifest: use \u201cDownload full backup\u201d on the live site.")
        if manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != 1:
            raise ApiError(400, "That backup has a format this version cannot read.")
        members, total = [], 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            where = _safe_member(info.filename)
            if where is None:
                raise ApiError(400, "The backup contains an unexpected file (%s); nothing was restored." % info.filename[:80])
            total += info.file_size
            members.append((info, where))
        if total > MAX_RESTORE_BYTES * 3 or "kelp_erp.db" not in [w[0] for _i, w in members]:
            raise ApiError(400, "The backup is too large or has no database; nothing was restored.")
        tmpdir = tempfile.mkdtemp(prefix="kelp_restore_")
        try:
            tmp_db = os.path.join(tmpdir, "incoming.db")
            with zf.open("kelp_erp.db") as src, open(tmp_db, "wb") as out:
                _copy_exact(src, out, zf.getinfo("kelp_erp.db").file_size)
            check = sqlite3.connect(tmp_db)
            try:
                ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                has_users = bool(check.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone())
            except sqlite3.DatabaseError:
                ok = has_users = False
            finally:
                check.close()
            if not ok or not has_users:
                raise ApiError(400, "The database inside the backup is damaged or is not a KelpWorks database; nothing was restored.")
            with RESTORE_LOCK:
                src = sqlite3.connect(tmp_db)
                dst = sqlite3.connect(DB_PATH, timeout=60)
                try:
                    src.backup(dst)                                   # replaces the whole content of the live file
                finally:
                    dst.close()
                    src.close()
                counts = {}
                for arc, folder in backup_dirs():
                    os.makedirs(folder, exist_ok=True)
                    for entry in os.listdir(folder):                  # empty the folder but keep it (it may be a mount point)
                        full = os.path.join(folder, entry)
                        shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
                    counts[arc] = 0
                for info, (arc, rel) in members:
                    if arc in ("kelp_erp.db", "manifest.json"):
                        continue
                    folder = dict(backup_dirs())[arc]
                    target = os.path.normpath(os.path.join(folder, *rel.split("/")))
                    if not target.startswith(os.path.normpath(folder) + os.sep):
                        raise ApiError(400, "Unsafe path in the backup: %s" % info.filename[:80])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src_f, open(target, "wb") as dst_f:
                        _copy_exact(src_f, dst_f, info.file_size)
                    counts[arc] += 1
                init_db()                                             # bring the restored data up to this version's schema
                conn = db()
                try:
                    scrub_for_staging(conn)
                    conn.commit()
                    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                finally:
                    conn.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return {"ok": True, "backupCreatedAt": manifest.get("createdAt"), "backupEnv": manifest.get("env"), "users": users, "files": counts}


class Handler(BaseHTTPRequestHandler):
    server_version = "KelpWorksERP/1.0"

    timeout = SOCKET_TIMEOUT                       # a client that goes silent mid-request is dropped (slowloris)

    def handle_one_request(self):
        with INFLIGHT_LOCK:
            INFLIGHT[0] += 1
        try:
            return super().handle_one_request()
        finally:
            with INFLIGHT_LOCK:
                INFLIGHT[0] -= 1

    def log_message(self, fmt, *args):
        pass                                       # the access line is written by log_request (which can leave tokens out)

    def log_request(self, code="-", size="-"):
        path = urlparse(self.path).path
        if path.startswith("/api/") or (str(code).isdigit() and int(code) >= 400):          # not every css / js / image
            logger.info("%s %s -> %s", self.command, redact(self.path), code)

    def _server_error(self, exc):
        """An unexpected failure: the traceback goes to the log under a short reference; the user gets the reference, never the raw error."""
        if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
            logger.warning("Database busy: %s %s", self.command, redact(self.path))
            return self._send_json({"error": "The server is busy right now. Please try again in a moment."}, 503)
        ref = secrets.token_hex(4)
        logger.error("Unhandled error [%s] %s %s", ref, self.command, redact(self.path), exc_info=exc)
        self._send_json({"error": "Something went wrong on the server (reference %s). Please try again; if it keeps happening, tell an administrator this reference." % ref}, 500)

    # ---- helpers ---------------------------------------------------------- #
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self, limit=None):
        limit = limit or MAX_REQUEST_BYTES
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise ApiError(400, "Invalid Content-Length")
        if length < 0:
            raise ApiError(400, "Invalid Content-Length")
        if length > limit:
            self.close_connection = True               # the body is not read; the connection is closed instead
            raise ApiError(413, "That request is too large (the limit is %d MB)." % max(1, limit // (1024 * 1024)) if limit >= 1024 * 1024
                           else "That request is too large.")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError(400, "Invalid JSON body")

    def _user_from_token(self, conn, token):
        """The user a token belongs to, or an ApiError. Rejects an expired / forged token, a token from before the user's last password change
        or deactivation (token_version), and a deactivated user."""
        payload = read_token(token or "")
        if not payload:
            raise ApiError(401, "Invalid or expired token")
        row = conn.execute("SELECT * FROM users WHERE id=?", (payload["uid"],)).fetchone()
        if not row:
            raise ApiError(401, "User not found")
        if int(payload.get("tv", 0) or 0) != int(row["token_version"] or 0):
            raise ApiError(401, "Your session has ended. Please sign in again.")
        if not row["active"]:
            raise ApiError(403, "This account has been deactivated")
        return row

    def _auth(self, conn):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError(401, "Missing token")
        return self._user_from_token(conn, header[7:])

    def _token_user(self, conn):
        """Authenticate a download: the token comes from the Authorization header, or from ?token= (so a file can open in a browser tab)."""
        header = self.headers.get("Authorization", "")
        tok = header[7:] if header.startswith("Bearer ") else (parse_qs(urlparse(self.path).query).get("token", [""])[0])
        if not tok:
            raise ApiError(401, "Missing token")
        user = self._user_from_token(conn, tok)
        if user["must_change_password"]:
            raise ApiError(403, "You must change your password before continuing.", "password_change_required")
        return user

    def _require_admin(self, user):
        if user["role"] != "admin":
            raise ApiError(403, "Administrator access required")

    @staticmethod
    def _is_quality_manager(user):
        return bool(user and user["is_quality_manager"])

    @staticmethod
    def _is_manager(user):
        """Production Manager or Quality Manager (the admin role alone grants neither)."""
        return bool(user and (user["is_quality_manager"] or user["is_production_manager"]))

    def _require_quality_manager(self, user, what):
        if not self._is_quality_manager(user):
            raise ApiError(403, "Only a Quality Manager can %s" % what)

    # ---- dispatch --------------------------------------------------------- #
    def end_headers(self):
        """Headers every response carries: no MIME sniffing, no framing, no referrer to other sites, and HTTPS only once the caller came in over HTTPS."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        if self.headers.get("X-Forwarded-Proto", "") == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if "/attachments/" in path and path.endswith("/download"):
            return self._download_attachment(path)
        if path.startswith("/api/sop-documents/") and path.endswith("/download"):
            return self._download_sop(path)
        if path.startswith("/api/labs/") and path.endswith("/download"):
            return self._download_lab_template(path)
        if path.startswith("/api/production/") and path.endswith("/summary.pdf"):
            return self._download_run_summary(path)
        if path.startswith("/api/production/") and path.endswith("/coa.pdf"):
            return self._download_run_summary(path, "coa")
        if path == "/api/reports/xlsx":
            return self._report_xlsx()
        if path == "/api/yield-usage/xlsx":
            return self._yield_usage_xlsx()
        if path == "/api/admin/backup":
            return self._admin_backup()
        if path == "/api/env":                       # public: the login page shows a STAGING banner from this
            return self._send_json({"env": ENV_NAME, "restoreEnabled": ALLOW_RESTORE})
        if path.startswith("/api/archive/"):
            return self._archive_get(path)
        if path.startswith("/api/"):
            return self._handle_api("GET")
        return self._serve_static(path)

    def _report_xlsx(self):
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
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
            self.send_header("Content-Disposition", content_disposition("kelpworks-report-%s_%s.xlsx" % (data["from"], data["to"]), False))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _yield_usage_xlsx(self):
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            try:
                data = self.route_yield_usage(qs, conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            content = yield_usage_workbook(data)
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition", 'attachment; filename="kelpworks-yield-usage.xlsx"')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _admin_backup(self):
        """Stream a consistent snapshot of the live database as a downloadable
        .db file. Uses sqlite3's own backup API (not a raw file copy) so it's
        safe to run against a database that's being written to concurrently —
        WAL-mode writers don't corrupt or block the snapshot. Admin-only:
        the file includes password hashes and every business record."""
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                user = self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            if user["role"] != "admin":
                return self._send_json({"error": "Administrator access required"}, 403)
            if qs.get("full", ["0"])[0] == "1":
                # database + uploaded documents + lab templates + SOPs, as one .zip (what a staging server restores)
                fd, zip_path = tempfile.mkstemp(suffix=".zip")
                os.close(fd)
                try:
                    build_full_backup(conn, zip_path)
                    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(os.path.getsize(zip_path)))
                    self.send_header("Content-Disposition", 'attachment; filename="kelpworks-full-backup-%s.zip"' % ts)
                    self.end_headers()
                    with open(zip_path, "rb") as f:
                        shutil.copyfileobj(f, self.wfile, 1 << 20)
                finally:
                    try:
                        os.remove(zip_path)
                    except OSError:
                        pass
                return
            fd, tmp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                dst = sqlite3.connect(tmp_path)
                try:
                    conn.backup(dst)
                finally:
                    dst.close()
                with open(tmp_path, "rb") as f:
                    content = f.read()
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition",
                             'attachment; filename="kelpworks-backup-%s.db"' % ts)
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _download_attachment(self, path):
        """Stream an attachment's bytes. Auth via Bearer header or ?token= (so a
        PDF/image can open directly in a browser tab)."""
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            seg = [s for s in path.split("/") if s]   # api production|totes :id attachments :aid download
            kind, rid, aid = seg[1], int(seg[2]), int(seg[4])
            if kind == "totes":
                r = conn.execute("SELECT * FROM tote_attachments WHERE id=? AND tote_lot_id=?",
                                 (aid, rid)).fetchone()
            else:
                r = conn.execute("SELECT * FROM run_attachments WHERE id=? AND run_id=?",
                                 (aid, rid)).fetchone()
            if not r:
                return self._send_json({"error": "Attachment not found"}, 404)
            full = os.path.join(UPLOAD_DIR, r["stored_name"])
            if not os.path.isfile(full):
                return self._send_json({"error": "File missing on disk"}, 404)
            with open(full, "rb") as f:
                data = f.read()
            ctype, inline_ok = served_type(r["filename"])             # from the file name, never from the stored (client-sent) type
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", content_disposition(r["filename"], inline_ok and not qs.get("dl", [""])[0]))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _download_sop(self, path):
        """Stream a controlled document's bytes. Any signed-in user can open
        one (it's just linked by name from the production log); only admins
        can add/replace/remove them (see route_sop_documents)."""
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            seg = [s for s in path.split("/") if s]   # api sop-documents :id download
            sid = int(seg[2])
            r = conn.execute("SELECT * FROM sop_documents WHERE id=?", (sid,)).fetchone()
            if not r or not r["stored_name"]:
                return self._send_json({"error": "Document not found"}, 404)
            full = os.path.join(SOP_DIR, r["stored_name"])
            if not os.path.isfile(full):
                return self._send_json({"error": "File missing on disk"}, 404)
            with open(full, "rb") as f:
                data = f.read()
            sop_name = r["filename"] or r["name"]
            ctype, inline_ok = served_type(sop_name)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", content_disposition(sop_name, inline_ok and not qs.get("dl", [""])[0]))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _download_run_summary(self, path, kind="summary"):
        """/api/production/:id/summary.pdf -- the Production Log Summary, generated from the current log
        (token via Authorization header or ?token= so it opens/downloads from a browser link)."""
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                user = self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            seg = [x for x in path.split("/") if x]
            if len(seg) != 4 or not seg[2].isdigit():
                return self._send_json({"error": "Unknown endpoint"}, 404)
            try:
                S = (self._coa_data if kind == "coa" else self._run_summary_data)(conn, int(seg[2]), user)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            data = (build_coa_pdf if kind == "coa" else build_run_summary_pdf)(S, os.path.join(PUBLIC_DIR, "logo.png"))
            disp = "attachment" if qs.get("dl", [""])[0] else "inline"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", content_disposition("%s_%s.pdf" % (
                S["lot"], "Certificate-of-Analysis" if kind == "coa" else "Production-Log-Summary"), disp == "inline"))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    # ---- records archive and backups (see the archive section above) ----
    def _archive_auth(self, conn):
        """Who may read the archive: an administrator (sign-in token), or the sync script with the archive key (X-Archive-Key header, compared in
        constant time, throttled like a sign-in). Returns who it was, for the log."""
        key = self.headers.get("X-Archive-Key", "")
        if key:
            ip = self._client_ip()
            wait = login_wait_seconds(ip, "archive-key")
            if wait:
                raise ApiError(429, "Too many failed attempts. Try again in %d seconds." % wait)
            if len(ARCHIVE_KEY) >= ARCHIVE_KEY_MIN and hmac.compare_digest(key.encode("utf-8"), ARCHIVE_KEY.encode("utf-8")):
                login_succeeded(ip, "archive-key")
                return "archive key"
            login_failed(ip, "archive-key")
            raise ApiError(403, "The archive key is not accepted")
        user = self._token_user(conn)
        self._require_admin(user)
        return user["name"]

    def _archive_get(self, path):
        conn = db()
        try:
            try:
                self._archive_auth(conn)
                qs = parse_qs(urlparse(self.path).query)
                if path == "/api/archive/manifest":
                    out = archive_manifest(conn)
                    conn.commit()                                     # a run archived for the first time is given its folder
                    return self._send_json(out)
                if path == "/api/archive/status":
                    return self._send_json(archive_status(conn))
                if path == "/api/archive/backups":
                    return self._send_json({"backups": [{k: v for k, v in b.items() if k != "path"} for b in list_backups()]})
                if path == "/api/archive/file":
                    return self._archive_send_file(conn, qs)
                if path == "/api/archive/backup":
                    return self._archive_send_backup(qs)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            return self._send_json({"error": "Unknown archive endpoint"}, 404)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _archive_send_file(self, conn, qs):
        rel = (qs.get("path", [""])[0] or "").replace("\\", "/")
        run = qs.get("run", [""])[0]
        if not rel or rel.startswith("/") or ".." in rel.split("/") or (run and not run.isdigit()):
            raise ApiError(400, "A run number and a path inside the archive are needed")
        res = archive_file_bytes(conn, int(run) if run else None, rel)
        if not res:
            raise ApiError(404, "That file is not part of the archive")
        data, version, ctype = res
        conn.commit()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Archive-Version", version)
        self.send_header("X-Archive-Sha256", hashlib.sha256(data).hexdigest())
        self.send_header("Content-Disposition", content_disposition(rel.split("/")[-1], False))
        self.end_headers()
        self.wfile.write(data)

    def _archive_send_backup(self, qs):
        name = qs.get("name", [""])[0]
        match = next((b for b in list_backups() if b["name"] == name), None)          # only a name from the listing, never a path from the caller
        if not match:
            raise ApiError(404, "No such backup")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip" if name.endswith(".zip") else "application/gzip")
        self.send_header("Content-Length", str(match["size"]))
        self.send_header("Content-Disposition", content_disposition(name, False))
        self.end_headers()
        with open(match["path"], "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def _archive_backup_now(self):
        """POST /api/archive/backup (administrators): the nightly backup, now. It runs outside the request transaction on purpose -- the sqlite backup
        API cannot copy a database whose own connection holds an open write transaction (it would wait forever)."""
        conn = db()
        try:
            try:
                user = self._token_user(conn)
                self._require_admin(user)
                self._body_json(limit=1024)
                res = run_nightly_backup(conn, force=True)           # replaces today's file
                conn.commit()
                return self._send_json(res)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def _download_lab_template(self, path):
        """/api/labs/starter-template/download or /api/labs/:id/template/download (token via header or ?token=)."""
        conn = db()
        try:
            qs = parse_qs(urlparse(self.path).query)
            try:
                self._token_user(conn)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
            seg = [x for x in path.split("/") if x]
            if seg[2] == "starter-template":
                data, fname = build_starter_docx(), "requisition-starter-template.docx"
            else:
                r = conn.execute("SELECT * FROM labs WHERE id=?", (int(seg[2]),)).fetchone()
                if not r or not r["template_stored"]:
                    return self._send_json({"error": "No template uploaded for this lab"}, 404)
                full = os.path.join(LAB_DIR, r["template_stored"])
                if not os.path.isfile(full):
                    return self._send_json({"error": "File missing on disk"}, 404)
                with open(full, "rb") as f:
                    data = f.read()
                fname = (r["template_name"] or "template.docx").replace('"', "").replace("\r", "").replace("\n", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", content_disposition(fname, False))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            conn.close()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/admin/restore":
            return self._admin_restore()
        if path == "/api/archive/backup":
            return self._archive_backup_now()
        return self._handle_api("POST")

    def _admin_restore(self):
        """Staging only: the request body is a full backup .zip (Content-Type application/zip). Admin-only; see restore_backup()."""
        conn = db()
        try:
            try:
                user = self._auth(conn)
                self._require_admin(user)
                if not ALLOW_RESTORE:
                    raise ApiError(403, "Restore is disabled on this server (it is only available on a staging server).")
                length = int(self.headers.get("Content-Length", 0) or 0)
                if not length:
                    raise ApiError(400, "No file was sent.")
                if length > MAX_RESTORE_BYTES:
                    raise ApiError(413, "That backup is larger than this server accepts (%d MB)." % (MAX_RESTORE_BYTES // (1024 * 1024)))
                fd, tmp = tempfile.mkstemp(suffix=".zip")
                try:
                    with os.fdopen(fd, "wb") as out:
                        left = length
                        while left > 0:
                            chunk = self.rfile.read(min(1 << 20, left))
                            if not chunk:
                                break
                            out.write(chunk)
                            left -= len(chunk)
                    if left:
                        raise ApiError(400, "The upload was cut short; nothing was restored.")
                    conn.close()                           # release this request's connection before the file is replaced
                    result = restore_backup(tmp)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                return self._send_json(result)
            except ApiError as e:
                return self._send_json({"error": e.message}, e.status)
        except Exception as e:  # pragma: no cover
            self._server_error(e)
        finally:
            try:
                conn.close()
            except Exception:
                pass

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
        # the app files change with every release and are not versioned: always re-fetch, so a browser never runs an old app.js against a newer server
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        if ext == ".html":
            self.send_header("Content-Security-Policy", APP_CSP)      # the app page runs only its own script (no injected inline script)
        self.end_headers()
        self.wfile.write(data)

    # ---- API router ------------------------------------------------------- #
    def _handle_api(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        conn = db()
        self._created_files, self._files_to_remove = [], []
        try:
            if method != "GET" and parsed.path != "/api/auth/login":
                conn.execute("BEGIN IMMEDIATE")          # take the write lock BEFORE any read the handler bases a write on
            result = self._route(method, parsed.path, query, conn)
            conn.commit()
            self._remove_files(self._files_to_remove)    # files of rows this request deleted (only once the delete is committed)
            self._send_json(result if result is not None else {"ok": True})
        except ApiError as e:
            conn.rollback()
            self._remove_files(self._created_files)      # files this request wrote for rows that were rolled back
            self._send_json({"error": e.message, **({"code": e.code} if e.code else {})}, status=e.status)
        except sqlite3.IntegrityError as e:
            conn.rollback()                              # a rule the database enforces (unique number, row still referenced): the request is refused as a whole
            self._remove_files(self._created_files)
            logger.warning("Refused by a database constraint: %s %s: %s", self.command, redact(self.path), e)
            self._send_json({"error": integrity_message(e), "code": "conflict"}, status=409)
        except (TimeoutError, ConnectionError):
            conn.rollback()                              # the client went silent or hung up mid-request: nothing to answer
            self._remove_files(self._created_files)
            self.close_connection = True
            logger.info("Client gave up: %s %s", self.command, redact(self.path))
        except Exception as e:  # pragma: no cover
            conn.rollback()
            self._remove_files(self._created_files)
            self._server_error(e)
        finally:
            conn.close()

    def _route(self, method, path, query, conn):
        seg = [s for s in path.split("/") if s]

        if method == "POST" and seg == ["api", "auth", "login"]:
            return self.login(conn)

        user = self._auth(conn)  # everything below requires auth
        if user["must_change_password"] and seg not in (["api", "me"], ["api", "me", "password"]):
            raise ApiError(403, "You must change your password before continuing.", "password_change_required")

        if method == "GET" and seg == ["api", "me"]:
            return self._me(user)
        if method == "POST" and seg == ["api", "me", "password"]:
            return self.change_my_password(conn, user)
        if seg[:2] == ["api", "users"]:
            return self.route_users(method, seg, conn, user)
        if method == "GET" and seg == ["api", "refdata"]:
            return self.refdata(conn)
        if seg[:2] == ["api", "settings"]:
            return self.route_settings(method, seg, conn, user)
        if seg[:2] == ["api", "sop-documents"]:
            return self.route_sop_documents(method, seg, conn, user)
        if method == "GET" and seg == ["api", "dashboard"]:
            return self.dashboard(conn)
        if seg[:2] == ["api", "totes"]:
            return self.route_totes(method, seg, query, conn, user)
        if seg[:2] == ["api", "harvest"]:
            return self.route_harvest(method, seg, conn)
        if method == "GET" and seg == ["api", "reagents"]:
            return self.route_reagents(query, conn)
        if seg[:2] == ["api", "preproc"]:
            return self.route_preproc(method, seg, conn, user)
        if seg[:2] == ["api", "samples"]:
            return self.route_samples(method, seg, query, conn, user)
        if seg[:2] == ["api", "cart"]:
            return self.route_cart(method, seg, conn, user)
        if seg[:2] == ["api", "requisitions"]:
            return self.route_requisitions(method, seg, conn, user)
        if seg[:2] == ["api", "labs"]:
            return self.route_labs(method, seg, conn, user)
        if seg[:2] == ["api", "coa-specs"]:
            return self.route_coa_specs(method, seg, conn, user)
        if seg == ["api", "requisition-contact"] and method == "GET":
            return requisition_contact_get(conn)
        if seg == ["api", "requisition-contact"] and method == "PUT":
            self._require_admin(user)
            c = requisition_contact_clean(self._body_json(), requisition_contact_get(conn))
            for k, v in requisition_contact_pairs(c):
                conn.execute("INSERT OR REPLACE INTO requisition_contact (key,value) VALUES (?,?)", (k, v))
            return requisition_contact_get(conn)
        if seg == ["api", "sample-label-names"] and method == "PUT":
            # {names: {stage: name}}: a name different from the built-in default is remembered for everyone; a blank name restores the default
            names = (self._body_json().get("names") or {})
            for stage, name in names.items():
                if stage not in SAMPLE_STAGES:
                    continue
                name = (name or "").strip()[:60]
                builtin = SAMPLE_LABEL_DEFAULT_NAMES.get(stage) or sample_stage_info(stage)[1]
                if not name or name == builtin:
                    conn.execute("DELETE FROM sample_label_names WHERE stage=?", (stage,))
                else:
                    conn.execute("INSERT OR REPLACE INTO sample_label_names (stage,name) VALUES (?,?)", (stage, name))
            return {"ok": True}
        if seg[:2] == ["api", "consumables"]:
            return self.route_consumables(method, seg, conn, user)
        if seg[:2] == ["api", "cip"]:
            return self.route_cip(method, seg, conn, user)
        if seg[:2] == ["api", "production"]:
            # A finalized run's production log is locked: writes need an open
            # amendment (documents and the yield-analysis flag are exempt).
            self._amend_guard(conn, method, seg, user)
            result = self.route_production(method, seg, conn, user)
            if method != "GET":
                # keep the sample catalogue in step with the run's Sample Point rows (finalize, amendments)
                uname = user["name"] if user else None
                if len(seg) >= 3 and seg[2].isdigit():
                    sync_samples(conn, int(seg[2]), uname)
                sync_pending_samples(conn, uname)
            return result
        if seg[:2] == ["api", "integrity"]:
            return self.route_integrity(method, seg, conn, user)
        if seg[:2] == ["api", "release"]:
            return self.route_release(method, seg, query, conn, user)
        if seg[:2] == ["api", "fg"]:
            return self.route_fg(method, seg, query, conn, user)
        if seg[:2] == ["api", "customers"]:
            return self.route_customers(method, seg, conn)
        if seg[:2] == ["api", "shipments"]:
            return self.route_shipments(method, seg, query, conn, user)
        if seg[:2] == ["api", "qc-charts"]:
            return self.route_qc_charts(method, seg, conn, user)
        if method == "GET" and seg == ["api", "yield-usage"]:
            return self.route_yield_usage(query, conn)
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
    def _client_ip(self):
        """The caller's address. Behind Render's proxy the real address is the LAST X-Forwarded-For entry (the proxy appends it); the header is
        only trusted on Render (RENDER is set there) or when KELP_ERP_TRUST_PROXY=1, because anywhere else a caller could forge it."""
        xff = self.headers.get("X-Forwarded-For", "")
        if xff and (os.environ.get("RENDER") or os.environ.get("KELP_ERP_TRUST_PROXY") == "1"):
            return xff.split(",")[-1].strip() or self.client_address[0]
        return self.client_address[0]

    def login(self, conn):
        data = self._body_json(limit=MAX_LOGIN_BODY_BYTES)
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        ip = self._client_ip()
        wait = login_wait_seconds(ip, email)
        if wait:
            raise ApiError(429, "Too many failed sign-in attempts. Try again in %d seconds." % wait)
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            dummy_verify(password)
            login_failed(ip, email)
            raise ApiError(401, "Invalid email or password")
        if not verify_password(password, row["password_hash"]):
            login_failed(ip, email)
            raise ApiError(401, "Invalid email or password")
        if not row["active"]:
            raise ApiError(403, "This account has been deactivated")
        login_succeeded(ip, email)
        return {"token": make_token(row["id"], row["token_version"]), "user": self._me(row)}

    def _me(self, row):
        return {"id": row["id"], "name": row["name"], "email": row["email"],
                "role": row["role"], "mustChange": bool(row["must_change_password"]),
                "isProductionManager": bool(row["is_production_manager"]),
                "isQualityManager": bool(row["is_quality_manager"]),
                "canAmendLog": bool(row["can_amend_log"])}

    # ---- users / admin ---------------------------------------------------- #
    def _user_public(self, r):
        return {"id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"],
                "active": bool(r["active"]), "mustChange": bool(r["must_change_password"]),
                "isProductionManager": bool(r["is_production_manager"]),
                "isQualityManager": bool(r["is_quality_manager"]),
                "canAmendLog": bool(r["can_amend_log"]),
                "createdAt": r["created_at"]}

    def _users(self, conn):
        return [self._user_public(r) for r in conn.execute(
            "SELECT * FROM users ORDER BY active DESC, role DESC, email")]

    def _log_permission(self, conn, uid, email, perm, old, new, actor):
        conn.execute(
            "INSERT INTO user_permission_log (user_id,user_email,permission,old_value,new_value,changed_by,changed_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, email, perm, old, new, actor["name"] if actor else None, now_iso()))

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
        conn.execute("UPDATE users SET password_hash=?, must_change_password=0, token_version=token_version+1 WHERE id=?",
                     (hash_password(newpw), user["id"]))
        fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        return {"ok": True, "token": make_token(fresh["id"], fresh["token_version"])}      # every OTHER session of this user has ended

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
                pm, qm = int(bool(d.get("isProductionManager"))), int(bool(d.get("isQualityManager")))
                am = int(bool(d.get("canAmendLog")))
                cur = conn.execute(
                    "INSERT INTO users (name,email,password_hash,role,must_change_password,active,created_at,"
                    "is_production_manager,is_quality_manager,can_amend_log) VALUES (?,?,?,?,?,1,?,?,?,?)",
                    (name, email, hash_password(pw), role, must_change, now_iso(), pm, qm, am))
                for perm, val in (("Production Manager", pm), ("Quality Manager", qm), ("Production Log Amender", am)):
                    if val:
                        self._log_permission(conn, cur.lastrowid, email, perm, 0, 1, user)
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
                conn.execute("UPDATE users SET password_hash=?, must_change_password=?, token_version=token_version+1 WHERE id=?",
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
                conn.execute("UPDATE users SET name=?, role=?, active=?, token_version=token_version+? WHERE id=?",
                             ((d["name"].strip() if d.get("name") else target["name"]),
                              new_role, new_active, 1 if (target["active"] and not new_active) else 0, uid))
                for key, col, perm in (("isProductionManager", "is_production_manager", "Production Manager"),
                                       ("isQualityManager", "is_quality_manager", "Quality Manager"),
                                       ("canAmendLog", "can_amend_log", "Production Log Amender")):
                    if key in d:
                        new_v = int(bool(d[key]))
                        if new_v != int(target[col] or 0):
                            conn.execute("UPDATE users SET %s=? WHERE id=?" % col, (new_v, uid))
                            self._log_permission(conn, uid, target["email"], perm, int(target[col] or 0), new_v, user)
                return {"users": self._users(conn)}
        raise ApiError(404, "Unknown users endpoint")

    # ---- reference data --------------------------------------------------- #
    def refdata(self, conn):
        species = [dict(code=r["code"], name=r["name"], common=r["common"])
                   for r in conn.execute("SELECT * FROM species ORDER BY code")]
        sites = [dict(code=r["code"], name=r["name"])
                 for r in conn.execute("SELECT * FROM sites ORDER BY code")]
        locations = [r["name"] for r in conn.execute("SELECT name FROM locations ORDER BY name")]
        sku_species = {}
        for r in conn.execute("SELECT sku_code, species_code FROM fg_sku_species"):
            sku_species.setdefault(r["sku_code"], []).append(r["species_code"])
        skus = [dict(code=r["code"], name=r["name"], species=sku_species.get(r["code"], []),
                    active=bool(r["active"]), tdsTarget=r["tds_target"], phTarget=r["ph_target"],
                    ksorbateTarget=r["ksorbate_target"], nabenzoateTarget=r["nabenzoate_target"])
                for r in conn.execute("SELECT * FROM fg_skus ORDER BY (active != 1), code")]
        customers = [self._customer_public(r) for r in conn.execute(
            "SELECT * FROM customers WHERE active=1 ORDER BY name")]
        # Lightweight, app-wide so any production-log QC Check can resolve its
        # SOP link by its stable key (not name -- a name can be renamed by an
        # admin at any time; matching by key means that rename is picked up
        # everywhere automatically) without a separate round trip. The full
        # admin CRUD view fetches /api/sop-documents itself.
        sops = [dict(id=r["id"], name=r["name"], key=r["key"], hasFile=bool(r["stored_name"]))
                for r in conn.execute("SELECT id, name, key, stored_name FROM sop_documents ORDER BY name")]
        # Every container type (Packaging output units and Sample Point
        # vessels alike) is just a consumable with is_container=1 -- exposed
        # here (not just via /api/consumables) so the production log's
        # Packaging/Sample Point dropdowns have it without an extra fetch.
        containers = [dict(id=r["id"], name=r["name"], unit=r["unit"], onHand=r["on_hand"],
                          reorderLevel=r["reorder_level"], costPerUnit=r["cost_per_unit"],
                          location=r["location"], litresEach=r["litres_each"],
                          isSampleContainer=bool(r["is_sample_container"]),
                          low=(r["on_hand"] <= r["reorder_level"]))
                     for r in conn.execute("SELECT * FROM consumables WHERE is_container=1 ORDER BY name")]
        qc_fields = [dict(id=f[0], stage=f[1], stageLabel=f[2], subtitle=f[3], label=f[4], unit=f[5])
                     for f in QC_FIELD_REGISTRY]
        return {"species": species, "sites": sites, "locations": locations,
                "skus": skus, "customers": customers, "sops": sops,
                "settings": get_settings(conn), "containers": containers,
                "qcFields": qc_fields, "requiredFields": required_keys_by_stage()}

    # ---- settings: admin-editable constants used by calculated fields ----- #
    def route_settings(self, method, seg, conn, user):
        if seg == ["api", "settings"] and method == "GET":
            return {"settings": get_settings(conn)}
        if len(seg) == 3 and seg[2] and method == "PUT":
            self._require_admin(user)
            key = seg[2]
            row = conn.execute("SELECT * FROM settings WHERE key=?", (key,)).fetchone()
            if not row:
                raise ApiError(404, "Unknown setting")
            d = self._body_json()
            value = numn(d.get("value"))
            if value is None:
                raise ApiError(400, "Enter a numeric value")
            conn.execute("UPDATE settings SET value=?, updated_at=? WHERE key=?",
                         (value, now_iso(), key))
            return {"settings": get_settings(conn)}
        raise ApiError(404, "Unknown settings endpoint")

    # ---- SOP documents (controlled documents, admin-managed) -------------- #
    def _sop_public(self, r):
        return {"id": r["id"], "name": r["name"], "key": r["key"], "filename": r["filename"],
                "hasFile": bool(r["stored_name"]), "size": r["size"],
                "uploadedBy": r["uploaded_by"], "uploadedAt": r["uploaded_at"],
                "updatedAt": r["updated_at"]}

    def _sop_edits(self, conn, sid):
        return [dict(field=r["field"], oldValue=r["old_value"], newValue=r["new_value"],
                     by=r["user_name"], at=r["created_at"])
                for r in conn.execute(
                    "SELECT * FROM sop_document_edits WHERE sop_id=? ORDER BY id DESC", (sid,))]

    def _log_sop_edit(self, conn, sid, user, field, old, new):
        conn.execute(
            "INSERT INTO sop_document_edits (sop_id,user_name,field,old_value,new_value,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (sid, user["name"] if user else None, field, old, new, now_iso()))

    def route_sop_documents(self, method, seg, conn, user):
        if seg == ["api", "sop-documents"] and method == "GET":
            rows = conn.execute("SELECT * FROM sop_documents ORDER BY name").fetchall()
            return {"sops": [self._sop_public(r) for r in rows]}
        if seg == ["api", "sop-documents"] and method == "POST":
            self._require_admin(user)
            return self._create_sop(conn, user)
        if len(seg) == 3 and seg[2].isdigit():
            sid = int(seg[2])
            row = conn.execute("SELECT * FROM sop_documents WHERE id=?", (sid,)).fetchone()
            if not row:
                raise ApiError(404, "Document not found")
            if method == "PUT":
                self._require_admin(user)
                return self._update_sop(conn, sid, row, user)
            if method == "DELETE":
                self._require_admin(user)
                return self._delete_sop(conn, sid, row)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "edits" and method == "GET":
            sid = int(seg[2])
            if not conn.execute("SELECT 1 FROM sop_documents WHERE id=?", (sid,)).fetchone():
                raise ApiError(404, "Document not found")
            return {"edits": self._sop_edits(conn, sid)}
        raise ApiError(404, "Unknown SOP documents endpoint")

    def _store_sop_file(self, filename, content_type, data_b64):
        """Decode+store one base64 file under SOP_DIR -- the tote/run
        attachment stores' sibling, for admin-managed controlled documents."""
        filename = (filename or "document").strip().replace("\\", "/").split("/")[-1] or "document"
        data_b64 = data_b64 or ""
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
        check_upload_filename(filename)
        stored = self._write_upload(SOP_DIR, raw, os.path.splitext(filename)[1][:12])
        return filename, stored, len(raw)

    def _create_sop(self, conn, user):
        d = self._body_json()
        name = (d.get("name") or "").strip()
        if not name:
            raise ApiError(400, "A document name is required")
        if conn.execute("SELECT 1 FROM sop_documents WHERE name=?", (name,)).fetchone():
            raise ApiError(400, "A document named \"%s\" already exists" % name)
        key = (d.get("key") or "").strip() or None
        if key and conn.execute("SELECT 1 FROM sop_documents WHERE key=?", (key,)).fetchone():
            raise ApiError(400, "Reference key \"%s\" is already in use" % key)
        ts = now_iso()
        filename = stored = size = content_type = None
        if d.get("dataB64"):
            filename, stored, size = self._store_sop_file(d.get("filename"), d.get("contentType"), d["dataB64"])
            content_type = served_type(filename)[0]
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sop_documents (name,key,filename,content_type,size,stored_name,uploaded_by,uploaded_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, key, filename, content_type, size, stored, user["name"] if user else None,
             ts if stored else None, ts))
        return {"sop": self._sop_public(conn.execute(
            "SELECT * FROM sop_documents WHERE id=?", (cur.lastrowid,)).fetchone())}

    def _update_sop(self, conn, sid, row, user):
        """Applies edits and writes one sop_document_edits row per field that
        actually changed (name, and/or the file itself) -- the reference
        `key` is intentionally not editable here, so a link coded against it
        never breaks."""
        d = self._body_json()
        name = row["name"]
        if "name" in d:
            name = (d.get("name") or "").strip()
            if not name:
                raise ApiError(400, "A document name is required")
            dup = conn.execute("SELECT 1 FROM sop_documents WHERE name=? AND id!=?", (name, sid)).fetchone()
            if dup:
                raise ApiError(400, "A document named \"%s\" already exists" % name)
        updates = {"name": name, "updated_at": now_iso()}
        if name != row["name"]:
            self._log_sop_edit(conn, sid, user, "Name", row["name"], name)
        if d.get("dataB64"):
            filename, stored, size = self._store_sop_file(d.get("filename"), d.get("contentType"), d["dataB64"])
            old_stored = row["stored_name"]
            updates.update(filename=filename, content_type=served_type(filename)[0], size=size,
                           stored_name=stored, uploaded_by=user["name"] if user else None,
                           uploaded_at=now_iso())
            self._log_sop_edit(conn, sid, user, "File", row["filename"] or "—", filename)
            if old_stored:
                try:
                    os.remove(os.path.join(SOP_DIR, old_stored))
                except OSError:
                    pass
        sets = ", ".join("%s=?" % c for c in updates)
        conn.execute("UPDATE sop_documents SET %s WHERE id=?" % sets, (*updates.values(), sid))
        return {"sop": self._sop_public(conn.execute(
            "SELECT * FROM sop_documents WHERE id=?", (sid,)).fetchone())}

    def _delete_sop(self, conn, sid, row):
        if row["stored_name"]:
            try:
                os.remove(os.path.join(SOP_DIR, row["stored_name"]))
            except OSError:
                pass
        conn.execute("DELETE FROM sop_documents WHERE id=?", (sid,))
        return {"ok": True}

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
                       reorderLevel=r["reorder_level"], low=(r["on_hand"] <= r["reorder_level"]),
                       category={"LBL": "label", "PKG": "packaging", "SMP": "packaging"}.get(item_category(r), "reagent"))
                  for r in conn.execute("SELECT * FROM consumables ORDER BY name")]
        runs = [run_public(r) for r in conn.execute(
            "SELECT * FROM production_runs WHERE status='completed' ORDER BY run_date DESC, id DESC LIMIT 5")]
        return {
            "stabilized": {"totes": stab["totes"], "kg": round(stab["kg"] or 0, 1),
                           "bySpecies": by_species},
            "finishedGoods": {"litres": round(fg_litres or 0, 1), "lines": fg},
            "consumables": consum,
            "lowStock": [c for c in consum if c["low"]],
            "recentRuns": runs,
        }

    # ---- stabilized totes ------------------------------------------------- #
    def route_totes(self, method, seg, query, conn, user):
        if seg == ["api", "totes"] and method == "GET":
            status = query.get("status", [""])[0]
            # includeRunId: also return this run's own totes regardless of
            # status (e.g. WIP totes already locked to an in-progress run),
            # so its Feedstock picker can still show/unselect them even
            # though a plain status filter would otherwise hide them.
            include_run_id = query.get("includeRunId", [""])[0]
            where, params = [], []
            if status and include_run_id:
                where.append("(status=? OR run_id=?)")
                params += [status, int(include_run_id)]
            elif status:
                where.append("status=?")
                params.append(status)
            elif include_run_id:
                where.append("run_id=?")
                params.append(int(include_run_id))
            sql = ("SELECT * FROM tote_lots"
                   + (" WHERE " + " AND ".join(where) if where else "")
                   + " ORDER BY checkin_date DESC, lot_number")
            rows = conn.execute(sql, params).fetchall()
            last_map = {r["tote_lot_id"]: r["last"] for r in conn.execute(
                "SELECT tote_lot_id, MAX(created_at) AS last FROM tote_stability_log GROUP BY tote_lot_id")}
            totes = []
            sources = self._preproc_sources(conn) if any(r["preproc_batch_id"] for r in rows) else {}
            # processing date of a consumed tote (ends its stabilization period): the run's finalize date (run date
            # for older runs), or the Pre-Processing batch's completion date
            run_dates, pre_dates = {}, {}
            # a fine-grind blend's stabilization clock starts at its Pre-Processing batch date, not the source totes' harvest
            batch_dates = {x["id"]: x["batch_date"] for x in conn.execute("SELECT id, batch_date FROM preproc_batches")}                 if any(r["preproc_batch_id"] for r in rows) else {}
            if any(r["status"] == "consumed" for r in rows):
                run_dates = {x["id"]: (x["finalized_at"] or "")[:10] or x["run_date"] for x in conn.execute(
                    "SELECT id, run_date, finalized_at FROM production_runs")}
                pre_dates = {x["tote_lot_id"]: (x["completed_at"] or "")[:10] or x["batch_date"] for x in conn.execute(
                    "SELECT pi.tote_lot_id, b.completed_at, b.batch_date FROM preproc_inputs pi "
                    "JOIN preproc_batches b ON b.id=pi.batch_id WHERE b.status='completed'")}
            for r in rows:
                t = tote_public(r)
                if r["preproc_batch_id"]:
                    t["batchDate"] = batch_dates.get(r["preproc_batch_id"])
                if r["status"] == "consumed":
                    t["processedDate"] = run_dates.get(r["run_id"]) or pre_dates.get(r["id"])
                t["lastUpdated"] = last_map.get(r["id"]) or r["created_at"]
                if r["preproc_batch_id"] and r["preproc_batch_id"] in sources:
                    t["batchLot"], t["sourceLots"] = sources[r["preproc_batch_id"]]
                totes.append(t)
            return {"totes": totes}
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
                if not it or it["status"] not in ("in_stock", "hold") or it["location"] == to:
                    continue
                self._log_move(conn, "tote", tid, it["lot_number"], it["location"], to, None, date, note)
                new_status = status_for_location(to, it["status"])
                if new_status != it["status"]:
                    self._log_stability(conn, tid, user, "Status", it["status"], new_status)
                conn.execute("UPDATE tote_lots SET location=?, status=? WHERE id=?", (to, new_status, tid))
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
                        new_status = status_for_location(to, it["status"])
                        if new_status != it["status"]:
                            self._log_stability(conn, tid, user, "Status", it["status"], new_status)
                        conn.execute("UPDATE tote_lots SET location=?, status=? WHERE id=?",
                                     (to, new_status, tid))
                    return {"tote": self._tote_with_last_updated(conn, conn.execute(
                        "SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()),
                        "moveLog": self._move_log(conn, "tote", tid)}
                raise ApiError(405, "Method not allowed")

            # /api/totes/:id/ph  — log a new pH and/or ORP reading, or read the
            # Feedstock Stability history (every field edit, not just pH/ORP).
            if len(seg) == 4 and seg[3] == "ph":
                if method == "GET":
                    return {"stabilityLog": self._stability_log(conn, tid),
                            "ph": it["ph"], "phUpdated": it["ph_updated"],
                            "orp": it["orp"], "orpUpdated": it["orp_updated"],
                            "lastUpdated": self._tote_with_last_updated(conn, it)["lastUpdated"],
                            "latestCharacterization": self._latest_characterization(conn, tid, it)}
                if method == "POST":
                    d = self._body_json()
                    ph = numn(d.get("ph")) if d.get("ph") not in (None, "") else None
                    orp = numn(d.get("orp")) if d.get("orp") not in (None, "") else None
                    if ph is None and orp is None:
                        raise ApiError(400, "Enter a pH and/or ORP value")
                    date = (d.get("date") or today_iso()).strip()
                    note = d.get("note")
                    updates = {}
                    if ph is not None:
                        self._log_stability(conn, tid, user, "pH", it["ph"], ph, note)
                        updates["ph"] = ph
                        updates["ph_updated"] = date
                    if orp is not None:
                        self._log_stability(conn, tid, user, "ORP (mV)", it["orp"], orp, note)
                        updates["orp"] = orp
                        updates["orp_updated"] = date
                    sets = ", ".join("%s=?" % c for c in updates)
                    conn.execute("UPDATE tote_lots SET %s WHERE id=?" % sets, (*updates.values(), tid))
                    return {"tote": self._tote_with_last_updated(conn, conn.execute(
                        "SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()),
                        "stabilityLog": self._stability_log(conn, tid)}
                raise ApiError(405, "Method not allowed")

            # /api/totes/:id/photo  — upload a Detail-card photo (no run involved)
            if len(seg) == 4 and seg[3] == "photo" and method == "POST":
                return self.upload_tote_photo(conn, tid, user)

            # /api/totes/:id/trace  -- Pre-Processing traceability (parents of a fine-grind blend,
            # or the blend a coarse tote was shredded into)
            if len(seg) == 4 and seg[3] == "trace" and method == "GET":
                return self._preproc_trace(conn, it)

            # /api/totes/:id/characterize  — Feedstock Inventory's Detail card:
            # the same characterization capture as a production run's
            # Feedstock section, standalone (see characterize_tote).
            if len(seg) == 4 and seg[3] == "characterize" and method == "POST":
                return self.characterize_tote(conn, tid, user)

            if len(seg) == 3 and method == "PUT":
                d = self._body_json()
                new_ph = numn(d["ph"]) if "ph" in d else it["ph"]
                new_orp = numn(d["orp"]) if "orp" in d else it["orp"]
                new_weight = numn(d["avgWeightKg"]) if "avgWeightKg" in d else it["avg_weight_kg"]
                new_location = d["location"] if "location" in d else it["location"]
                new_stab_method = d["stabilizationMethod"] if "stabilizationMethod" in d else it["stabilization_method"]
                new_storage_unit = d["storageUnit"] if "storageUnit" in d else it["storage_unit"]
                new_notes = d["notes"] if "notes" in d else it["notes"]
                # An explicit status wins; otherwise a location change may
                # auto-flip status to/from 'hold' (see status_for_location).
                new_status = d["status"] if "status" in d else status_for_location(new_location, it["status"])
                # A tote's status is moved by the processes that use it (run draft -> wip, finalize -> consumed, disposal). By hand it can only
                # go between in stock and hold, and a tote in a run or already consumed keeps its weight.
                if "status" in d and d["status"] != it["status"] and (it["status"] not in ("in_stock", "hold") or d["status"] not in ("in_stock", "hold")):
                    raise ApiError(400, "A tote that is %s cannot be set to %s by hand" % (it["status"], d["status"]))
                if it["status"] in ("wip", "consumed", "disposed") and new_weight != it["avg_weight_kg"]:
                    raise ApiError(400, "The weight of a tote that is in a run or already consumed cannot be changed")
                # Feedstock Stability log: one line item per field that actually
                # changed, who changed it and when.
                if "ph" in d and new_ph != it["ph"]:
                    self._log_stability(conn, tid, user, "pH", it["ph"], new_ph)
                if "orp" in d and new_orp != it["orp"]:
                    self._log_stability(conn, tid, user, "ORP (mV)", it["orp"], new_orp)
                if "avgWeightKg" in d and new_weight != it["avg_weight_kg"]:
                    self._log_stability(conn, tid, user, "Weight (kg)", it["avg_weight_kg"], new_weight)
                if new_location != it["location"]:
                    self._log_stability(conn, tid, user, "Location", it["location"], new_location)
                if new_status != it["status"]:
                    self._log_stability(conn, tid, user, "Status", it["status"], new_status)
                ph_updated = today_iso() if ("ph" in d and new_ph != it["ph"]) else it["ph_updated"]
                orp_updated = today_iso() if ("orp" in d and new_orp != it["orp"]) else it["orp_updated"]
                conn.execute(
                    "UPDATE tote_lots SET ph=?, ph_updated=?, orp=?, orp_updated=?, avg_weight_kg=?,"
                    " location=?, status=?, stabilization_method=?, storage_unit=?, notes=? WHERE id=?",
                    (new_ph, ph_updated, new_orp, orp_updated, new_weight, new_location, new_status,
                     new_stab_method, new_storage_unit, new_notes, tid))
                return {"tote": self._tote_with_last_updated(conn, conn.execute(
                    "SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone())}
            if len(seg) == 3 and method == "DELETE":
                if it["status"] == "consumed":
                    raise ApiError(400, "Cannot delete a tote already consumed by a run")
                if it["status"] == "wip":
                    raise ApiError(400, "Cannot delete a tote that's locked into an in-progress run")
                if conn.execute("SELECT 1 FROM run_inputs WHERE tote_lot_id=? UNION ALL SELECT 1 FROM preproc_inputs WHERE tote_lot_id=?",
                                (tid, tid)).fetchone():
                    raise ApiError(409, "This tote was inspected for a production run or pre-processing batch, so it is part of that record and "
                                        "cannot be deleted. Place it on QAQC Hold or dispose of it instead.", "tote_has_history")
                self._files_to_remove += [os.path.join(UPLOAD_DIR, a["stored_name"]) for a in conn.execute(
                    "SELECT stored_name FROM tote_attachments WHERE tote_lot_id=?", (tid,))]
                conn.execute("DELETE FROM tote_lots WHERE id=?", (tid,))
                return {"ok": True}
        raise ApiError(404, "Unknown totes endpoint")

    def _tote_with_last_updated(self, conn, row):
        t = tote_public(row)
        r = conn.execute("SELECT MAX(created_at) AS last FROM tote_stability_log WHERE tote_lot_id=?",
                         (row["id"],)).fetchone()
        t["lastUpdated"] = (r["last"] if r else None) or row["created_at"]
        return t

    def _stability_log(self, conn, tote_id):
        return [dict(field=r["field"], oldValue=r["old_value"], newValue=r["new_value"],
                     note=r["note"], by=r["user_name"], at=r["created_at"],
                     runId=r["run_id"], attachmentId=r["attachment_id"])
                for r in conn.execute(
                    "SELECT * FROM tote_stability_log WHERE tote_lot_id=? ORDER BY id DESC",
                    (tote_id,))]

    def _log_stability(self, conn, tote_id, user, field, old, new, note=None, run_id=None, attachment_id=None):
        conn.execute(
            "INSERT INTO tote_stability_log (tote_lot_id,user_name,field,old_value,new_value,note,"
            "created_at,run_id,attachment_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (tote_id, user["name"] if user else None, field, _fmtval(old), _fmtval(new), note, now_iso(),
             run_id, attachment_id))

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

    # ---- Production Log Summary (PDF) data ------------------------------------------------- #
    def _run_summary_data(self, conn, rid, user):
        r = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not r:
            raise ApiError(404, "Production run not found")
        if r["status"] != "completed":
            raise ApiError(400, "Finalize the run first -- the summary reports a completed production log")
        run = self._run_full(conn, r)
        rel = self._release_summary(conn, r)
        sku = conn.execute("SELECT * FROM fg_skus WHERE code=?", (r["sku_code"],)).fetchone()
        site_names = {x["code"]: x["name"] for x in conn.execute("SELECT code,name FROM sites")}
        sp_names = {x["code"]: (x["common"] or x["name"]) for x in conn.execute("SELECT * FROM species")}
        ymd = lambda iso: (iso or "")[:10]
        inputs = run["inputs"]
        accepted = [i for i in inputs if i.get("decision") != "rejected"]
        weights = [i["weightKg"] for i in accepted if i.get("weightKg") is not None]
        measured = sum(weights) if weights else (r["input_kg"] or None)
        phs = [i["ph"] for i in accepted if i.get("ph") is not None]
        orps = [i["orp"] for i in accepted if i.get("orp") is not None]
        sites = sorted({site_names.get(i["site"], i["site"]) for i in accepted if i.get("site")})
        species = sorted({sp_names.get(i["species"], i["species"]) for i in accepted if i.get("species")})
        stages = {k: dict(v) for k, v in run["stages"].items()}
        for k in ("homogenization", "extraction", "separation", "pasteurization", "dilution", "packaging"):
            stages.setdefault(k, {})
        water_parts = [stages["homogenization"].get("rinsingWaterL"), stages["homogenization"].get("dilutionWaterL"),
                       stages["dilution"].get("waterAddedL")] + [p_.get("waterAddedL") for p_ in run["dilutionPasses"]]
        water_vals = [w_ for w_ in water_parts if w_ not in (None, "")]
        pk_entries = run["packagingEntries"]
        units = sum((e.get("qty") or 0) for e in pk_entries)
        # reagents drawn by this run (the item actually used, from the commit records)
        parts = []
        for rc in conn.execute("SELECT rc.reagent, rc.committed_kg, c.name FROM run_reagent_commits rc "
                               "LEFT JOIN consumables c ON c.id=rc.consumable_id WHERE rc.run_id=?", (rid,)):
            if rc["committed_kg"]:
                label = {"Citric Acid": "citric acid", "Potassium Sorbate": "K-sorbate", "Sodium Benzoate": "Na benzoate"}.get(rc["reagent"], rc["reagent"])
                item = (" (%s)" % rc["name"]) if rc["name"] and rc["name"] != rc["reagent"] else ""
                parts.append("%s %s kg%s" % (label, ("%.1f" % rc["committed_kg"]).rstrip("0").rstrip("."), item))
        # samples (catalogue) and lab requisitions
        samples = [{"code": x["sample_code"], "stageLabel": sample_stage_info(x["stage"])[1], "description": x["description"] or "Microbial",
                    "container": x["container"], "status": x["status"], "collectedAt": x["collected_at"] or x["created_at"]}
                   for x in conn.execute("SELECT * FROM samples WHERE run_id=? ORDER BY sample_code", (rid,))]
        reqs = []
        for q in conn.execute("SELECT * FROM lab_requisitions WHERE run_id=? ORDER BY id", (rid,)):
            names = []
            n = 0
            for rs in conn.execute("SELECT analyses FROM requisition_samples WHERE requisition_id=?", (q["id"],)):
                n += 1
                for a in (json.loads(rs["analyses"]) if rs["analyses"] else []):
                    if a not in names:
                        names.append(a)
            reqs.append({"reqNumber": q["req_number"], "labName": q["lab_name"], "poNumber": q["po_number"], "nSamples": n,
                         "analyses": ", ".join(names), "date": ymd(q["created_at"])})
        # materials from the inventory ledger (ref = processing lot)
        order = {"reagent": 0, "packaging": 1, "sample": 2, "label": 3}
        names_c = {"reagent": "Reagents", "packaging": "Packaging", "sample": "Sample containers", "label": "FG labels"}
        agg = {}
        for t in conn.execute("SELECT t.delta, t.reason, c.name, c.unit, c.is_container, c.label_sku_code FROM consumable_txns t "
                              "JOIN consumables c ON c.id=t.consumable_id WHERE t.ref=?", (r["processing_lot"],)):
            cat = self._yu_category(t["reason"], t["is_container"], t["label_sku_code"])
            if cat:
                k = (cat, t["name"], t["unit"])
                agg[k] = agg.get(k, 0) - (t["delta"] or 0)
        materials = [(names_c[k[0]], k[1], round(v, 2), k[2]) for k, v in sorted(agg.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])) if abs(v) > 1e-9]
        prog = run["progress"]
        tot = sum(sec["total"] for sec in prog["sections"])
        fil = sum(sec["filled"] for sec in prog["sections"])
        revs = run["revisions"]
        amend = sum(1 for x in revs if x.get("kind") == "amendment")
        fg_status = {"on_hand": "On hand", "pending_release": "Pending release", "hold": "Hold", "sold": "Sold", "disposed": "Disposed"}
        ev = lambda who, at: ("%s  ·  %s" % (who, ymd(at))) if who else "Pending"
        return {
            "lot": r["processing_lot"], "runDate": r["run_date"], "product": sku["name"] if sku else r["sku_code"],
            "location": r["location"], "operators": r["operators"],
            "feedstockSummary": " · ".join(x for x in (", ".join(species), ", ".join(sites)) if x) or "-",
            "releaseState": r["release_state"], "releaseLabel": RELEASE_LABELS.get(r["release_state"], "-"),
            "finalizedText": ("%s  ·  %s" % (r["finalized_by"] or "-", ymd(r["finalized_at"]))) if r["finalized_at"] else "-",
            "reviewText": ev(rel.get("reviewedBy"), rel.get("reviewedAt")) if r["release_state"] != "legacy" else "Before review workflow",
            "releaseText": ev(rel.get("releasedBy"), rel.get("releasedAt")) if r["release_state"] != "legacy" else "Before release workflow",
            "revisionText": "Rev %s%s%s" % (run.get("revision") or 1, (" (%d amendment%s)" % (amend, "" if amend == 1 else "s")) if amend else "",
                                           "  -  OPEN AMENDMENT" if rel.get("amendment") else ""),
            "completenessText": ("All %d required fields complete" % tot) if fil >= tot and tot else ("%d of %d (%d%%)" % (fil, tot, round(100.0 * fil / tot) if tot else 0)),
            "nDocuments": len(run["attachments"]),
            "stages": stages, "targetTds": r["target_tds"] if r["target_tds"] is not None else (sku["tds_target"] if sku else None),
            "targetPh": sku["ph_target"] if sku else None,
            "outputL": r["output_litres"], "measuredKg": measured, "toteCount": len(accepted), "rejectedCount": len(inputs) - len(accepted),
            "totalWaterL": round(sum(water_vals), 1) if water_vals else None,
            "unitsPackaged": units, "firstLoaded": min([i["loadedAt"] for i in accepted if i.get("loadedAt")] or [""]) or None,
            "feedstockLine": ("%d tote%s  ·  %s kg measured" % (len(accepted), "" if len(accepted) == 1 else "s", ("%.0f" % measured) if measured else "-")),
            "phRange": ("%.1f - %.1f (avg %.1f)" % (min(phs), max(phs), sum(phs) / len(phs))) if phs else "",
            "orpLine": ("ORP %d to %d mV" % (min(orps), max(orps))) if orps else "",
            "reagentLine": "  ·  ".join(parts), "extraPasses": len(run["dilutionPasses"]),
            "packagingLine": "  ·  ".join("%s x %s" % (("%g" % e["qty"]), e["containerUnit"]) for e in pk_entries if e.get("qty")),
            "samples": samples, "requisitions": reqs, "materials": materials,
            "fgLots": [dict(f, statusLabel=fg_status.get(f["status"], f["status"])) for f in run["fgLots"]],
            "qcRecorded": (run["qcSummary"]["recorded"], run["qcSummary"]["total"]) if run.get("qcSummary") else None,
            "generatedAt": today_iso(), "generatedBy": user["name"] if user else None,
        }

    # ---- Certificate of Analysis ---------------------------------------------------------- #
    def _coa_data(self, conn, rid, user):
        r = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not r:
            raise ApiError(404, "Production run not found")
        if r["status"] != "completed":
            raise ApiError(400, "Finalize the run first -- the certificate reports a completed production run")
        ev = coa_evaluate(conn, r)
        rel = self._release_summary(conn, r)
        sku = conn.execute("SELECT * FROM fg_skus WHERE code=?", (r["sku_code"],)).fetchone()
        released = r["release_state"] in ("released", "legacy")
        return {"lot": r["processing_lot"], "product": sku["name"] if sku else r["sku_code"], "runDate": r["run_date"],
                "fgLots": ", ".join(x["lot"] for x in rel["lots"]) or "-", "releaseLabel": RELEASE_LABELS.get(r["release_state"], "-"),
                "released": released,
                "releasedBy": rel.get("releasedBy") or ("Before release workflow" if r["release_state"] == "legacy" else ""),
                "releasedAt": (rel.get("releasedAt") or "")[:10], "releasedComment": rel.get("releasedComment") or "",
                # only the tests flagged "listed on the certificate" are printed (the Lab results window shows them all)
                "rows": [x for x in ev["rows"] if x["listed"]], "additional": ev["additional"],
                "failed": [x["name"] for x in ev["rows"] if x["listed"] and x["status"] == "fail"],
                "applicationRate": ev["summary"]["applicationRate"], "applicationPeriods": ev["summary"]["applicationPeriods"],
                "generatedAt": today_iso(), "generatedBy": user["name"] if user else None}

    def _lab_results_payload(self, conn, r):
        specs = [{"code": s["code"], "name": s["name"], "group": s["grp"], "unit": s["unit"], "basis": s["basis"],
                  "method": s["method"], "required": bool(s["required"]), "specText": coa_spec_text(s)}
                 for s in conn.execute("SELECT * FROM coa_specs WHERE basis!='run' ORDER BY sort, code")]
        return {"results": [lab_result_public(x) for x in conn.execute("SELECT * FROM lab_results WHERE run_id=? ORDER BY id DESC", (r["id"],))],
                "coa": coa_evaluate(conn, r), "specs": specs, "metalUnits": ["ppm", "%", "ppb", "mg/kg"]}

    def _lab_regate(self, conn, run, user):
        """A lab result added or voided AFTER release can make released product non-conforming (a required result removed, or a new failure
        that the release did not accept). The run's unsold lots are then put on hold and the audit trail says why."""
        if run["release_state"] not in ("released", "legacy"):
            return
        lab = coa_evaluate(conn, run)["summary"]
        ev = conn.execute("SELECT detail FROM release_events WHERE run_id=? AND event_type IN ('released','legacy_release') ORDER BY id DESC LIMIT 1",
                          (run["id"],)).fetchone()
        accepted = set()
        if ev and ev["detail"]:
            try:
                accepted = set(json.loads(ev["detail"]).get("outOfSpec", []))
            except (ValueError, AttributeError):
                accepted = set()
        problems = list(lab["missingRequired"]) + [f for f in lab["failed"] if f not in accepted]
        if not problems:
            return
        moved = self._set_lot_status(conn, run["id"], ("on_hand",), "hold")
        release_log(conn, run["id"], "lab_result_hold", user, capacity="Quality Manager",
                    meaning="A lab result change after release left this run non-conforming (%s); its unsold lots were put on hold." % ", ".join(problems),
                    detail={"problems": problems, "lots": moved})

    def route_lab_results(self, method, seg, conn, user):
        rid = int(seg[2])
        r = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not r:
            raise ApiError(404, "Production run not found")
        if len(seg) == 4 and method == "GET":
            return self._lab_results_payload(conn, r)
        if method == "POST" and len(seg) >= 5:
            self._require_quality_manager(user, "enter or void lab results")
        if len(seg) == 4 and method == "POST":
            self._require_quality_manager(user, "enter or void lab results")
        if len(seg) == 5 and seg[4] == "scan" and method == "POST":
            # read an uploaded lab-report PDF and propose the form's fields (nothing is saved; the user reviews them)
            d = self._body_json()
            a = conn.execute("SELECT * FROM run_attachments WHERE id=? AND run_id=?", (d.get("attachmentId"), rid)).fetchone()
            if not a:
                raise ApiError(404, "Document not found on this run")
            if not ((a["filename"] or "").lower().endswith(".pdf") or "pdf" in (a["content_type"] or "").lower()):
                raise ApiError(400, "Only a PDF lab report can be scanned")
            full = os.path.join(UPLOAD_DIR, a["stored_name"])
            if not os.path.isfile(full):
                raise ApiError(404, "File missing on disk")
            with open(full, "rb") as f:
                data = f.read()
            try:
                lines = pdf_text_lines(data)
            except Exception:
                lines = []
            if not lines:
                raise ApiError(400, "No text could be read from this PDF (it may be a scanned image). Enter the values by hand.")
            return parse_lab_report(lines)
        if len(seg) == 4 and method == "POST":
            d = self._body_json()
            lab_name = (d.get("labName") or "").strip()
            lab_id = d.get("labId")
            if lab_id:
                lb = conn.execute("SELECT name FROM labs WHERE id=?", (lab_id,)).fetchone()
                lab_name = lb["name"] if lb else lab_name
            report = (d.get("reportNumber") or "").strip()
            rdate = (d.get("reportDate") or "").strip()
            if not lab_name:
                raise ApiError(400, "Enter the laboratory")
            if not report:
                raise ApiError(400, "Enter the lab report number")
            if rdate and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rdate):
                raise ApiError(400, "Report date must be a date")
            att = d.get("attachmentId")
            if att and not conn.execute("SELECT 1 FROM run_attachments WHERE id=? AND run_id=?", (att, rid)).fetchone():
                raise ApiError(400, "That document is not attached to this run")
            rows = d.get("results") or []
            if not rows:
                raise ApiError(400, "Add at least one result")
            now = now_iso()
            added = []
            for i, x in enumerate(rows, 1):
                code = (x.get("specCode") or "").strip() or None
                spec = None
                if code:
                    spec = conn.execute("SELECT * FROM coa_specs WHERE code=? AND basis!='run'", (code,)).fetchone()
                    if not spec:
                        raise ApiError(400, "Row %d: unknown test" % i)
                analyte = spec["name"] if spec else (x.get("analyte") or "").strip()
                if not analyte:
                    raise ApiError(400, "Row %d: enter the test name" % i)
                try:
                    qual, num, vtext = parse_result_value(x.get("value"))
                except ApiError as e:
                    raise ApiError(400, "%s: %s" % (analyte, e.message))
                unit = (x.get("unit") or "").strip()
                if spec and spec["basis"] in ("value", "absent"):
                    unit = spec["unit"]
                    if spec["basis"] == "absent" and num is not None:
                        raise ApiError(400, "%s: enter Negative or Positive" % analyte)
                elif spec and spec["basis"] == "metal":
                    unit = unit or "ppm"
                    if unit.lower() not in COA_PPM_FACTORS:
                        raise ApiError(400, "%s: unit must be ppm, %%, ppb or mg/kg" % analyte)
                    if num is None:
                        raise ApiError(400, "%s: enter a number" % analyte)
                meth = (x.get("method") or "").strip() or (spec["method"] if spec else "") or None
                conn.execute("INSERT INTO lab_results (run_id,spec_code,analyte,lab_id,lab_name,report_number,report_date,sample_ref,method,"
                             "qualifier,value_num,value_text,unit,attachment_id,entered_by,entered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (rid, code, analyte, lab_id or None, lab_name, report, rdate or None, (d.get("sampleRef") or "").strip() or None, meth,
                              qual, num, vtext, unit or None, att or None, user["name"], now))
                added.append(analyte)
            release_log(conn, rid, "lab_results_added", user, capacity=None,
                        meaning="Laboratory results entered from report %s (%s)." % (report, lab_name),
                        detail={"report": report, "lab": lab_name, "tests": added})
            self._lab_regate(conn, r, user)
            return self._lab_results_payload(conn, r)
        if len(seg) == 6 and seg[4].isdigit() and seg[5] == "void" and method == "POST":
            d = self._body_json()
            reason = (d.get("reason") or "").strip()
            x = conn.execute("SELECT * FROM lab_results WHERE id=? AND run_id=?", (int(seg[4]), rid)).fetchone()
            if not x:
                raise ApiError(404, "Result not found")
            if x["voided_at"]:
                raise ApiError(409, "That result is already voided")
            if not reason:
                raise ApiError(400, "Enter the reason for voiding this result")
            conn.execute("UPDATE lab_results SET voided_at=?, voided_by=?, void_reason=? WHERE id=?", (now_iso(), user["name"], reason, x["id"]))
            release_log(conn, rid, "lab_result_voided", user, capacity=None,
                        meaning="Laboratory result voided: %s (report %s)." % (x["analyte"], x["report_number"]),
                        comment=reason, detail={"report": x["report_number"], "test": x["analyte"], "result": coa_result_text(x)})
            self._lab_regate(conn, r, user)
            return self._lab_results_payload(conn, r)
        raise ApiError(404, "Unknown lab-results endpoint")

    def route_coa_specs(self, method, seg, conn, user):
        def pub(s):
            return {"code": s["code"], "name": s["name"], "group": s["grp"], "unit": s["unit"], "basis": s["basis"],
                    "minVal": s["min_val"], "maxVal": s["max_val"], "maxExclusive": bool(s["max_exclusive"]),
                    "required": bool(s["required"]), "active": bool(s["active"]), "method": s["method"], "specText": coa_spec_text(s)}
        if seg == ["api", "coa-specs"] and method == "GET":
            return {"specs": [pub(s) for s in conn.execute("SELECT * FROM coa_specs ORDER BY sort, code")],
                    "applicationRate": float(get_setting_value(conn, "coa_application_rate_kg_ha", 0) or 0),
                    "applicationPeriods": float(get_setting_value(conn, "coa_application_periods", 1) or 1)}
        if len(seg) == 3 and method == "PUT":
            self._require_admin(user)
            s = conn.execute("SELECT * FROM coa_specs WHERE code=?", (seg[2],)).fetchone()
            if not s:
                raise ApiError(404, "Specification not found")
            d = self._body_json()

            def num(k, cur):
                if k not in d:
                    return cur
                v = d[k]
                if v in (None, ""):
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    raise ApiError(400, "Limits must be numbers")
            mn, mx = num("minVal", s["min_val"]), num("maxVal", s["max_val"])
            if mn is not None and mx is not None and mn > mx:
                raise ApiError(400, "Minimum cannot exceed maximum")
            conn.execute("UPDATE coa_specs SET min_val=?, max_val=?, max_exclusive=?, required=?, active=?, method=? WHERE code=?",
                         (mn, mx, 1 if d.get("maxExclusive", s["max_exclusive"]) else 0, 1 if d.get("required", s["required"]) else 0,
                          1 if d.get("active", s["active"]) else 0, (d.get("method", s["method"]) or "").strip() or None, s["code"]))
            return {"spec": pub(conn.execute("SELECT * FROM coa_specs WHERE code=?", (s["code"],)).fetchone())}
        raise ApiError(404, "Unknown endpoint")

    # ---- Samples: catalogue, retention inventory, cart, lab requisitions ---------------- #
    SAMPLE_SQL = (
        "SELECT s.*, r.processing_lot, r.run_date, r.sku_code, q.req_number, q.lab_name AS req_lab, q.attachment_id AS req_att, "
        "c.lab_id AS cart_lab_id, c.analyses AS cart_analyses, c.added_by AS cart_by "
        "FROM samples s JOIN production_runs r ON r.id=s.run_id "
        "LEFT JOIN lab_requisitions q ON q.id=s.requisition_id LEFT JOIN sample_cart c ON c.sample_id=s.id")

    def _sample_public(self, conn, r, months):
        retention = (r["description"] or "") == "Retention"
        collected = r["collected_at"] or r["created_at"]
        discard_by = add_months(collected, months) if retention else None
        try:
            analyses = json.loads(r["cart_analyses"]) if r["cart_analyses"] else []
        except ValueError:
            analyses = []
        return {"id": r["id"], "code": r["sample_code"], "idDetailed": r["id_detailed"] or r["processing_lot"], "idSimplified": r["id_simplified"] or lot_simplified(r["processing_lot"]),
                "labelType": r["label_type"] or "detailed", "idsLocked": bool(r["requisition_id"]),
                "displayId": (r["id_simplified"] or lot_simplified(r["processing_lot"])) if r["label_type"] == "simplified" else (r["id_detailed"] or r["processing_lot"]),
                "runId": r["run_id"], "processingLot": r["processing_lot"],
                "runDate": r["run_date"], "sku": r["sku_code"], "stage": r["stage"],
                "stageLabel": sample_stage_info(r["stage"])[1], "type": r["type"], "description": r["description"],
                "container": r["container"], "collectedAt": collected, "status": r["status"], "location": r["location"],
                "notes": r["notes"], "isRetention": retention, "discardBy": discard_by,
                "expired": bool(discard_by and discard_by < today_iso() and r["status"] in ("available", "in_cart")),
                "removedAt": r["removed_at"], "removedBy": r["removed_by"], "removedReason": r["removed_reason"],
                "requisitionId": r["requisition_id"], "reqNumber": r["req_number"], "reqLab": r["req_lab"],
                "reqAttachmentId": r["req_att"],
                "cartLabId": r["cart_lab_id"], "cartAnalyses": analyses, "inCart": r["status"] == "in_cart"}

    def route_samples(self, method, seg, query, conn, user):
        uname = user["name"] if user else None
        months = get_setting_value(conn, "sample_retention_months", 12)
        if seg == ["api", "samples"] and method == "GET":
            where, params = [], []
            if query.get("runId", [""])[0]:
                where.append("s.run_id=?"); params.append(int(query["runId"][0]))
            if query.get("status", [""])[0]:
                sts = [x for x in query["status"][0].split(",") if x]
                where.append("s.status IN (%s)" % ",".join("?" * len(sts))); params += sts
            ret = query.get("retention", [""])[0]
            if ret == "0":                      # the analysis catalogue: everything except Retention samples
                where.append("COALESCE(s.description,'')<>'Retention'")
            elif ret:                           # the retention inventory: only Retention samples
                where.append("s.description='Retention'")
            sql = self.SAMPLE_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.run_date DESC, r.id DESC, s.sample_code"
            return {"samples": [self._sample_public(conn, r, months) for r in conn.execute(sql, params)],
                    "retentionMonths": months}
        if seg == ["api", "samples", "location"] and method == "POST":
            d = self._body_json()
            ids = [int(x) for x in (d.get("ids") or [])]
            loc = (d.get("location") or "").strip() or None
            for sid in ids:
                row = conn.execute("SELECT * FROM samples WHERE id=?", (sid,)).fetchone()
                if row and row["location"] != loc:
                    conn.execute("UPDATE samples SET location=? WHERE id=?", (loc, sid))
                    sample_log(conn, sid, "location", "%s -> %s" % (row["location"] or "none", loc or "none"), uname)
            return {"updated": len(ids)}
        if len(seg) >= 3 and seg[2].isdigit():
            sid = int(seg[2])
            row = conn.execute(self.SAMPLE_SQL + " WHERE s.id=?", (sid,)).fetchone()
            if not row:
                raise ApiError(404, "Sample not found")
            if len(seg) == 3 and method == "GET":
                d = self._sample_public(conn, row, months)
                d["events"] = [{"type": e["event_type"], "detail": e["detail"], "by": e["user_name"], "at": e["created_at"]}
                               for e in conn.execute("SELECT * FROM sample_events WHERE sample_id=? ORDER BY id DESC", (sid,))]
                return d
            if len(seg) == 3 and method == "PUT":
                d = self._body_json()
                if "notes" in d:
                    conn.execute("UPDATE samples SET notes=? WHERE id=?", ((d["notes"] or "").strip() or None, sid))
                    sample_log(conn, sid, "note", (d["notes"] or "").strip() or "(cleared)", uname)
                if "location" in d:
                    loc = (d["location"] or "").strip() or None
                    if loc != row["location"]:
                        conn.execute("UPDATE samples SET location=? WHERE id=?", (loc, sid))
                        sample_log(conn, sid, "location", "%s -> %s" % (row["location"] or "none", loc or "none"), uname)
                return self._sample_public(conn, conn.execute(self.SAMPLE_SQL + " WHERE s.id=?", (sid,)).fetchone(), months)
            if len(seg) == 4 and seg[3] == "remove" and method == "POST":
                d = self._body_json()
                reason = (d.get("reason") or "").strip()
                if not reason:
                    raise ApiError(400, "A reason is required to remove a sample")
                if row["status"] not in ("available", "in_cart"):
                    raise ApiError(400, "This sample is already %s" % row["status"])
                note = (d.get("note") or "").strip()
                conn.execute("DELETE FROM sample_cart WHERE sample_id=?", (sid,))
                conn.execute("UPDATE samples SET status='removed', removed_at=?, removed_by=?, removed_reason=? WHERE id=?",
                             (now_iso(), uname, reason + (" - " + note if note else ""), sid))
                sample_log(conn, sid, "removed", reason + (" - " + note if note else ""), uname)
                return self._sample_public(conn, conn.execute(self.SAMPLE_SQL + " WHERE s.id=?", (sid,)).fetchone(), months)
            if len(seg) == 4 and seg[3] == "restore" and method == "POST":
                if row["status"] != "removed":
                    raise ApiError(400, "Only a removed sample can be restored")
                conn.execute("UPDATE samples SET status='available', removed_at=NULL, removed_by=NULL, removed_reason=NULL WHERE id=?", (sid,))
                sample_log(conn, sid, "restored", "Removal reversed", uname)
                return self._sample_public(conn, conn.execute(self.SAMPLE_SQL + " WHERE s.id=?", (sid,)).fetchone(), months)
        raise ApiError(404, "Unknown samples endpoint")

    # -- labs (admin-maintained) -- #
    @staticmethod
    def _ready_template_path(lab_name):
        """The token-ready KelpWorks copy of this lab's form shipped in docs/requisition-templates (matched on the lab's name), or None."""
        key = re.sub(r"[^a-z0-9]", "", (lab_name or "").lower())
        want = "foodassure" if "foodassure" in key else "sgs" if "sgs" in key else None
        folder = os.path.join(BASE_DIR, "docs", "requisition-templates")
        if want and os.path.isdir(folder):
            for fn in sorted(os.listdir(folder)):
                if fn.lower().endswith(".docx") and re.sub(r"[^a-z0-9]", "", fn.lower()).startswith(want):
                    return os.path.join(folder, fn)
        return None

    def _lab_public(self, conn, r):
        tokens = None
        if r["template_stored"]:
            try:
                with open(os.path.join(LAB_DIR, r["template_stored"]), "rb") as f:
                    tokens = len(docx_inspect(f.read())[0])
            except Exception:
                tokens = 0
        ready = self._ready_template_path(r["name"])
        return {"id": r["id"], "name": r["name"], "templateTokens": tokens, "readyTemplate": os.path.basename(ready) if ready else None, "contact": r["contact"], "email": r["email"], "phone": r["phone"],
                "address": r["address"], "notes": r["notes"], "active": bool(r["active"]),
                "hasTemplate": bool(r["template_stored"]), "templateName": r["template_name"],
                "sampleSheet": bool(r["sample_sheet"]), "mergeIds": bool(r["merge_ids"]),
                "analyses": [{"id": a["id"], "name": a["name"], "code": a["code"], "notes": a["notes"], "method": a["method"],
                              "active": bool(a["active"]), "category": a["category"], "kind": a["kind"] or "analysis",
                              "symbol": a["symbol"], "capacity": a["capacity"], "reqNote": a["req_note"]}
                             for a in conn.execute("SELECT * FROM lab_analyses WHERE lab_id=? ORDER BY name", (r["id"],))]}

    @staticmethod
    def _analysis_kind(d, cur=None):
        """(kind, capacity) from a request body: analysis | mineral | scan (a scan needs the number of minerals it covers)."""
        kind = d.get("kind", cur["kind"] if cur else "analysis") or "analysis"
        if kind not in ("analysis", "mineral", "scan"):
            raise ApiError(400, "Type must be Analysis, Mineral or Mineral scan")
        cap = None
        if kind == "scan":
            try:
                cap = int(d["capacity"]) if d.get("capacity") not in (None, "") else (cur["capacity"] if cur else None)
            except (TypeError, ValueError):
                cap = None
            if not cap or cap < 1:
                raise ApiError(400, "Enter how many minerals this scan covers")
        return kind, cap

    def _labs_all(self, conn):
        return [self._lab_public(conn, r) for r in conn.execute("SELECT * FROM labs ORDER BY name")]

    def _template_report(self, conn, lab_id, raw):
        tokens, has_row = docx_inspect(raw)
        warnings = []
        lab = conn.execute("SELECT sample_sheet FROM labs WHERE id=?", (lab_id,)).fetchone()
        if not has_row and not (lab and lab["sample_sheet"]):
            warnings.append("No table row with {{sample.*}} tokens was found, so the list of samples will not appear in the requisition "
                            "(tick \"Also generate a sample spreadsheet\" on the lab if the lab takes the sample list as an attachment).")
        names = {a["name"].lower() for a in conn.execute("SELECT name FROM lab_analyses WHERE lab_id=?", (lab_id,))}
        names |= {(a["code"] or "").lower() for a in conn.execute("SELECT code FROM lab_analyses WHERE lab_id=?", (lab_id,))}
        for t in tokens:
            if t.startswith("sample.check:"):
                if t[13:].strip().lower() not in names:
                    warnings.append("Checkbox token {{%s}} does not match any analysis (name or code) for this lab." % t)
            elif t.startswith("sample."):
                if t[7:] not in REQ_SAMPLE_KEYS:
                    warnings.append("Unknown sample token {{%s}}." % t)
            elif t.startswith("analysis."):
                if t[9:] not in REQ_ANALYSIS_KEYS:
                    warnings.append("Unknown analysis token {{%s}}." % t)
            elif t not in REQ_SCALAR_TOKENS:
                warnings.append("Unknown token {{%s}} (it will be left blank)." % t)
        return {"tokens": tokens, "hasSampleRow": has_row, "warnings": warnings}

    def route_labs(self, method, seg, conn, user):
        if seg == ["api", "labs"]:
            if method == "GET":
                return {"labs": self._labs_all(conn)}
            if method == "POST":
                self._require_admin(user)
                d = self._body_json()
                name = (d.get("name") or "").strip()
                if not name:
                    raise ApiError(400, "Lab name is required")
                if conn.execute("SELECT 1 FROM labs WHERE name=?", (name,)).fetchone():
                    raise ApiError(409, "A lab with that name already exists")
                cur = conn.execute("INSERT INTO labs (name,contact,email,phone,address,notes,sample_sheet,merge_ids,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                   (name, d.get("contact"), d.get("email"), d.get("phone"), d.get("address"), d.get("notes"),
                                    1 if d.get("sampleSheet") else 0, 1 if d.get("mergeIds") else 0, now_iso()))
                return self._lab_public(conn, conn.execute("SELECT * FROM labs WHERE id=?", (cur.lastrowid,)).fetchone())
        if len(seg) >= 3 and seg[2].isdigit():
            lid = int(seg[2])
            lab = conn.execute("SELECT * FROM labs WHERE id=?", (lid,)).fetchone()
            if not lab:
                raise ApiError(404, "Lab not found")
            if len(seg) == 3:
                self._require_admin(user)
                if method == "PUT":
                    d = self._body_json()
                    name = (d.get("name") or lab["name"]).strip()
                    if name != lab["name"] and conn.execute("SELECT 1 FROM labs WHERE name=? AND id<>?", (name, lid)).fetchone():
                        raise ApiError(409, "A lab with that name already exists")
                    conn.execute("UPDATE labs SET name=?, contact=?, email=?, phone=?, address=?, notes=?, active=?, sample_sheet=?, merge_ids=? WHERE id=?",
                                 (name, d.get("contact", lab["contact"]), d.get("email", lab["email"]), d.get("phone", lab["phone"]),
                                  d.get("address", lab["address"]), d.get("notes", lab["notes"]),
                                  (1 if d["active"] else 0) if "active" in d else lab["active"],
                                  (1 if d["sampleSheet"] else 0) if "sampleSheet" in d else lab["sample_sheet"],
                                  (1 if d["mergeIds"] else 0) if "mergeIds" in d else lab["merge_ids"], lid))
                    return self._lab_public(conn, conn.execute("SELECT * FROM labs WHERE id=?", (lid,)).fetchone())
                if method == "DELETE":
                    if conn.execute("SELECT 1 FROM lab_requisitions WHERE lab_id=?", (lid,)).fetchone() or \
                            conn.execute("SELECT 1 FROM sample_cart WHERE lab_id=?", (lid,)).fetchone():
                        raise ApiError(400, "This lab has requisitions or cart samples -- deactivate it instead of deleting")
                    if lab["template_stored"]:
                        try:
                            os.remove(os.path.join(LAB_DIR, lab["template_stored"]))
                        except OSError:
                            pass
                    conn.execute("DELETE FROM labs WHERE id=?", (lid,))
                    return {"ok": True}
            if len(seg) >= 4 and seg[3] == "analyses":
                self._require_admin(user)
                if len(seg) == 4 and method == "POST":
                    d = self._body_json()
                    name = (d.get("name") or "").strip()
                    if not name:
                        raise ApiError(400, "Analysis name is required")
                    if conn.execute("SELECT 1 FROM lab_analyses WHERE lab_id=? AND name=?", (lid, name)).fetchone():
                        raise ApiError(409, "That analysis already exists for this lab")
                    kind, cap = self._analysis_kind(d)
                    conn.execute("INSERT INTO lab_analyses (lab_id,name,code,notes,method,category,kind,symbol,capacity,req_note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (lid, name, (d.get("code") or "").strip() or None, (d.get("notes") or "").strip() or None,
                                  (d.get("method") or "").strip() or None, (d.get("category") or "").strip() or None, kind,
                                  (d.get("symbol") or "").strip() or None, cap, (d.get("reqNote") or "").strip()[:300] or None))
                    return self._lab_public(conn, lab)
                if len(seg) == 5 and seg[4].isdigit():
                    aid = int(seg[4])
                    a = conn.execute("SELECT * FROM lab_analyses WHERE id=? AND lab_id=?", (aid, lid)).fetchone()
                    if not a:
                        raise ApiError(404, "Analysis not found")
                    if method == "PUT":
                        d = self._body_json()
                        name = (d.get("name") or a["name"]).strip()
                        if name != a["name"] and conn.execute("SELECT 1 FROM lab_analyses WHERE lab_id=? AND name=? AND id<>?", (lid, name, aid)).fetchone():
                            raise ApiError(409, "That analysis already exists for this lab")
                        kind, cap = self._analysis_kind(d, a)
                        conn.execute("UPDATE lab_analyses SET name=?, code=?, notes=?, method=?, active=?, category=?, kind=?, symbol=?, capacity=?, req_note=? WHERE id=?",
                                     (name, d.get("code", a["code"]), d.get("notes", a["notes"]), d.get("method", a["method"]),
                                      (1 if d["active"] else 0) if "active" in d else a["active"],
                                      ((d.get("category") or "").strip() or None) if "category" in d else a["category"], kind,
                                      ((d.get("symbol") or "").strip() or None) if "symbol" in d else a["symbol"], cap,
                                      ((d.get("reqNote") or "").strip()[:300] or None) if "reqNote" in d else a["req_note"], aid))
                        return self._lab_public(conn, lab)
                    if method == "DELETE":
                        conn.execute("DELETE FROM lab_analyses WHERE id=?", (aid,))
                        # drop it from any cart assignment that still lists it
                        for c in conn.execute("SELECT * FROM sample_cart WHERE lab_id=?", (lid,)).fetchall():
                            ids = [x for x in (json.loads(c["analyses"]) if c["analyses"] else []) if x != aid]
                            conn.execute("UPDATE sample_cart SET analyses=? WHERE id=?", (json.dumps(ids), c["id"]))
                        return self._lab_public(conn, lab)
            if len(seg) == 5 and seg[3] == "template" and seg[4] == "builtin" and method == "POST":
                self._require_admin(user)
                path = self._ready_template_path(lab["name"])
                if not path:
                    raise ApiError(404, "There is no ready-made template for this lab. Upload its form under Template instead.")
                with open(path, "rb") as f:
                    raw = f.read()
                report = self._template_report(conn, lid, raw)
                os.makedirs(LAB_DIR, exist_ok=True)
                stored = secrets.token_hex(8) + ".docx"
                with open(os.path.join(LAB_DIR, stored), "wb") as f:
                    f.write(raw)
                if lab["template_stored"]:
                    try:
                        os.remove(os.path.join(LAB_DIR, lab["template_stored"]))
                    except OSError:
                        pass
                conn.execute("UPDATE labs SET template_name=?, template_stored=? WHERE id=?", (os.path.basename(path), stored, lid))
                return {"lab": self._lab_public(conn, conn.execute("SELECT * FROM labs WHERE id=?", (lid,)).fetchone()), "report": report}
            if len(seg) == 4 and seg[3] == "template":
                self._require_admin(user)
                if method == "POST":
                    d = self._body_json()
                    data_b64 = d.get("dataB64") or ""
                    if data_b64.startswith("data:") and "," in data_b64:
                        data_b64 = data_b64.split(",", 1)[1]
                    try:
                        raw = base64.b64decode(data_b64)
                    except Exception:
                        raise ApiError(400, "Could not decode file data")
                    report = self._template_report(conn, lid, raw)
                    os.makedirs(LAB_DIR, exist_ok=True)
                    stored = secrets.token_hex(8) + ".docx"
                    with open(os.path.join(LAB_DIR, stored), "wb") as f:
                        f.write(raw)
                    if lab["template_stored"]:
                        try:
                            os.remove(os.path.join(LAB_DIR, lab["template_stored"]))
                        except OSError:
                            pass
                    fname = (d.get("filename") or "template.docx").replace("\\", "/").split("/")[-1]
                    conn.execute("UPDATE labs SET template_name=?, template_stored=? WHERE id=?", (fname, stored, lid))
                    return {"lab": self._lab_public(conn, conn.execute("SELECT * FROM labs WHERE id=?", (lid,)).fetchone()), "report": report}
                if method == "DELETE":
                    if lab["template_stored"]:
                        try:
                            os.remove(os.path.join(LAB_DIR, lab["template_stored"]))
                        except OSError:
                            pass
                    conn.execute("UPDATE labs SET template_name=NULL, template_stored=NULL WHERE id=?", (lid,))
                    return self._lab_public(conn, conn.execute("SELECT * FROM labs WHERE id=?", (lid,)).fetchone())
        raise ApiError(404, "Unknown labs endpoint")

    # -- the cart -- #
    def _cart_items(self, conn, months):
        sql = self.SAMPLE_SQL + " WHERE s.status='in_cart' ORDER BY r.run_date DESC, r.id DESC, s.sample_code"
        return [self._sample_public(conn, r, months) for r in conn.execute(sql)]

    def _cart_response(self, conn):
        return {"items": self._cart_items(conn, get_setting_value(conn, "sample_retention_months", 12)),
                "labs": [l for l in self._labs_all(conn) if l["active"]]}

    def _validate_assignment(self, conn, lab_id, analysis_ids):
        lab = conn.execute("SELECT * FROM labs WHERE id=? AND active=1", (lab_id,)).fetchone()
        if not lab:
            raise ApiError(400, "Choose an active lab")
        ok = {a["id"] for a in conn.execute("SELECT id FROM lab_analyses WHERE lab_id=? AND active=1 AND kind!='scan'", (lab_id,))}   # scans are added automatically
        ids = [int(x) for x in (analysis_ids or [])]
        bad = [x for x in ids if x not in ok]
        if bad:
            raise ApiError(400, "Those analyses are not offered by %s" % lab["name"])
        return sorted(set(ids))

    def route_cart(self, method, seg, conn, user):
        uname = user["name"] if user else None
        if seg == ["api", "cart"]:
            if method == "GET":
                return self._cart_response(conn)
            if method == "POST":
                for sid in [int(x) for x in (self._body_json().get("sampleIds") or [])]:
                    s = conn.execute("SELECT * FROM samples WHERE id=?", (sid,)).fetchone()
                    if not s or s["status"] != "available":
                        continue
                    conn.execute("UPDATE samples SET status='in_cart' WHERE id=?", (sid,))
                    conn.execute("INSERT OR IGNORE INTO sample_cart (sample_id,added_by,added_at) VALUES (?,?,?)", (sid, uname, now_iso()))
                    sample_log(conn, sid, "cart_add", "Added to the lab cart", uname)
                return self._cart_response(conn)
        if seg == ["api", "cart", "assign"] and method == "POST":
            d = self._body_json()
            lab_id = int(d.get("labId") or 0)
            ids = self._validate_assignment(conn, lab_id, d.get("analysisIds"))
            lab = conn.execute("SELECT name FROM labs WHERE id=?", (lab_id,)).fetchone()
            names = {a["id"]: a["name"] for a in conn.execute("SELECT id,name FROM lab_analyses WHERE lab_id=?", (lab_id,))}
            for sid in [int(x) for x in (d.get("sampleIds") or [])]:
                if conn.execute("SELECT 1 FROM sample_cart WHERE sample_id=?", (sid,)).fetchone():
                    conn.execute("UPDATE sample_cart SET lab_id=?, analyses=? WHERE sample_id=?", (lab_id, json.dumps(ids), sid))
                    sample_log(conn, sid, "assigned", "%s: %s" % (lab["name"], ", ".join(names[i] for i in ids) or "no analyses yet"), uname)
            return self._cart_response(conn)
        if seg == ["api", "cart", "requisitions", "preview"] and method == "POST":
            # what would be created: the filled form + sample list for each lab / run that is ready (nothing is written)
            d = self._body_json()
            notes = (d.get("notes") or "").strip() or None
            contact = requisition_contact_clean(d["contact"], requisition_contact_get(conn)) if d.get("contact") else None
            out = []
            for (run_id, lab_id), items in self._cart_ready_groups(conn, d).items():
                po_number = self._requisition_po(conn, d, run_id, lab_id)
                b = self._requisition_build(conn, user, run_id, lab_id, items, notes, po_number, "(assigned when created)", contact)
                out.append({"lab": b["lab"]["name"], "lot": b["run"]["processing_lot"], "nSamples": len(items),
                            "templateName": b["lab"]["template_name"], "conflicts": b["conflicts"],
                            "ids": [sr["id"] for sr in b["sample_rows"]],          # kept for a browser still running the previous app.js
                            "lines": len(b["lines"]), "form": docx_to_html(b["doc"]),
                            "sheet": xlsx_to_html(b["sheet"]) if b["sheet"] else None})
            return {"previews": out}
        if seg == ["api", "cart", "requisitions"] and method == "POST":
            return self._create_requisitions(conn, user, self._body_json())
        if len(seg) == 3 and seg[2].isdigit():
            sid = int(seg[2])
            cart = conn.execute("SELECT * FROM sample_cart WHERE sample_id=?", (sid,)).fetchone()
            if not cart:
                raise ApiError(404, "That sample is not in the cart")
            if method == "PUT":
                d = self._body_json()
                lab_id = int(d["labId"]) if d.get("labId") else None
                ids = self._validate_assignment(conn, lab_id, d.get("analysisIds")) if lab_id else []
                conn.execute("UPDATE sample_cart SET lab_id=?, analyses=? WHERE sample_id=?", (lab_id, json.dumps(ids), sid))
                if lab_id:
                    lab = conn.execute("SELECT name FROM labs WHERE id=?", (lab_id,)).fetchone()
                    names = {a["id"]: a["name"] for a in conn.execute("SELECT id,name FROM lab_analyses WHERE lab_id=?", (lab_id,))}
                    sample_log(conn, sid, "assigned", "%s: %s" % (lab["name"], ", ".join(names[i] for i in ids) or "no analyses yet"), uname)
                return self._cart_response(conn)
            if method == "DELETE":
                conn.execute("DELETE FROM sample_cart WHERE sample_id=?", (sid,))
                conn.execute("UPDATE samples SET status='available' WHERE id=? AND status='in_cart'", (sid,))
                sample_log(conn, sid, "cart_remove", "Taken out of the cart", uname)
                return self._cart_response(conn)
        raise ApiError(404, "Unknown cart endpoint")

    # -- requisitions -- #
    def _requisition_public(self, conn, r):
        samples = []
        for rs in conn.execute("SELECT rs.*, s.sample_code, s.id_detailed, s.id_simplified, s.label_type, s.stage, s.type, s.description FROM requisition_samples rs "
                               "JOIN samples s ON s.id=rs.sample_id WHERE rs.requisition_id=? ORDER BY s.id", (r["id"],)):
            samples.append({"sampleId": rs["sample_id"], "code": rs["sample_code"], "idDetailed": rs["id_detailed"], "idSimplified": rs["id_simplified"],
                            "displayId": (rs["id_simplified"] if rs["label_type"] == "simplified" else rs["id_detailed"]) or rs["sample_code"], "stageLabel": sample_stage_info(rs["stage"])[1],
                            "type": rs["type"], "description": rs["description"],
                            "analyses": json.loads(rs["analyses"]) if rs["analyses"] else []})
        run = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (r["run_id"],)).fetchone()
        att = conn.execute("SELECT filename FROM run_attachments WHERE id=?", (r["attachment_id"],)).fetchone() if r["attachment_id"] else None
        sheet = conn.execute("SELECT filename FROM run_attachments WHERE id=?", (r["sheet_attachment_id"],)).fetchone() if r["sheet_attachment_id"] else None
        return {"id": r["id"], "reqNumber": r["req_number"], "runId": r["run_id"], "processingLot": run["processing_lot"] if run else None,
                "labId": r["lab_id"], "labName": r["lab_name"], "attachmentId": r["attachment_id"],
                "filename": att["filename"] if att else None, "poNumber": r["po_number"],
                "sheetAttachmentId": r["sheet_attachment_id"], "sheetFilename": sheet["filename"] if sheet else None,
                "notes": r["notes"], "createdBy": r["created_by"],
                "createdAt": r["created_at"], "samples": samples}

    def route_requisitions(self, method, seg, conn, user):
        if seg == ["api", "requisitions"] and method == "GET":
            return {"requisitions": [self._requisition_public(conn, r)
                                     for r in conn.execute("SELECT * FROM lab_requisitions ORDER BY id DESC")]}
        raise ApiError(404, "Unknown requisitions endpoint")

    def _requisition_po(self, conn, d, run_id, lab_id):
        """The PO / reference number of one requisition: what the user entered for that run + lab (`poNumbers`, keyed "run:lab", an empty string
        meaning "none"), else a single `poNumber`, else the production run's number -- the default, which the user can always change."""
        nums = d.get("poNumbers") or {}
        key = "%d:%d" % (run_id, lab_id)
        if key in nums:
            return (str(nums[key] or "").strip()[:60]) or None
        if (d.get("poNumber") or "").strip():
            return d["poNumber"].strip()[:60]
        run = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (run_id,)).fetchone()
        return run["processing_lot"] if run else None

    def _cart_ready_groups(self, conn, d):
        """{(run_id, lab_id): [(sample_id, [analysis ids])]} for the cart samples that have a lab and at least one analysis,
        optionally narrowed by sampleIds / labId / runId."""
        want = {int(x) for x in (d.get("sampleIds") or [])}
        lab_filter = int(d["labId"]) if d.get("labId") else None
        run_filter = int(d["runId"]) if d.get("runId") else None
        groups = {}
        for c in conn.execute("SELECT c.*, s.run_id FROM sample_cart c JOIN samples s ON s.id=c.sample_id ORDER BY s.id"):
            ids = json.loads(c["analyses"]) if c["analyses"] else []
            if not c["lab_id"] or not ids:
                continue
            if (want and c["sample_id"] not in want) or (lab_filter and c["lab_id"] != lab_filter) or \
                    (run_filter and c["run_id"] != run_filter):
                continue
            groups.setdefault((c["run_id"], c["lab_id"]), []).append((c["sample_id"], ids))
        if not groups:
            raise ApiError(400, "Nothing is ready: each cart sample needs a lab and at least one analysis")
        return groups

    def _requisition_build(self, conn, user, run_id, lab_id, items, notes, po_number, req_number, contact=None):
        """Fill a lab's requisition form (and its sample spreadsheet when the lab takes one) for one run + lab. Used by the real creation
        and by the preview (which passes a placeholder req_number); it writes nothing."""
        uname = user["name"] if user else None
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
        lab = conn.execute("SELECT * FROM labs WHERE id=?", (lab_id,)).fetchone()
        an = {a["id"]: a for a in conn.execute("SELECT * FROM lab_analyses WHERE lab_id=?", (lab_id,))}
        vols = {r_["name"]: r_["litres_each"] for r_ in conn.execute("SELECT name, litres_each FROM consumables WHERE is_sample_container=1")}
        sample_rows = []
        scan_pool = list(an.values())

        def analysis_fields(ids):
            """The analysis part of a requisition row for these analysis ids. Minerals are ticked one by one; the form asks for ONE mineral scan
            sized by how many were ticked (and the notes list them)."""
            mins = sorted([an[i] for i in ids if i in an and an[i]["kind"] == "mineral"],
                          key=lambda a: (MINERAL_ORDER.index(a["symbol"]) if a["symbol"] in MINERAL_ORDER else 99, a["name"]))
            direct = [an[i] for i in ids if i in an and an[i]["kind"] not in ("mineral", "scan")]
            scan = None
            if mins:
                scan, biggest = mineral_scan_for(scan_pool, len(mins))
                if scan is None:
                    raise ApiError(400, "%d minerals are more than the largest mineral scan %s offers (%d). Remove some minerals." % (len(mins), lab["name"], biggest))
            shown = ([scan] if scan else []) + direct
            names = [a["name"] for a in shown]
            methods = []
            for a in shown + (mins if not (scan and scan["method"]) else []):
                if a["method"] and a["method"] not in methods:
                    methods.append(a["method"])
            # the method of each requested analysis starts on the same line as its name (blank when it has none; a scan without its own method shows
            # its minerals' method). Every analysis is one line plus a blank spacer line in BOTH columns, so the two columns stay in step as long as no
            # name wraps -- the template's Analysis Requested column is wide enough for that (see CLAUDE.md).
            lines_m = [(a["method"] or (next((m["method"] for m in mins if m["method"]), "") if a is scan else "")) for a in shown]
            return {"analyses": ", ".join(names), "analyses_lines": "\n\n".join(names), "methods": ", ".join(methods),
                    "methods_lines": "\n\n".join(lines_m).rstrip("\n"),
                    "analysis_names": names, "analysis_codes": [a["code"] for a in shown if a["code"]], "_names": names,
                    "scan": scan["name"] if scan else "", "_minerals": [a["name"] for a in mins], "minerals": ", ".join(a["name"] for a in mins),
                    "_direct_ids": [a["id"] for a in direct], "_shown_ids": [a["id"] for a in shown]}
        for n, (sid, ids) in enumerate(items, 1):
            s = conn.execute("SELECT * FROM samples WHERE id=?", (sid,)).fetchone()
            sample_rows.append(dict({"n": str(n), "id": (s["id_simplified"] or lot_simplified(run["processing_lot"])),
                            "container_qty": "1", "volume": container_volume(vols.get(s["container"]), s["container"]),
                            "volume_text": volume_text([container_volume(vols.get(s["container"]), s["container"])]),
                            "id_detailed": s["id_detailed"] or "", "id_simplified": s["id_simplified"] or "", "code": s["sample_code"], "stage": sample_stage_info(s["stage"])[1], "type": s["type"] or "",
                                "description": s["description"] or "", "container": s["container"] or "",
                                "collected": (s["collected_at"] or "").replace("T", " ")[:16],
                                "location": s["location"] or "", "notes": s["notes"] or "", "_sid": sid, "_ids": list(ids)}, **analysis_fields(ids)))
        sku = conn.execute("SELECT name FROM fg_skus WHERE code=?", (run["sku_code"],)).fetchone()
        product = (sku["name"] if sku else run["sku_code"]) or ""
        for sr in sample_rows:
            # product, lot and process point only -- the sample type / description are not part of the text a lab prints on its report
            sr["report_description"] = " ".join(x for x in (product, run["processing_lot"], "-", sr["stage"]) if x)
            sr["sheet_description"] = " ".join(x for x in (run["processing_lot"], "-", sr["stage"]) if x)
        # labs that list a Sample ID once (SGS): the form, the sheet and the notes all work from the merged lines; the other labs get one form row per sample
        merge = bool(lab["merge_ids"])
        lines = consolidate_sample_rows(sample_rows, analysis_fields if merge else None)
        doc_rows = lines if merge else sample_rows
        # distinct analyses requested on this requisition (in lab order), with how many lines want each
        disp_ids, all_names = {}, []
        for r_ in doc_rows:
            for aid in r_["_shown_ids"]:
                disp_ids[aid] = disp_ids.get(aid, 0) + 1
            all_names += [x for x in r_["analysis_names"] if x not in all_names]
        req_analyses = []
        for aid in sorted(disp_ids):                              # the order the lab's analyses were added; a mineral scan counts once per line that needs it
            req_analyses.append({"name": an[aid]["name"], "code": an[aid]["code"] or "", "method": an[aid]["method"] or "", "n": str(len(req_analyses) + 1),
                                 "count": str(disp_ids[aid])})
        d_today = datetime.date.fromisoformat(today_iso())
        cont = contact or requisition_contact_get(conn)
        scalars = {"req_number": req_number, "date": today_iso(), "date_long": "%s %d, %d" % (d_today.strftime("%B"), d_today.day, d_today.year),
                   "po_number": po_number or "", "po_check": "\u2612" if po_number else "\u2610", "company": COMPANY_NAME, "lab_name": lab["name"],
                   "lab_contact": lab["contact"] or "", "lab_email": lab["email"] or "", "lab_phone": lab["phone"] or "",
                   "lab_address": lab["address"] or "", "processing_lot": run["processing_lot"], "run_date": run["run_date"],
                   "sku": run["sku_code"] or "", "product": product,
                   "requested_by": uname or "", "requested_by_email": (user["email"] if user else "") or "",
                   # one contact: the submitter's name / phone are also the results contact and the customer phone; the five emails are
                   # where results are sent (SGS "Send analysis results to", FoodAssure customer emails). The signature is the submitter's name.
                   "customer_phone": cont["submitter"]["phone"], **{"customer_email_%d" % (i + 1): cont["emails"][i] for i in range(5)},
                   "submitter_name": cont["submitter"]["name"], "submitter_phone": cont["submitter"]["phone"], "submitter_email": cont["submitter"]["email"],
                   "results_name": cont["submitter"]["name"], "results_phone": cont["submitter"]["phone"],
                   **{"results_email_%d" % (i + 1): cont["emails"][i] for i in range(5)},
                   "results_emails": "\n".join(e for e in cont["emails"] if e),
                   "br": "\n",
                   "sample_count": str(len(sample_rows)), "analyses": ", ".join(all_names),
                   # the minerals requested + each analysis's standing note, one per line, ahead of anything the user typed in the cart
                   "notes": requisition_notes(doc_rows, an, notes)}
        template = None
        if lab["template_stored"]:
            try:
                with open(os.path.join(LAB_DIR, lab["template_stored"]), "rb") as f:
                    template = f.read()
            except OSError:
                # never quietly swap in the built-in layout for a lab that has its own form
                raise ApiError(409, "The requisition template for %s (%s) is missing on the server. Upload it again under "
                                    "Admin > Labs & analyses > Template." % (lab["name"], lab["template_name"] or "template"))
        doc = docx_fill(template or build_starter_docx(), scalars, doc_rows, req_analyses)
        sheet = build_sample_sheet_xlsx(scalars, sample_rows, req_analyses, lines) if lab["sample_sheet"] else None
        return {"run": run, "lab": lab, "doc": doc, "sheet": sheet, "sample_rows": sample_rows, "scalars": scalars, "lines": lines,
                "conflicts": [] if merge else requisition_id_conflicts(sample_rows)}

    def _create_requisitions(self, conn, user, d):
        """Turn every ready cart sample (lab + at least one analysis) into requisitions -- one per run + lab --
        fill the lab's .docx template, save the document on the run, and take those samples out of the cart."""
        uname = user["name"] if user else None
        groups = self._cart_ready_groups(conn, d)
        notes = (d.get("notes") or "").strip() or None
        contact = requisition_contact_clean(d["contact"], requisition_contact_get(conn)) if d.get("contact") else None
        created = []
        for (run_id, lab_id), items in groups.items():
            lab = conn.execute("SELECT * FROM labs WHERE id=?", (lab_id,)).fetchone()
            po_number = self._requisition_po(conn, d, run_id, lab_id)
            ts = now_iso()
            cur = conn.execute("INSERT INTO lab_requisitions (req_number,run_id,lab_id,lab_name,notes,created_by,created_at,po_number) VALUES (?,?,?,?,?,?,?,?)",
                               ("TEMP-" + secrets.token_hex(6), run_id, lab_id, lab["name"], notes, uname, ts, po_number))
            rid = cur.lastrowid
            req_number = "REQ-%s-%03d" % (today_iso().replace("-", ""), rid)
            b = self._requisition_build(conn, user, run_id, lab_id, items, notes, po_number, req_number, contact)
            run, sample_rows = b["run"], b["sample_rows"]
            safe_lab = re.sub(r"[^A-Za-z0-9]+", "_", lab["name"]).strip("_")
            att_id = self._store_attachment(conn, run_id, "%s_%s_%s.docx" % (req_number, safe_lab, run["processing_lot"]),
                                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                            base64.b64encode(b["doc"]).decode("ascii"), uname)
            sheet_id = None
            if b["sheet"]:
                sheet_id = self._store_attachment(
                    conn, run_id, "%s_%s_%s_samples.xlsx" % (req_number, safe_lab, run["processing_lot"]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    base64.b64encode(b["sheet"]).decode("ascii"), uname)
            conn.execute("UPDATE lab_requisitions SET req_number=?, attachment_id=?, sheet_attachment_id=? WHERE id=?",
                         (req_number, att_id, sheet_id, rid))
            for sr in sample_rows:
                sid = sr["_sid"]
                conn.execute("INSERT INTO requisition_samples (requisition_id,sample_id,analyses) VALUES (?,?,?)",
                             (rid, sid, json.dumps(sr["_names"])))
                conn.execute("UPDATE samples SET status='submitted', requisition_id=? WHERE id=?", (rid, sid))
                conn.execute("DELETE FROM sample_cart WHERE sample_id=?", (sid,))
                sample_log(conn, sid, "requisition", "%s to %s: %s" % (req_number, lab["name"], ", ".join(sr["_names"])), uname)
            created.append(self._requisition_public(conn, conn.execute("SELECT * FROM lab_requisitions WHERE id=?", (rid,)).fetchone()))
        resp = self._cart_response(conn)
        resp["requisitions"] = created
        return resp

    # ---- Pre-Processing: shred + blend + pack back into feedstock inventory ---------- #
    PREPROC_FIELDS = [
        ("batch_date", "batchDate", "text"), ("location", "location", "text"), ("operators", "operators", "text"),
        ("shredder", "shredder", "text"), ("notes", "notes", "text"),
        ("shredded_kg", "shreddedKg", "num"), ("start_solids_pct", "startSolidsPct", "num"),
        ("target_solids_pct", "targetSolidsPct", "num"), ("recommended_water_l", "recommendedWaterL", "num"),
        ("water_added_l", "waterAddedL", "num"), ("blend_volume_l", "blendVolumeL", "num"),
        ("final_solids_pct", "finalSolidsPct", "num"), ("measured_ph", "measuredPh", "num"),
        ("target_ph", "targetPh", "num"), ("citric_kg", "citricKg", "num"), ("citric_item_id", "citricItemId", "num"),
    ]

    def _preproc_sources(self, conn):
        """{batch id: (batch lot, [source tote lots])} for every batch that produced a blend."""
        out = {}
        for b in conn.execute("SELECT id, batch_lot FROM preproc_batches"):
            lots = [r["lot_number"] for r in conn.execute(
                "SELECT t.lot_number FROM preproc_inputs pi JOIN tote_lots t ON t.id=pi.tote_lot_id "
                "WHERE pi.batch_id=? ORDER BY t.lot_number", (b["id"],))]
            out[b["id"]] = (b["batch_lot"], lots)
        return out

    def _preproc_inputs_public(self, conn, bid):
        return [{"toteLotId": r["id"], "lot": r["lot_number"], "site": r["site_code"],
                 "species": r["species_code"], "harvestDate": r["checkin_date"],
                 "avgWeightKg": r["avg_weight_kg"], "weightKg": r["weight_kg"], "volumeL": r["in_volume_l"], "ph": r["ph"],
                 "location": r["location"], "status": r["status"]}
                for r in conn.execute(
                    "SELECT pi.weight_kg, pi.volume_l AS in_volume_l, t.* FROM preproc_inputs pi JOIN tote_lots t ON t.id=pi.tote_lot_id "
                    "WHERE pi.batch_id=? ORDER BY t.lot_number", (bid,))]

    def _preproc_public(self, conn, r, full=True):
        d = {"id": r["id"], "batchLot": r["batch_lot"], "status": r["status"],
             "createdBy": r["created_by"], "createdAt": r["created_at"],
             "completedAt": r["completed_at"], "completedBy": r["completed_by"]}
        for col, key, _kind in self.PREPROC_FIELDS:
            d[key] = r[col]
        n_in = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(weight_kg),0) kg FROM preproc_inputs WHERE batch_id=?",
                            (r["id"],)).fetchone()
        d["inputCount"], d["inputKg"] = n_in["n"], round(n_in["kg"], 2)
        if r["status"] == "draft":
            d["progress"] = self._preproc_progress(conn, r)
        outs = [tote_public(t) for t in conn.execute(
            "SELECT * FROM tote_lots WHERE preproc_batch_id=? ORDER BY tote_number", (r["id"],))]
        d["outputCount"] = len(outs)
        d["packedL"] = round(sum(o["volumeL"] or 0 for o in outs), 1) if outs else None
        if full:
            d["inputs"] = self._preproc_inputs_public(conn, r["id"])
            d["packaging"] = [{"container": p["container"], "qty": p["qty"], "litresEach": p["litres_each"]}
                              for p in conn.execute("SELECT * FROM preproc_packaging WHERE batch_id=? ORDER BY id",
                                                    (r["id"],))]
            d["outputs"] = outs
            d["problems"] = self._preproc_problems(conn, r) if r["status"] == "draft" else []
        return d

    def _preproc_get(self, conn, bid, draft_only=False):
        r = conn.execute("SELECT * FROM preproc_batches WHERE id=?", (bid,)).fetchone()
        if not r:
            raise ApiError(404, "Pre-processing batch not found")
        if draft_only and r["status"] != "draft":
            raise ApiError(409, "This batch is completed and can no longer be changed")
        return r

    def _preproc_progress(self, conn, b):
        """Per-section progress for a draft batch (same shape as a production run's `progress`, so the card's chips are
        the same component). Blend is optional; every other section's fields are required to complete the batch."""
        inputs = conn.execute("SELECT * FROM preproc_inputs WHERE batch_id=?", (b["id"],)).fetchall()
        packs = conn.execute("SELECT * FROM preproc_packaging WHERE batch_id=?", (b["id"],)).fetchall()
        has = lambda v: v is not None and v != ""

        def sec(key, label, items, required=True):
            missing = [lab for lab, ok in items if not ok]
            total = len(items)
            return {"key": key, "label": label, "total": total, "filled": total - len(missing), "missing": missing,
                    "done": not missing, "started": len(missing) < total, "required": required, "optional": not required}
        sections = [
            sec("initiation", "Initiation", [("Batch date", has(b["batch_date"])), ("Operators", has(b["operators"]))]),
            sec("pick", "Feedstock pick list", [
                ("At least one feedstock tote", bool(inputs)),
                ("Weight for every feedstock tote", bool(inputs) and all(i["weight_kg"] and i["weight_kg"] > 0 for i in inputs))]),
            sec("blend", "Blend", [("Starting % solids", has(b["start_solids_pct"])), ("Target % solids", has(b["target_solids_pct"])),
                                   ("Dilution water added (L)", has(b["water_added_l"])), ("Blend volume (L)", has(b["blend_volume_l"]))], False),
            sec("ph", "pH balancing", [("Measured pH", has(b["measured_ph"])), ("Target pH", has(b["target_ph"])),
                                       ("Citric acid added (kg)", has(b["citric_kg"]))]),
            sec("pack", "Pack-out", [
                ("Output location", has(b["location"])), ("At least one pack-out row", bool(packs)),
                ("Container, quantity and fill volume on every pack-out row",
                 bool(packs) and all(p_["container"] and p_["qty"] and p_["qty"] > 0 and p_["litres_each"] and p_["litres_each"] > 0 for p_ in packs))]),
        ]
        req = [x for x in sections if x["required"]]
        return {"sections": sections, "requiredTotal": sum(x["total"] for x in req), "requiredFilled": sum(x["filled"] for x in req),
                "complete": all(x["done"] for x in req)}

    def _preproc_problems(self, conn, b):
        """Everything a batch still needs before it can be completed (the Blend section is optional)."""
        return [m for x in self._preproc_progress(conn, b)["sections"] if x["required"] for m in x["missing"]]

    def route_preproc(self, method, seg, conn, user):
        uname = user["name"] if user else None
        if seg == ["api", "preproc"]:
            if method == "GET":
                rows = conn.execute("SELECT * FROM preproc_batches ORDER BY id DESC").fetchall()
                return {"batches": [self._preproc_public(conn, r, full=False) for r in rows]}
            if method == "POST":
                ts = now_iso()
                cur = conn.cursor()
                cur.execute("INSERT INTO preproc_batches (batch_lot,batch_date,target_solids_pct,target_ph,created_by,"
                            "created_at) VALUES (?,?,?,?,?,?)",
                            ("TEMP-" + secrets.token_hex(6), today_iso(),
                             get_setting_value(conn, "preproc_target_solids_pct", 50),
                             get_setting_value(conn, "preproc_target_ph", 3.7), uname, ts))
                bid = cur.lastrowid
                cur.execute("UPDATE preproc_batches SET batch_lot=? WHERE id=?",
                            ("BL-%s-%03d" % (ts[:10].replace("-", ""), bid), bid))
                return self._preproc_public(conn, self._preproc_get(conn, bid))
            raise ApiError(405, "Method not allowed")
        if len(seg) < 3 or not seg[2].isdigit():
            raise ApiError(404, "Unknown pre-processing endpoint")
        bid = int(seg[2])
        if len(seg) == 3:
            if method == "GET":
                return self._preproc_public(conn, self._preproc_get(conn, bid))
            b = self._preproc_get(conn, bid, draft_only=True)
            if method == "PUT":
                d = self._body_json()
                updates = {}
                for col, key, kind in self.PREPROC_FIELDS:
                    if key in d:
                        updates[col] = numn(d[key]) if kind == "num" else ((d[key] or "").strip() or None)
                if "location" in updates and updates["location"]:
                    self._ensure_location(conn, updates["location"])
                if updates:
                    conn.execute("UPDATE preproc_batches SET %s WHERE id=?" % ", ".join("%s=?" % c for c in updates),
                                 (*updates.values(), bid))
                return self._preproc_public(conn, self._preproc_get(conn, bid))
            if method == "DELETE":
                # discard a draft: every locked tote goes back into stock
                for i in conn.execute("SELECT tote_lot_id FROM preproc_inputs WHERE batch_id=?", (bid,)).fetchall():
                    self._preproc_release_tote(conn, i["tote_lot_id"], b, user)
                conn.execute("DELETE FROM preproc_batches WHERE id=?", (bid,))
                return {"ok": True}
            raise ApiError(405, "Method not allowed")
        b = self._preproc_get(conn, bid, draft_only=True)
        if len(seg) == 4 and seg[3] == "inputs" and method == "POST":
            ids = [int(x) for x in (self._body_json().get("toteIds") or [])]
            if not ids:
                raise ApiError(400, "Select at least one tote")
            for tid in ids:
                t = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()
                if not t or t["status"] != "in_stock":
                    raise ApiError(400, "Tote %s is not in stock" % (t["lot_number"] if t else tid))
                if (t["grind"] or "Coarse") != "Coarse":
                    raise ApiError(400, "Tote %s is already fine grind" % t["lot_number"])
                self._log_stability(conn, tid, user, "Status", "in_stock", "wip",
                                    "Pulled for pre-processing batch %s" % b["batch_lot"])
                conn.execute("UPDATE tote_lots SET status='wip' WHERE id=?", (tid,))
                conn.execute("INSERT INTO preproc_inputs (batch_id,tote_lot_id,weight_kg,volume_l) VALUES (?,?,?,?)",
                             (bid, tid, t["avg_weight_kg"], t["volume_l"]))
            return self._preproc_public(conn, self._preproc_get(conn, bid))
        if len(seg) == 5 and seg[3] == "inputs" and seg[4].isdigit():
            tid = int(seg[4])
            row = conn.execute("SELECT * FROM preproc_inputs WHERE batch_id=? AND tote_lot_id=?", (bid, tid)).fetchone()
            if not row:
                raise ApiError(404, "That tote is not part of this batch")
            if method == "PUT":
                d = self._body_json()
                if "weightKg" in d:
                    conn.execute("UPDATE preproc_inputs SET weight_kg=? WHERE id=?", (numn(d.get("weightKg")), row["id"]))
                if "volumeL" in d:
                    conn.execute("UPDATE preproc_inputs SET volume_l=? WHERE id=?", (numn(d.get("volumeL")), row["id"]))
            elif method == "DELETE":
                self._preproc_release_tote(conn, tid, b, user)
                conn.execute("DELETE FROM preproc_inputs WHERE id=?", (row["id"],))
            else:
                raise ApiError(405, "Method not allowed")
            return self._preproc_public(conn, self._preproc_get(conn, bid))
        if len(seg) == 4 and seg[3] == "packaging" and method == "PUT":
            conn.execute("DELETE FROM preproc_packaging WHERE batch_id=?", (bid,))
            for row in (self._body_json().get("rows") or []):
                container = (row.get("container") or "").strip() or None
                qty = int(num(row.get("qty")))
                if container or qty:
                    conn.execute("INSERT INTO preproc_packaging (batch_id,container,qty,litres_each) VALUES (?,?,?,?)",
                                 (bid, container, qty, numn(row.get("litresEach"))))
            return self._preproc_public(conn, self._preproc_get(conn, bid))
        if len(seg) == 4 and seg[3] == "complete" and method == "POST":
            return self._preproc_complete(conn, b, user)
        raise ApiError(404, "Unknown pre-processing endpoint")

    def _preproc_release_tote(self, conn, tid, batch, user):
        t = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tid,)).fetchone()
        if t and t["status"] == "wip":
            self._log_stability(conn, tid, user, "Status", "wip", "in_stock",
                                "Released from pre-processing batch %s" % batch["batch_lot"])
            conn.execute("UPDATE tote_lots SET status='in_stock' WHERE id=?", (tid,))

    def _preproc_complete(self, conn, b, user):
        """Finish the batch in one transaction: create the fine-grind output IBCs as feedstock lots,
        consume the source totes, deduct citric acid + the empty IBCs used, and return the emptied
        source IBCs to the Used IBC pool."""
        bid, uname = b["id"], (user["name"] if user else None)
        problems = self._preproc_problems(conn, b)
        if problems:
            raise ApiError(400, "Complete these required fields first: " + "; ".join(problems))
        inputs = conn.execute(
            "SELECT pi.weight_kg, t.* FROM preproc_inputs pi JOIN tote_lots t ON t.id=pi.tote_lot_id "
            "WHERE pi.batch_id=? ORDER BY t.lot_number", (bid,)).fetchall()
        for i in inputs:
            if i["status"] != "wip":
                raise ApiError(400, "Tote %s is no longer locked to this batch (status %s)" % (i["lot_number"], i["status"]))
        packs = conn.execute("SELECT * FROM preproc_packaging WHERE batch_id=? ORDER BY id", (bid,)).fetchall()
        total_l = sum(p["qty"] * p["litres_each"] for p in packs)
        # shredded mass = the weights of the pulled totes; blend mass adds the dilution water (1 kg = 1 L)
        shredded_kg = round(sum(i["weight_kg"] for i in inputs), 2)
        conn.execute("UPDATE preproc_batches SET shredded_kg=? WHERE id=?", (shredded_kg, bid))
        blend_kg = shredded_kg + (b["water_added_l"] or 0)
        sites = {i["site_code"] for i in inputs}
        species = {i["species_code"] for i in inputs}
        stab = {i["stabilization_method"] for i in inputs}
        site = sites.pop() if len(sites) == 1 else "MIX"
        sp = species.pop() if len(species) == 1 else "MIX"
        if site == "MIX":
            conn.execute("INSERT OR IGNORE INTO sites (code,name) VALUES ('MIX','Mixed sites (blend)')")
        if sp == "MIX":
            conn.execute("INSERT OR IGNORE INTO species (code,name,common) VALUES ('MIX','Mixed species','Mixed blend')")
        stabilization = stab.pop() if len(stab) == 1 else "Citric acid"
        harvests = sorted(i["checkin_date"] for i in inputs if i["checkin_date"])
        harvest_date = harvests[0] if harvests else None
        source_lots = [i["lot_number"] for i in inputs]
        ts = now_iso()
        # stock: containers first (they can block on a shortage), then citric acid
        need = {}
        for p in packs:
            need[p["container"]] = need.get(p["container"], 0) + p["qty"]
        for name, qty in need.items():
            if not self._consumable_by_name(conn, name):
                raise ApiError(400, "Unknown container: %s" % name)
            self._adjust_container_stock(conn, name, -qty, "Pre-processing pack-out", b["batch_lot"], uname)
        if b["citric_kg"]:
            citric = self._reagent_item(conn, "Citric Acid", b["citric_item_id"])
            if not citric:
                raise ApiError(400, "No Citric Acid reagent exists in Inventory Items")
            self._consume(conn, citric["id"], -b["citric_kg"], "Pre-processing pH adjustment", b["batch_lot"], uname)
        used = self._consumable_by_name(conn, "Used 1,000 L IBC Tote")
        if used and inputs:
            self._consume(conn, used["id"], len(inputs), "Emptied by pre-processing", b["batch_lot"], uname)
        # output lots, one per IBC
        outputs, n = [], 0
        for p in packs:
            for _ in range(p["qty"]):
                n += 1
                lot = "%s-%02d" % (b["batch_lot"], n)
                kg = round(blend_kg * p["litres_each"] / total_l, 2)
                conn.execute(
                    "INSERT INTO tote_lots (lot_number,site_code,species_code,harvest_year,checkin_date,received_date,"
                    "tote_number,volume_l,ph,ph_updated,avg_weight_kg,location,description,status,"
                    "stabilization_method,storage_unit,storage_source,created_at,grind,preproc_batch_id,solids_pct)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'in_stock', ?,?,?,?, 'Fine', ?, ?)",
                    (lot, site, sp, int(harvest_date[:4]) if harvest_date else None, harvest_date, b["batch_date"],
                     n, p["litres_each"], b["measured_ph"], b["batch_date"], kg, b["location"],
                     "Fine-ground blend of %d lot(s), batch %s" % (len(inputs), b["batch_lot"]),
                     stabilization, "Tote", p["container"], ts, bid, b["final_solids_pct"]))
                tid = conn.execute("SELECT id FROM tote_lots WHERE lot_number=?", (lot,)).fetchone()["id"]
                self._log_stability(conn, tid, user, "Created", None, lot,
                                    "Pre-processing batch %s from %s" % (b["batch_lot"], ", ".join(source_lots)))
                outputs.append(lot)
        for i in inputs:
            self._log_stability(conn, i["id"], user, "Status", "wip", "consumed",
                                "Shredded in pre-processing batch %s -> %s" % (b["batch_lot"], ", ".join(outputs)))
            conn.execute("UPDATE tote_lots SET status='consumed' WHERE id=?", (i["id"],))
        conn.execute("UPDATE preproc_batches SET status='completed', completed_at=?, completed_by=? WHERE id=?",
                     (ts, uname, bid))
        return self._preproc_public(conn, self._preproc_get(conn, bid))

    def _preproc_trace(self, conn, tote):
        """Traceability for one tote: if it is a fine-grind blend, the batch and the source lots it was
        made from; if it is a coarse tote that was shredded, the batch and the blend lots it went into."""
        bid = tote["preproc_batch_id"]
        role = "output" if bid else None
        if not bid:
            row = conn.execute("SELECT batch_id FROM preproc_inputs WHERE tote_lot_id=? ORDER BY id DESC",
                               (tote["id"],)).fetchone()
            bid, role = (row["batch_id"], "input") if row else (None, None)
        if not bid:
            return {"role": None}
        b = conn.execute("SELECT * FROM preproc_batches WHERE id=?", (bid,)).fetchone()
        return {"role": role, "batch": self._preproc_public(conn, b)}

    # ---- harvest check-in (creates a batch of totes) ---------------------- #
    def route_harvest(self, method, seg, conn):
        if seg == ["api", "harvest"] and method == "POST":
            d = self._body_json()
            site = (d.get("site") or "").strip().upper()
            species = (d.get("species") or "").strip().upper()
            date = (d.get("harvestDate") or today_iso()).strip()
            received_date = (d.get("receivedDate") or today_iso()).strip() or None
            count = int(num(d.get("toteCount")))
            total_kg = num(d.get("totalKg"))
            ph = numn(d.get("ph"))
            orp = numn(d.get("orp"))
            location = (d.get("location") or "").strip() or None
            stabilization_method = (d.get("stabilizationMethod") or "").strip() or "Citric acid"
            storage_unit = (d.get("storageUnit") or "").strip() or "Tote"
            grind = (d.get("grind") or "").strip().capitalize() or "Coarse"
            if grind not in ("Coarse", "Fine"):
                raise ApiError(400, "Grind must be Coarse or Fine")
            notes = (d.get("notes") or "").strip() or None
            if not site or not species or count <= 0:
                raise ApiError(400, "Site, species and a storage unit count > 0 are required")
            # Empty storage units consumed for this harvest come from a chosen
            # source (empty IBC-tote stock); "Burlap sack" isn't inventory-tracked,
            # so it has no consumable id and nothing is decremented for it.
            ibc_id = d.get("ibcConsumableId")
            ibc_row = None
            storage_source = (d.get("storageSourceLabel") or "").strip() or None
            if ibc_id:
                ibc_row = conn.execute("SELECT * FROM consumables WHERE id=?", (ibc_id,)).fetchone()
                if not ibc_row:
                    raise ApiError(400, "Unknown storage unit source")
                if ibc_row["on_hand"] < count:
                    raise ApiError(400, "Not enough %s on hand (%g < %d)"
                                   % (ibc_row["name"], ibc_row["on_hand"], count))
                storage_source = storage_source or ibc_row["name"]
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
                    "checkin_date,received_date,tote_number,volume_l,ph,orp,avg_weight_kg,location,"
                    "description,status,stabilization_method,storage_unit,storage_source,notes,created_at,grind)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'in_stock',?,?,?,?,?,?)",
                    (lot, site, species, int(date[:4]), date, received_date, n, 1000, ph, orp, avg, location,
                     "Fresh Stabilized Ground %s" % common, stabilization_method, storage_unit,
                     storage_source, notes, ts, grind))
                created.append(lot)
            if ibc_row:
                self._consume(conn, ibc_row["id"], -count, "Harvest check-in (storage unit fill)",
                              "%s-%s-%s" % (site, species, datestr))
            return {"created": created, "avgWeightKg": avg, "count": len(created),
                    "storageSource": storage_source}
        if seg == ["api", "harvest", "bulk"] and method == "POST":
            return self._harvest_bulk(conn)
        raise ApiError(404, "Unknown harvest endpoint")

    # One row per tote -- unlike the batch check-in above (one average weight
    # shared across a count of totes), each CSV row is its own tote with its
    # own weight, so rows sharing a site/species/harvest-date still need the
    # same tote-number continuation logic, tracked in-memory across the file
    # rather than in one COALESCE(MAX(...)) query per batch.
    def _harvest_bulk(self, conn):
        d = self._body_json()
        csv_text = d.get("csvText") or ""
        if not csv_text.strip():
            raise ApiError(400, "No CSV data received")
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)
        except Exception:
            raise ApiError(400, "Could not parse the CSV file")
        if not rows:
            raise ApiError(400, "The CSV has no data rows")
        if len(rows) > 500:
            raise ApiError(400, "Too many rows in one import (max 500)")
        required = {"site", "species", "harvestDate", "avgWeightKg"}
        have = {(h or "").strip() for h in (reader.fieldnames or [])}
        missing = required - have
        if missing:
            raise ApiError(400, "CSV is missing required column(s): %s" % ", ".join(sorted(missing)))
        species_codes = {r["code"] for r in conn.execute("SELECT code FROM species")}
        counters = {}  # (site,species,date) -> next tote_number
        ts = now_iso()
        created = []
        for i, row in enumerate(rows, start=2):  # row 1 is the header
            def cell(key):
                return ((row.get(key) or "").strip())
            site = cell("site").upper()
            species = cell("species").upper()
            date = cell("harvestDate")
            if not site or not species or not date:
                raise ApiError(400, "Row %d: site, species and harvestDate are required" % i)
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                raise ApiError(400, "Row %d: harvestDate '%s' must be YYYY-MM-DD" % (i, date))
            received_date = cell("receivedDate") or None
            if received_date:
                try:
                    datetime.date.fromisoformat(received_date)
                except ValueError:
                    raise ApiError(400, "Row %d: receivedDate '%s' must be YYYY-MM-DD" % (i, received_date))
            if species not in species_codes:
                raise ApiError(400, "Row %d: unknown species code '%s'" % (i, species))
            try:
                avg = float(cell("avgWeightKg"))
            except ValueError:
                avg = 0
            if avg <= 0:
                raise ApiError(400, "Row %d: avgWeightKg must be a number greater than 0" % i)
            ph = numn(cell("ph"))
            orp = numn(cell("orp"))
            location = cell("location") or None
            stabilization_method = cell("stabilizationMethod") or "Citric acid"
            storage_unit = cell("storageUnit") or "Tote"
            storage_source = cell("storageSource") or None
            grind = cell("grind").capitalize() or "Coarse"
            if grind not in ("Coarse", "Fine"):
                raise ApiError(400, "Row %d: grind '%s' must be Coarse or Fine" % (i, cell("grind")))
            notes = cell("notes") or None
            if not conn.execute("SELECT 1 FROM sites WHERE code=?", (site,)).fetchone():
                conn.execute("INSERT INTO sites (code,name) VALUES (?,?)", (site, site))
            if location:
                conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (location,))
            key = (site, species, date)
            if key not in counters:
                counters[key] = conn.execute(
                    "SELECT COALESCE(MAX(tote_number),0) n FROM tote_lots "
                    "WHERE site_code=? AND species_code=? AND checkin_date=?", key).fetchone()["n"]
            counters[key] += 1
            n = counters[key]
            datestr = date.replace("-", "")
            lot = "%s-%s-%s-%03d" % (site, species, datestr, n)
            sp = conn.execute("SELECT common FROM species WHERE code=?", (species,)).fetchone()
            common = sp["common"] if sp else species
            conn.execute(
                "INSERT INTO tote_lots (lot_number,site_code,species_code,harvest_year,"
                "checkin_date,received_date,tote_number,volume_l,ph,orp,avg_weight_kg,location,"
                "description,status,stabilization_method,storage_unit,storage_source,notes,created_at,grind)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'in_stock',?,?,?,?,?,?)",
                (lot, site, species, int(date[:4]), date, received_date, n, 1000, ph, orp, avg, location,
                 "Fresh Stabilized Ground %s" % common, stabilization_method, storage_unit,
                 storage_source, notes, ts, grind))
            created.append(lot)
        return {"created": created, "count": len(created)}

    # ---- consumables ------------------------------------------------------ #
    def _consumable_public(self, r):
        return dict(id=r["id"], name=r["name"], unit=r["unit"], onHand=r["on_hand"],
                    reorderLevel=r["reorder_level"], costPerUnit=r["cost_per_unit"],
                    location=r["location"], isContainer=bool(r["is_container"]),
                    litresEach=r["litres_each"], isSampleContainer=bool(r["is_sample_container"]),
                    itemNumber=r["item_number"], labelSku=r["label_sku_code"],
                    labelPackage=r["label_package"], isCipAgent=bool(r["is_cip_agent"]),
                    reagentType=r["reagent_type"], low=(r["on_hand"] <= r["reorder_level"]))

    def _clean_item_number(self, conn, raw, exclude_id=None):
        """Item # is normally auto-assigned (CAT-NNN, see assign_item_numbers); an admin may type their own. A typed value
        must be unique across all inventory items (409 otherwise); blank = none given."""
        item_number = (raw or "").strip() or None
        if item_number:
            dup = conn.execute("SELECT id FROM consumables WHERE item_number=? AND id IS NOT ?",
                               (item_number, exclude_id)).fetchone()
            if dup:
                raise ApiError(409, "Item # %s is already used by another item" % item_number)
        return item_number

    def route_consumables(self, method, seg, conn, user):
        if seg == ["api", "consumables"]:
            if method == "GET":
                rows = conn.execute("SELECT * FROM consumables ORDER BY name").fetchall()
                return {"consumables": [self._consumable_public(r) for r in rows]}
            if method == "POST":
                d = self._body_json()
                label_sku = (d.get("labelSku") or "").strip()
                label_package = (d.get("labelPackage") or "").strip()
                is_label = bool(label_sku or label_package)
                name = (d.get("name") or "").strip()
                if is_label:
                    # A finished-good label: one item per (SKU, package type),
                    # named automatically, deducted 1 per finished unit at
                    # finalize. Admin-only, like any new container type.
                    self._require_admin(user)
                    sku_row = conn.execute("SELECT name FROM fg_skus WHERE code=?", (label_sku,)).fetchone()
                    if not sku_row:
                        raise ApiError(400, "Choose a product SKU for this label")
                    if label_package not in packaging_container_litres_map(conn):
                        raise ApiError(400, "Choose a package type for this label")
                    if conn.execute("SELECT 1 FROM consumables WHERE label_sku_code=? AND label_package=?",
                                    (label_sku, label_package)).fetchone():
                        raise ApiError(409, "A label for that SKU and package type already exists")
                    name = name or "FG Label - %s - %s" % (sku_row["name"], label_package)
                if not name:
                    raise ApiError(400, "Name is required")
                is_container = bool(d.get("isContainer")) and not is_label
                # Anyone can add a general reagent (unchanged); only an
                # admin can introduce a new container type (Packaging output
                # unit or Sample Point vessel) -- editing/receiving/using an
                # existing one stays open to everyone below.
                if is_container:
                    self._require_admin(user)
                if conn.execute("SELECT 1 FROM consumables WHERE name=?", (name,)).fetchone():
                    raise ApiError(409, "That item already exists")
                item_number = self._clean_item_number(conn, d.get("itemNumber"))
                location = self._ensure_location(conn, d.get("location"))
                litres_each = numn(d.get("litresEach")) if is_container else None
                is_sample = 1 if (is_container and d.get("isSampleContainer")) else 0
                # Only a plain reagent can be a CIP cleaning agent (offered on
                # CIP log lines) -- not a container or a label.
                is_cip = 1 if (d.get("isCipAgent") and not is_container and not is_label) else 0
                if is_cip:
                    self._require_admin(user)
                rtype = (d.get("reagentType") or "").strip() or None
                if rtype and (rtype not in self.REAGENT_RUN_COLUMNS or is_container or is_label):
                    raise ApiError(400, "Reagent type must be one of: %s" % ", ".join(self.REAGENT_RUN_COLUMNS))
                cur = conn.execute(
                    "INSERT INTO consumables (name,unit,on_hand,reorder_level,cost_per_unit,location,"
                    "is_container,litres_each,is_sample_container,item_number,label_sku_code,label_package,"
                    "is_cip_agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, "ea" if is_label else (d.get("unit") or "unit").strip(), num(d.get("onHand")),
                     num(d.get("reorderLevel")), numn(d.get("costPerUnit")), location,
                     1 if is_container else 0, litres_each, is_sample, item_number,
                     label_sku if is_label else None, label_package if is_label else None, is_cip))
                if rtype:
                    conn.execute("UPDATE consumables SET reagent_type=? WHERE id=?", (rtype, cur.lastrowid))
                if not item_number:
                    assign_item_numbers(conn)
                return {"ok": True, "itemNumber": conn.execute("SELECT item_number FROM consumables WHERE id=?", (cur.lastrowid,)).fetchone()["item_number"]}
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "history" and method == "GET":
            cid = int(seg[2])
            if not conn.execute("SELECT 1 FROM consumables WHERE id=?", (cid,)).fetchone():
                raise ApiError(404, "Item not found")
            return {"history": self._consumable_history(conn, cid)}
        if seg == ["api", "consumables", "bulk"] and method == "POST":
            # Bulk inventory update (physical stocktake corrections, receiving
            # a large shipment, etc.) -- admin-only, matching the "bulk upload
            # ... and/or create new container types" restriction.
            self._require_admin(user)
            return self._consumables_bulk(conn, user)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "adjust" and method == "POST":
            cid = int(seg[2])
            c = conn.execute("SELECT * FROM consumables WHERE id=?", (cid,)).fetchone()
            if not c:
                raise ApiError(404, "Item not found")
            d = self._body_json()
            delta = num(d.get("delta"))
            if delta == 0:
                raise ApiError(400, "Adjustment delta must be non-zero")
            self._consume(conn, cid, delta, d.get("reason") or "Manual adjustment", d.get("ref"),
                          user["name"] if user else None)
            return {"onHand": conn.execute(
                "SELECT on_hand FROM consumables WHERE id=?", (cid,)).fetchone()["on_hand"]}
        if len(seg) == 3 and seg[2].isdigit() and method == "PUT":
            cid = int(seg[2])
            c = conn.execute("SELECT * FROM consumables WHERE id=?", (cid,)).fetchone()
            if not c:
                raise ApiError(404, "Item not found")
            d = self._body_json()
            location = self._ensure_location(conn, d["location"]) if "location" in d else c["location"]
            item_number = ((self._clean_item_number(conn, d["itemNumber"], cid) or c["item_number"])
                           if "itemNumber" in d else c["item_number"])
            if "reagentType" in d:
                rtype = (d["reagentType"] or "").strip() or None
                if rtype and rtype not in self.REAGENT_RUN_COLUMNS:
                    raise ApiError(400, "Reagent type must be one of: %s" % ", ".join(self.REAGENT_RUN_COLUMNS))
                conn.execute("UPDATE consumables SET reagent_type=? WHERE id=?", (rtype, cid))
            conn.execute(
                "UPDATE consumables SET reorder_level=?, cost_per_unit=?, location=?, litres_each=?,"
                " is_sample_container=?, item_number=? WHERE id=?",
                (num(d["reorderLevel"]) if "reorderLevel" in d else c["reorder_level"],
                 numn(d["costPerUnit"]) if "costPerUnit" in d else c["cost_per_unit"],
                 location,
                 (numn(d["litresEach"]) if "litresEach" in d else c["litres_each"]) if c["is_container"] else None,
                 (1 if d.get("isSampleContainer") else 0) if "isSampleContainer" in d
                 else c["is_sample_container"],
                 item_number,
                 cid))
            return {"ok": True}
        raise ApiError(404, "Unknown consumables endpoint")

    # ---- CIP (Clean In Place) log ------------------------------------------ #
    CIP_PURPOSES = ("Post-run", "Pre-run", "Changeover", "Scheduled", "Other")

    def _cip_public(self, conn, r):
        chems = [dict(id=c["id"], consumableId=c["consumable_id"], name=c["name"], unit=c["unit"],
                      qty=c["qty"], concentrationPct=c["concentration_pct"], tempC=c["temp_c"],
                      contactMin=c["contact_min"])
                 for c in conn.execute(
                     "SELECT cc.*, c.name, c.unit FROM cip_event_chemicals cc"
                     " JOIN consumables c ON c.id=cc.consumable_id WHERE cc.cip_event_id=? ORDER BY cc.id",
                     (r["id"],))]
        lot = None
        if r["run_id"]:
            run = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (r["run_id"],)).fetchone()
            lot = run["processing_lot"] if run else None
        return {"id": r["id"], "ref": r["cip_ref"], "startedAt": r["started_at"], "endedAt": r["ended_at"],
                "equipment": r["equipment"], "purpose": r["purpose"], "runId": r["run_id"],
                "processingLot": lot, "operators": r["operators"], "result": r["result"],
                "notes": r["notes"], "chemicals": chems, "createdBy": r["created_by"],
                "createdAt": r["created_at"], "updatedBy": r["updated_by"], "updatedAt": r["updated_at"]}

    def _cip_fields(self, conn, d):
        """Validate a CIP event body. Returns (fields dict, chemical lines,
        {consumable_id: total qty})."""
        def dt(key, required):
            raw = (d.get(key) or "").strip()
            if not raw:
                if required:
                    raise ApiError(400, "Enter the CIP start date and time")
                return None
            try:
                datetime.datetime.fromisoformat(raw)
            except ValueError:
                raise ApiError(400, "Enter a valid date and time for %s" % ("start" if key == "startedAt" else "end"))
            return raw
        started_at = dt("startedAt", True)
        ended_at = dt("endedAt", False)
        if ended_at and ended_at < started_at:
            raise ApiError(400, "The CIP end time can't be before its start time")
        equipment = (d.get("equipment") or "").strip()
        if not equipment:
            raise ApiError(400, "Enter the equipment that was cleaned")
        purpose = (d.get("purpose") or "").strip() or None
        if purpose and purpose not in self.CIP_PURPOSES:
            raise ApiError(400, "Choose a valid purpose")
        result = (d.get("result") or "").strip().lower() or None
        if result not in (None, "pass", "fail"):
            raise ApiError(400, "Result must be pass or fail")
        run_id = d.get("runId")
        run_id = int(run_id) if run_id not in (None, "") else None
        if run_id is not None and not conn.execute("SELECT 1 FROM production_runs WHERE id=?", (run_id,)).fetchone():
            raise ApiError(400, "Linked production run not found")
        lines, totals = [], {}
        for ln in (d.get("chemicals") or []):
            cid = ln.get("consumableId")
            row = conn.execute("SELECT * FROM consumables WHERE id=?", (cid,)).fetchone() if cid not in (None, "") else None
            if not row or not row["is_cip_agent"]:
                raise ApiError(400, "Each chemical line must be a CIP cleaning agent")
            qty = num(ln.get("qty"))
            if qty <= 0:
                raise ApiError(400, "Enter a quantity greater than 0 for %s" % row["name"])
            lines.append((row["id"], qty, numn(ln.get("concentrationPct")), numn(ln.get("tempC")),
                          numn(ln.get("contactMin"))))
            totals[row["id"]] = round(totals.get(row["id"], 0) + qty, 4)
        fields = {"started_at": started_at, "ended_at": ended_at, "equipment": equipment, "purpose": purpose,
                  "run_id": run_id, "operators": (d.get("operators") or "").strip() or None,
                  "result": result, "notes": (d.get("notes") or "").strip() or None}
        return fields, lines, totals

    def _cip_totals(self, conn, event_id):
        return {r["consumable_id"]: round(r["t"], 4) for r in conn.execute(
            "SELECT consumable_id, SUM(qty) t FROM cip_event_chemicals WHERE cip_event_id=?"
            " GROUP BY consumable_id", (event_id,))}

    def _apply_cip_stock(self, conn, ref, old_totals, new_totals, user_name, reason):
        """Deduct (or refund) the net change in each CIP agent's litres. Never
        raises on a shortage -- a cleaning that really happened must always be
        recorded -- so any item driven below zero is returned as a warning for
        the UI to show ("receive stock to correct")."""
        warnings = []
        for cid in set(old_totals) | set(new_totals):
            delta = round(new_totals.get(cid, 0) - old_totals.get(cid, 0), 4)
            if not delta:
                continue
            self._consume(conn, cid, -delta, reason, ref, user_name)
            row = conn.execute("SELECT name, unit, on_hand FROM consumables WHERE id=?", (cid,)).fetchone()
            if delta > 0 and row["on_hand"] < 0:
                warnings.append("%s is now below zero (%.1f %s) -- receive stock to correct"
                                % (row["name"], row["on_hand"], row["unit"]))
        return warnings

    def route_cip(self, method, seg, conn, user):
        uname = user["name"] if user else None
        if seg == ["api", "cip"]:
            if method == "GET":
                rows = conn.execute("SELECT * FROM cip_events ORDER BY started_at DESC, id DESC").fetchall()
                return {"events": [self._cip_public(conn, r) for r in rows]}
            if method == "POST":
                fields, lines, totals = self._cip_fields(conn, self._body_json())
                ts = now_iso()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO cip_events (cip_ref,started_at,ended_at,equipment,purpose,run_id,operators,"
                    "result,notes,created_by,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("TEMP-" + secrets.token_hex(6), fields["started_at"], fields["ended_at"], fields["equipment"],
                     fields["purpose"], fields["run_id"], fields["operators"],
                     fields["result"], fields["notes"], uname, ts))
                eid = cur.lastrowid
                ref = "CIP-%s-%03d" % (fields["started_at"][:10].replace("-", ""), eid)
                cur.execute("UPDATE cip_events SET cip_ref=? WHERE id=?", (ref, eid))
                for ln in lines:
                    cur.execute("INSERT INTO cip_event_chemicals (cip_event_id,consumable_id,qty,"
                                "concentration_pct,temp_c,contact_min) VALUES (?,?,?,?,?,?)", (eid, *ln))
                warnings = self._apply_cip_stock(conn, ref, {}, totals, uname, "CIP cleaning")
                row = conn.execute("SELECT * FROM cip_events WHERE id=?", (eid,)).fetchone()
                return {"event": self._cip_public(conn, row), "warnings": warnings}
        if len(seg) == 3 and seg[2].isdigit():
            eid = int(seg[2])
            row = conn.execute("SELECT * FROM cip_events WHERE id=?", (eid,)).fetchone()
            if not row:
                raise ApiError(404, "CIP entry not found")
            if method == "PUT":
                fields, lines, totals = self._cip_fields(conn, self._body_json())
                old_totals = self._cip_totals(conn, eid)
                sets = ", ".join("%s=?" % k for k in fields)
                conn.execute("UPDATE cip_events SET %s, updated_by=?, updated_at=? WHERE id=?" % sets,
                             (*fields.values(), uname, now_iso(), eid))
                conn.execute("DELETE FROM cip_event_chemicals WHERE cip_event_id=?", (eid,))
                for ln in lines:
                    conn.execute("INSERT INTO cip_event_chemicals (cip_event_id,consumable_id,qty,"
                                 "concentration_pct,temp_c,contact_min) VALUES (?,?,?,?,?,?)", (eid, *ln))
                warnings = self._apply_cip_stock(conn, row["cip_ref"], old_totals, totals, uname,
                                                 "CIP entry edited")
                row = conn.execute("SELECT * FROM cip_events WHERE id=?", (eid,)).fetchone()
                return {"event": self._cip_public(conn, row), "warnings": warnings}
            if method == "DELETE":
                self._require_admin(user)
                self._apply_cip_stock(conn, row["cip_ref"], self._cip_totals(conn, eid), {}, uname,
                                      "CIP entry deleted")
                conn.execute("DELETE FROM cip_events WHERE id=?", (eid,))
                return {"ok": True}
        raise ApiError(404, "Unknown CIP endpoint")

    def _consumables_bulk(self, conn, user):
        d = self._body_json()
        csv_text = d.get("csvText") or ""
        if not csv_text.strip():
            raise ApiError(400, "No CSV data received")
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)
        except Exception:
            raise ApiError(400, "Could not parse the CSV file")
        if not rows:
            raise ApiError(400, "The CSV has no data rows")
        if len(rows) > 500:
            raise ApiError(400, "Too many rows in one import (max 500)")
        have = {(h or "").strip() for h in (reader.fieldnames or [])}
        if "name" not in have or "onHand" not in have:
            raise ApiError(400, "CSV is missing required column(s): name, onHand")
        updated = 0
        for i, row in enumerate(rows, start=2):  # row 1 is the header
            name = (row.get("name") or "").strip()
            if not name:
                raise ApiError(400, "Row %d: name is required" % i)
            c = conn.execute("SELECT * FROM consumables WHERE name=?", (name,)).fetchone()
            if not c:
                raise ApiError(400, "Row %d: unknown item '%s'" % (i, name))
            try:
                new_on_hand = float((row.get("onHand") or "").strip())
            except ValueError:
                raise ApiError(400, "Row %d: onHand must be a number" % i)
            if new_on_hand < 0:
                raise ApiError(400, "Row %d: onHand cannot be negative" % i)
            delta = new_on_hand - c["on_hand"]
            if delta:
                self._consume(conn, c["id"], delta, (row.get("reason") or "").strip() or "Bulk import",
                              "Bulk import", user["name"] if user else None)
                updated += 1
        return {"updated": updated}

    def _consume(self, conn, consumable_id, delta, reason, ref, user_name=None):
        conn.execute("UPDATE consumables SET on_hand = on_hand + ? WHERE id=?", (delta, consumable_id))
        conn.execute("INSERT INTO consumable_txns (consumable_id,delta,reason,ref,user_name,created_at)"
                     " VALUES (?,?,?,?,?,?)", (consumable_id, delta, reason, ref, user_name, now_iso()))

    def _consumable_by_name(self, conn, name):
        return conn.execute("SELECT * FROM consumables WHERE name=?", (name,)).fetchone()

    def _consumable_history(self, conn, cid):
        return [{"delta": r["delta"], "reason": r["reason"], "ref": r["ref"],
                 "userName": r["user_name"], "createdAt": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM consumable_txns WHERE consumable_id=? ORDER BY created_at DESC, id DESC", (cid,))]

    def _adjust_container_stock(self, conn, name, delta, reason, ref, user_name=None):
        """Consume/refund a container consumable's on-hand stock by name, for
        the live Sample Point deduction (add a row -> -qty, delete it -> +qty
        back, edit qty/container -> the difference) and for the Packaging
        table's own net-change commit (see _commit_packaging_stock). A no-op
        if name is blank or doesn't match any consumable (e.g. a Sample Point
        row whose container hasn't been picked yet). Never blocks on a shortage:
        a run proceeds and the item's on-hand simply goes negative (flagged LOW)."""
        if not name or not delta:
            return
        row = self._consumable_by_name(conn, name)
        if not row:
            return
        self._consume(conn, row["id"], delta, reason, ref, user_name)

    def _commit_packaging_stock(self, conn, run_id, user_name=None, sku=None):
        """Nets the Packaging table's container/qty edits since the last
        commit into at most one consumable_txns line per container -- adding
        rows, bumping a qty a few times, then settling on a final number all
        collapse into a single "Packaging saved" entry reflecting the total
        change; a container whose total qty is unchanged since the last
        commit is skipped entirely (no entry, no on-hand touch).

        The matching finished-good label (item mapped to the run's SKU + that
        container) is consumed with it, one per container, using the same
        net-change idea (see _commit_label_stock). `sku` overrides the run's
        stored SKU -- finalize passes the SKU it is about to save."""
        lot_row = conn.execute("SELECT processing_lot, sku_code FROM production_runs WHERE id=?", (run_id,)).fetchone()
        lot = lot_row["processing_lot"] if lot_row else None
        sku = sku or (lot_row["sku_code"] if lot_row else None)
        current = {r["container_unit"]: (r["total"] or 0) for r in conn.execute(
            "SELECT container_unit, SUM(qty) total FROM run_packaging_entries"
            " WHERE run_id=? AND container_unit IS NOT NULL GROUP BY container_unit", (run_id,))}
        committed = {r["container_unit"]: r["committed_qty"] for r in conn.execute(
            "SELECT container_unit, committed_qty FROM run_packaging_commits WHERE run_id=?", (run_id,))}
        for unit in set(current) | set(committed):
            new_total = current.get(unit, 0)
            old_total = committed.get(unit, 0)
            if new_total == old_total:
                continue
            self._adjust_container_stock(conn, unit, old_total - new_total, "Packaging saved (net change)",
                                          lot, user_name)
            if unit in committed:
                conn.execute("UPDATE run_packaging_commits SET committed_qty=? WHERE run_id=? AND container_unit=?",
                             (new_total, run_id, unit))
            else:
                conn.execute("INSERT INTO run_packaging_commits (run_id,container_unit,committed_qty)"
                             " VALUES (?,?,?)", (run_id, unit, new_total))
        self._commit_label_stock(conn, run_id, lot, sku, current, user_name)

    def _commit_label_stock(self, conn, run_id, lot, sku, container_totals, user_name=None):
        """One finished-good label per container consumed: nets the labels the
        run should now have used (container totals mapped through the run's SKU
        to label items) against run_label_commits, as one ledger line per label
        item. No label item for a SKU + container means it simply isn't tracked.
        A shortage never blocks (same as before), it shows as low/negative."""
        want = {}
        if sku:
            for unit, qty in container_totals.items():
                row = conn.execute("SELECT id FROM consumables WHERE label_sku_code=? AND label_package=?",
                                   (sku, unit)).fetchone()
                if row and qty:
                    want[row["id"]] = want.get(row["id"], 0) + qty
        have = {r["consumable_id"]: r["committed_qty"] for r in conn.execute(
            "SELECT consumable_id, committed_qty FROM run_label_commits WHERE run_id=?", (run_id,))}
        for cid in set(want) | set(have):
            new_qty, old_qty = want.get(cid, 0), have.get(cid, 0)
            if new_qty == old_qty:
                continue
            self._consume(conn, cid, old_qty - new_qty, "FG labels (packaging saved)", lot, user_name)
            conn.execute("INSERT INTO run_label_commits (run_id,consumable_id,committed_qty) VALUES (?,?,?)"
                         " ON CONFLICT(run_id,consumable_id) DO UPDATE SET committed_qty=excluded.committed_qty",
                         (run_id, cid, new_qty))

    # Reagents deducted from the Dilution & Preservation entries: consumable
    # name -> the production_runs column holding that run's running total kg.
    REAGENT_RUN_COLUMNS = {
        "Citric Acid": "citric_kg",
        "Potassium Sorbate": "sorbate_kg",
        "Sodium Benzoate": "nabenzoate_kg",
    }

    REAGENT_ITEM_COLUMNS = {"Citric Acid": "dilution_citric_item_id", "Potassium Sorbate": "dilution_ksorbate_item_id",
                            "Sodium Benzoate": "dilution_nabenzoate_item_id"}

    def _reagent_items(self, conn, rtype):
        """Every inventory item of this reagent type; the one named exactly like the type (the original item) first."""
        return conn.execute("SELECT * FROM consumables WHERE reagent_type=? ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, name",
                            (rtype, rtype)).fetchall()

    def _reagent_item(self, conn, rtype, selected_id=None):
        """The item a run draws a reagent from: the one chosen in the production log, else the type's default (the
        item named like the type, then the first of that type), else a legacy untyped item named like the type."""
        if selected_id:
            r = conn.execute("SELECT * FROM consumables WHERE id=? AND reagent_type=?", (selected_id, rtype)).fetchone()
            if r:
                return r
        items = self._reagent_items(conn, rtype)
        return items[0] if items else self._consumable_by_name(conn, rtype)

    def route_reagents(self, query, conn):
        """GET /api/reagents[?runId=] -- every reagent type with its inventory items (and, for a run, what that run has
        already deducted and from which item) -- feeds the production log's reagent pickers and over-stock notes."""
        types = []
        for t in self.REAGENT_RUN_COLUMNS:
            items = self._reagent_items(conn, t)
            if not items:
                legacy = self._consumable_by_name(conn, t)
                items = [legacy] if legacy else []
            types.append({"type": t, "defaultItemId": items[0]["id"] if items else None,
                          "items": [{"id": i["id"], "name": i["name"], "itemNumber": i["item_number"], "unit": i["unit"],
                                     "onHand": i["on_hand"]} for i in items]})
        commits = {}
        rid = query.get("runId", [""])[0]
        if rid.isdigit():
            for rc in conn.execute("SELECT * FROM run_reagent_commits WHERE run_id=?", (int(rid),)):
                item_id = rc["consumable_id"]
                if not item_id:
                    legacy = self._consumable_by_name(conn, rc["reagent"])
                    item_id = legacy["id"] if legacy else None
                commits[rc["reagent"]] = {"itemId": item_id, "kg": rc["committed_kg"]}
        return {"types": types, "commits": commits}

    @staticmethod
    def _dilution_reagent_kg(run):
        """kg of each reagent the run's Dilution & Preservation entries imply:
        citric straight from its field; sorbate/benzoate from the volume of
        stock solution added x its w/v concentration (same math the UI shows
        as "added (kg)")."""
        def stock_kg(added_l, stock_pct):
            return round((added_l or 0) * (stock_pct or 0) / 100.0, 4)
        return {
            "Citric Acid": round(run["dilution_citric_kg"] or 0, 4),
            "Potassium Sorbate": stock_kg(run["dilution_ksorbate_added_l"], run["dilution_ksorbate_stock_pct"]),
            "Sodium Benzoate": stock_kg(run["dilution_nabenzoate_added_l"], run["dilution_nabenzoate_stock_pct"]),
        }

    def _commit_reagent_usage(self, conn, run_id, user_name=None):
        """Nets the Dilution & Preservation reagent entries since the last
        commit into at most one consumable_txns line per reagent, same model
        as _commit_packaging_stock: unchanged since the last commit = no
        entry at all. The delta is also added to the run's running kg total
        (production_runs.citric_kg / sorbate_kg / nabenzoate_kg) so anything
        entered via Edit run keeps counting. A reagent with no matching
        consumable row is skipped (and not recorded as committed)."""
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return
        lot = run["processing_lot"]
        current = self._dilution_reagent_kg(run)
        # citric acid added in additional dilution passes' pH balancing counts too
        extra_citric = conn.execute("SELECT COALESCE(SUM(citric_kg),0) c FROM run_dilution_passes WHERE run_id=?",
                                    (run_id,)).fetchone()["c"]
        current["Citric Acid"] = round(current["Citric Acid"] + extra_citric, 4)
        committed = {r["reagent"]: (r["committed_kg"], r["consumable_id"]) for r in conn.execute(
            "SELECT reagent, committed_kg, consumable_id FROM run_reagent_commits WHERE run_id=?", (run_id,))}
        for name, col in self.REAGENT_RUN_COLUMNS.items():
            item = self._reagent_item(conn, name, run[self.REAGENT_ITEM_COLUMNS[name]])
            if not item:
                continue
            prev_kg, prev_id = committed.get(name, (0, None))
            if prev_kg and not prev_id:                  # committed before items were selectable: the item named like the type
                legacy = self._consumable_by_name(conn, name)
                prev_id = legacy["id"] if legacy else item["id"]
            total_delta = round(current[name] - (prev_kg or 0), 4)
            base_kg = prev_kg or 0
            if base_kg and prev_id and prev_id != item["id"]:
                # a different item was chosen for this reagent: give back what the old item supplied, take it all from the new one
                self._consume(conn, prev_id, base_kg, "Dilution & Preservation: %s item changed (refund)" % name, lot, user_name)
                base_kg = 0
            delta = round(current[name] - base_kg, 4)
            if delta:
                # a shortage never blocks the run -- on-hand just goes negative
                self._consume(conn, item["id"], -delta, "Dilution & Preservation saved (net change)", lot, user_name)
            if delta or total_delta or prev_id != item["id"] or name not in committed:
                conn.execute(
                    "INSERT INTO run_reagent_commits (run_id,reagent,committed_kg,consumable_id) VALUES (?,?,?,?)"
                    " ON CONFLICT(run_id, reagent) DO UPDATE SET committed_kg=excluded.committed_kg, consumable_id=excluded.consumable_id",
                    (run_id, name, current[name], item["id"]))
            if total_delta:
                conn.execute("UPDATE production_runs SET %s = ROUND(COALESCE(%s, 0) + ?, 4) WHERE id=?" % (col, col),
                             (total_delta, run_id))

    # Stages that actually contain a "QC Check" container (Pasteurization and
    # Dilution & Preservation only have Process Check/Sample Point boxes).
    QC_CHECK_STAGES = {"homogenization", "extraction", "separation", "packaging"}

    def _log_qc_field_changes(self, conn, run_id, stage, updates, user_name=None):
        """After save_stage's column UPDATE, diffs the QC-Check subset of
        `updates` (col -> new value, as just applied to production_runs)
        against qc_field_log and upserts only the ones that changed -- a
        reading resubmitted unchanged touches nothing, stamping user_name/now
        only on a real change. A field cleared back to None deletes its log
        row (no value -> nothing to attribute -> matches the read side's
        "not yet recorded" state)."""
        if stage not in self.QC_CHECK_STAGES:
            return
        cols = {f[0] for f in QC_FIELD_REGISTRY if f[1] == stage}
        touched = {c: v for c, v in updates.items() if c in cols}
        if not touched:
            return
        existing = {r["field_id"]: r["value"] for r in conn.execute(
            "SELECT field_id, value FROM qc_field_log WHERE run_id=? AND field_id IN (%s)"
            % ",".join("?" * len(touched)), (run_id, *touched.keys()))}
        now = now_iso()
        for field_id, val in touched.items():
            if val is None:
                if field_id in existing:
                    conn.execute("DELETE FROM qc_field_log WHERE run_id=? AND field_id=?", (run_id, field_id))
                continue
            if field_id in existing and existing[field_id] == val:
                continue
            conn.execute(
                "INSERT INTO qc_field_log (run_id, field_id, value, recorded_by, recorded_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(run_id, field_id) DO UPDATE SET"
                " value=excluded.value, recorded_by=excluded.recorded_by, recorded_at=excluded.recorded_at",
                (run_id, field_id, val, user_name, now))

    def _qc_checks_public(self, conn, run_id):
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ApiError(404, "Production run not found")
        audit = {r["field_id"]: r for r in conn.execute(
            "SELECT * FROM qc_field_log WHERE run_id=?", (run_id,))}
        out = []
        for field_id, stage, stage_label, subtitle, label, unit in QC_FIELD_REGISTRY:
            a = audit.get(field_id)
            out.append({
                "fieldId": field_id, "stage": stage, "stageLabel": stage_label,
                "subtitle": subtitle, "label": label, "unit": unit,
                "value": run[field_id],
                "recordedBy": a["recorded_by"] if a else None,
                "recordedAt": a["recorded_at"] if a else None,
            })
        return out

    # ---- production ------------------------------------------------------- #
    # Fields a run edit may touch: (db column, json key, label, kind). The reagent amounts are NOT here: they are the Dilution &
    # Preservation entries (edited under an amendment) and their stock is committed by _commit_reagent_usage, so a second path that
    # changed the run's totals and moved stock by hand would leave the ledger, run_reagent_commits and the run out of step.
    RUN_EDIT_FIELDS = [
        ("run_date", "runDate", "Run date", "text"),
        ("exclude_from_stats", "excludeFromStats", "Exclude from yield & usage analysis (1 = excluded)", "num"),
        ("exclude_reason", "excludeReason", "Exclusion reason", "text"),
        ("location", "location", "Production Location", "text"),
        ("notes", "notes", "Notes", "text"),
        ("operators", "operators", "Operators", "text"),
    ]

    # Process-stage columns a stage save may touch: (db column, json key, kind)
    STAGE_FIELDS = {
        "homogenization": [
            ("homog_started_at", "startedAt", "text"),
            ("homog_rinsing_water_l", "rinsingWaterL", "num"),
            ("homog_slurry_l", "slurryL", "num"),
            ("homog_wet_solids_wt_g", "wetSolidsWtG", "num"),
            ("homog_liquid_wt_g", "liquidWtG", "num"),
            ("homog_pct_wet_solids", "pctWetSolids", "num"),
            ("homog_target_pct_wet_solids", "targetPctWetSolids", "num"),
            ("homog_dilution_water_target_l", "dilutionWaterTargetL", "num"),
            ("homog_dilution_water_l", "dilutionWaterL", "num"),
            ("homog_post_dilution_tank_l", "postDilutionTankL", "num"),
            ("homog_lot_pct_solids", "lotPctSolids", "num"),
            ("homog_qc_ph", "qcPh", "num"),
            ("homog_sample_collected_at", "sampleCollectedAt", "text"),
            ("homog_tds_pct", "tdsPct", "num"),
            ("homog_brix_pct", "brixPct", "num"),
            ("homog_mannitol_pct", "mannitolPct", "num"),
            ("homog_ts_liquid_pct", "tsLiquidPct", "num"),
            ("homog_rho_liquid_g_ml", "rhoLiquidGMl", "num"),
            ("homog_ts_slurry_pct", "tsSlurryPct", "num"),
            ("homog_rho_slurry_g_ml", "rhoSlurryGMl", "num"),
            ("homog_ts_solids_pct", "tsSolidsPct", "num"),
            ("homog_solids_loading_pct", "solidsLoadingPct", "num"),
            # homog_output_l, homog_initial_ph, homog_citric_kg, homog_final_ph
            # and the fixed sample-checklist columns all stay (additive-only)
            # but are no longer collected -- "Output (L)" and the whole
            # "Process Check - pH control" box were removed from the
            # Homogenization Output section; the checklist was replaced by
            # the repeatable run_sample_points table.
        ],
        "extraction": [
            ("extraction_started_at", "startedAt", "text"),
            ("extraction_amplitude_pct", "amplitudePct", "num"),
            ("extraction_flowrate_lpm", "flowrateLpm", "num"),
            ("extraction_pressure_psi", "pressurePsi", "num"),
            ("extraction_starting_power_w", "startingPowerW", "num"),
            ("extraction_qc_ph", "qcPh", "num"),
            ("extraction_tds_pct", "tdsPct", "num"),
            ("extraction_brix_pct", "brixPct", "num"),
            ("extraction_mannitol_pct", "mannitolPct", "num"),
            ("extraction_ts_liquid_pct", "tsLiquidPct", "num"),
            ("extraction_rho_liquid_g_ml", "rhoLiquidGMl", "num"),
            ("extraction_ts_slurry_pct", "tsSlurryPct", "num"),
            ("extraction_rho_slurry_g_ml", "rhoSlurryGMl", "num"),
            ("extraction_ts_solids_pct", "tsSolidsPct", "num"),
        ],
        "separation": [
            ("separation_started_at", "startedAt", "text"),
            ("separation_flowrate_lpm", "flowrateLpm", "num"),
            ("separation_mesh_micron", "meshMicron", "num"),
            ("separation_wet_solids_wt_kg", "wetSolidsWtKg", "num"),
            ("separation_pct_moisture", "pctMoisture", "num"),
            ("separation_pct_moisture_screw", "pctMoistureScrew", "num"),
            ("separation_liquid_qc_ph", "liquidQcPh", "num"),
            ("separation_liquid_tds_pct", "liquidTdsPct", "num"),
            ("separation_liquid_brix_pct", "liquidBrixPct", "num"),
            ("separation_liquid_mannitol_pct", "liquidMannitolPct", "num"),
            ("separation_liquid_ts_liquid_pct", "liquidTsLiquidPct", "num"),
            ("separation_liquid_rho_liquid_g_ml", "liquidRhoLiquidGMl", "num"),
            ("separation_solids_sample_collected_at", "solidsSampleCollectedAt", "text"),
            # separation_water_addition_l column stays (additive-only) but is
            # no longer collected -- "Water addition (L)" was removed.
        ],
        "pasteurization": [
            ("pasteurization_started_at", "startedAt", "text"),
            ("pasteurization_product_setpoint_c", "productSetpointC", "num"),
            ("pasteurization_boiler_setpoint_c", "boilerSetpointC", "num"),
            ("pasteurization_post_sample_collected_at", "postSampleCollectedAt", "text"),
            ("pasteurization_tds_pct", "tdsPct", "num"),      # now the Separation filtrate TDS used by the plan
            ("pasteurization_tank5a_l", "tank5aL", "num"),
            ("pasteurization_tank5b_l", "tank5bL", "num"),
            ("pasteurization_receiving", "receivingTanks", "text"),
            ("pasteurization_tank6a_start_l", "tank6aStartL", "num"),
            ("pasteurization_tank6b_start_l", "tank6bStartL", "num"),
            ("pasteurization_max_transfer_l", "maxTransferL", "num"),
            ("pasteurization_transfer_rec_l", "recommendedTransferL", "num"),
            ("pasteurization_water_rec_l", "recommendedWaterL", "num"),
            # pasteurization_pre_sample_collected_at and pasteurization_total_volume_l
            # columns stay (additive-only) but are no longer collected -- the
            # "Pre-pasteurization microbial check" box and "Total volume (L)"
            # field were both removed.
        ],
        "dilution": [
            ("dilution_fill_level_tank_6ab_l", "fillLevelTank6abL", "num"),
            ("dilution_measured_ph", "measuredPh", "num"),
            ("dilution_citric_kg", "citricKg", "num"),
            ("dilution_citric_item_id", "citricItemId", "num"),
            ("dilution_ksorbate_item_id", "ksorbateItemId", "num"),
            ("dilution_nabenzoate_item_id", "nabenzoateItemId", "num"),
            ("dilution_ksorbate_stock_pct", "ksorbateStockPct", "num"),
            ("dilution_ksorbate_added_l", "ksorbateAddedL", "num"),
            ("dilution_nabenzoate_stock_pct", "nabenzoateStockPct", "num"),
            ("dilution_nabenzoate_added_l", "nabenzoateAddedL", "num"),
            ("dilution_product_transferred_l", "productTransferredL", "num"),
            ("dilution_water_added_l", "waterAddedL", "num"),
            ("dilution_tank6a_final_l", "tank6aFinalL", "num"),
            ("dilution_tank6b_final_l", "tank6bFinalL", "num"),
            ("dilution_ksorbate_added_l_6a", "ksorbateAddedL6a", "num"),
            ("dilution_ksorbate_added_l_6b", "ksorbateAddedL6b", "num"),
            ("dilution_nabenzoate_added_l_6a", "nabenzoateAddedL6a", "num"),
            ("dilution_nabenzoate_added_l_6b", "nabenzoateAddedL6b", "num"),
            ("dilution_final_variance_pct", "finalVariancePct", "num"),
        ],
        "packaging": [
            # packaging_started_at removed -- see the schema comment above.
            ("packaging_packaged_at", "packagedAt", "text"),
            # Quality Check (subtitle "LKE characterization"), Liquid-only fields.
            ("packaging_qc_ph", "qcPh", "num"),
            ("packaging_tds_pct", "tdsPct", "num"),
            ("packaging_brix_pct", "brixPct", "num"),
            ("packaging_mannitol_pct", "mannitolPct", "num"),
            ("packaging_ts_liquid_pct", "tsLiquidPct", "num"),
            ("packaging_rho_liquid_g_ml", "rhoLiquidGMl", "num"),
            ("packaging_sample_collected_at", "sampleCollectedAt", "text"),
        ],
    }

    # Feedstock (run_inputs) characterization fields: (db column, json key, kind)
    INPUT_FIELDS = [
        ("loaded_at", "loadedAt", "text"),
        ("ph", "ph", "num"),
        ("ph_measured_at", "phMeasuredAt", "text"),
        ("orp", "orp", "num"),
        ("orp_range", "orpRange", "text"),
        ("weight_kg", "weightKg", "num"),
        ("volume_l", "volumeL", "num"),
        ("density_kg_l", "densityKgL", "num"),
        ("odour", "odour", "text"),
        ("odour_other", "odourOther", "text"),
        ("odour_intensity", "odourIntensity", "text"),
        ("decision", "decision", "text"),
        ("rejection_reason", "rejectionReason", "text"),
        ("notes", "notes", "text"),
    ]

    # Dilution & Preservation fields: (db column, json key, kind)
    DILUTION_FIELDS = [
        ("tank", "tank", "text"),
        ("volume_initial_l", "volumeInitialL", "num"),
        ("water_required_l", "waterRequiredL", "num"),
        ("volume_final_l", "volumeFinalL", "num"),
        ("sorbate_required_kg", "sorbateRequiredKg", "num"),
        ("benzoate_required_kg", "benzoateRequiredKg", "num"),
        ("preservatives_added", "preservativesAdded", "bool"),
        ("preservatives_added_at", "preservativesAddedAt", "text"),
        ("citric_kg", "citricKg", "num"),
        ("samples_taken", "samplesTaken", "bool"),
        ("samples_taken_at", "samplesTakenAt", "text"),
        ("notes", "notes", "text"),
    ]

    SAMPLE_POINT_FIELDS = [
        ("type", "type", "text"),
        ("description", "description", "text"),
        ("qty", "qty", "int"),
        ("container", "container", "text"),
    ]

    # Packaging table entries: (db column, json key, kind)
    PACKAGING_ENTRY_FIELDS = [
        ("container_unit", "containerUnit", "text"),
        ("qty", "qty", "num"),
    ]

    def _run_full(self, conn, r):
        """A completed run with its whole production log attached -- the
        Production list payload, and (minus volatile/non-log keys, see
        _release_snapshot_hash) the snapshot a release sign-off attests to."""
        d = run_public(r)
        d["inputTotes"] = [row["lot_number"] for row in conn.execute(
            "SELECT t.lot_number FROM run_inputs ri JOIN tote_lots t ON t.id=ri.tote_lot_id "
            "WHERE ri.run_id=? ORDER BY t.lot_number", (r["id"],))]
        d["fgLots"] = [fg_public(row) for row in conn.execute(
            "SELECT * FROM fg_lots WHERE run_id=? ORDER BY package_size", (r["id"],))]
        d["edits"] = self._run_edits(conn, r["id"])
        d["attachments"] = self._attachments(conn, r["id"])
        d["qcSummary"] = {
            "recorded": sum(1 for f in QC_FIELD_REGISTRY if r[f[0]] is not None),
            "total": len(QC_FIELD_REGISTRY),
        }
        d["inputs"] = self._run_inputs_public(conn, r["id"])
        d["dilutions"] = self._dilutions_public(conn, r["id"])
        d["dilutionPasses"] = self._dilution_passes_public(conn, r["id"])
        d["samplePoints"] = self._sample_points_public(conn, r["id"])
        d["packagingEntries"] = self._packaging_entries_public(conn, r["id"])
        d["release"] = {"state": r["release_state"], "label": RELEASE_LABELS.get(r["release_state"], "—"),
                        "finalizedBy": r["finalized_by"]}
        am = self._open_amendment(conn, r["id"])
        d["amendment"] = self._amendment_public(am) if am else None
        d["progress"] = self._run_progress(conn, r)
        d["revisions"] = self._run_revisions_public(conn, r)
        d["revision"] = d["revisions"][-1]["rev"] if d["revisions"] else 1
        return d

    # ---- production log: required fields + section progress ---------------- #
    def _run_progress(self, conn, r):
        """Per-section completeness against the required-field registry: how
        many required fields have a value and which are still missing. A
        section is `done` (green dot) only when none are missing."""
        rid = r["id"]
        stages = run_public(r)["stages"]
        sections = []
        for sec in PROGRESS_SECTIONS:
            items = []   # (label, filled?)
            if sec["key"] == "feedstock":
                if r["status"] == "draft":
                    try:
                        dd = json.loads(r["draft_data"]) if r["draft_data"] else {}
                    except ValueError:
                        dd = {}
                    tote_ids = [int(x) for x in (dd.get("toteIds") or [])]
                    rows = {x["toteLotId"]: x for x in self._run_inputs_public(conn, rid)}
                else:
                    rows = {x["toteLotId"]: x for x in self._run_inputs_public(conn, rid)
                            if x["decision"] != "rejected"}
                    tote_ids = list(rows)
                if not tote_ids:
                    items.append(("At least one tote", False))
                for tid in tote_ids:
                    row = rows.get(tid) or {}
                    lot = conn.execute("SELECT lot_number FROM tote_lots WHERE id=?", (tid,)).fetchone()
                    name = lot["lot_number"] if lot else str(tid)
                    for key, label in REQUIRED_FEEDSTOCK:
                        items.append(("%s: %s" % (name, label), _has_value(row.get(key))))
            else:
                for entry in sec["fields"]:
                    stage, key, label = entry[:3]
                    if len(entry) > 3 and not entry[3](stages):
                        continue          # conditional field that doesn't apply to this run
                    items.append((label, _has_value(stages[stage].get(key))))
                if sec["key"] == "dilution":
                    for ps in conn.execute("SELECT * FROM run_dilution_passes WHERE run_id=? ORDER BY pass_no", (rid,)):
                        recv = ps["receiving"] or ""
                        ua, ub = recv in ("6A", "6AB"), recv in ("6B", "6AB")
                        n = ps["pass_no"]
                        parts = [("Tank 5A level (L)", ps["tank5a_l"]), ("Tank 5B level (L)", ps["tank5b_l"]),
                                 ("Receiving tanks", recv), ("Product transferred (L)", ps["product_transferred_l"]),
                                 ("Dilution water added (L)", ps["water_added_l"]),
                                 ("Measured pH", ps["measured_ph"]), ("Citric acid added (kg)", ps["citric_kg"])]
                        for on, t in ((ua, "6A"), (ub, "6B")):
                            if on:
                                lc = t.lower()
                                parts += [("Tank %s level before transfer (L)" % t, ps["tank%s_start_l" % lc]),
                                          ("Final level, Tank %s (L)" % t, ps["tank%s_final_l" % lc]),
                                          ("Ksorbate added, Tank %s (L)" % t, ps["ksorbate_added_l_%s" % lc]),
                                          ("Sodium benzoate added, Tank %s (L)" % t, ps["nabenzoate_added_l_%s" % lc])]
                        for label, val in parts:
                            items.append(("Pass %d: %s" % (n, label), _has_value(val)))
                if sec.get("sample_rows"):
                    n = conn.execute("SELECT COUNT(*) c FROM run_sample_points WHERE run_id=? AND stage='packaging'",
                                     (rid,)).fetchone()["c"]
                    items.append((sec["sample_rows"], n > 0))
                if sec.get("packaging_entries"):
                    n = conn.execute("SELECT COUNT(*) c FROM run_packaging_entries WHERE run_id=? AND COALESCE(qty,0)>0",
                                     (rid,)).fetchone()["c"]
                    items.append((sec["packaging_entries"], n > 0))
            missing = [l for l, ok in items if not ok]
            filled = len(items) - len(missing)
            sections.append({"key": sec["key"], "label": sec["label"], "total": len(items), "filled": filled,
                             "missing": missing, "done": bool(items) and not missing, "started": filled > 0})
        return {"sections": sections,
                "requiredTotal": sum(x["total"] for x in sections),
                "requiredFilled": sum(x["filled"] for x in sections),
                "complete": all(x["done"] for x in sections)}

    def _required_problems(self, conn, r):
        """'' when every required field has a value, else a readable list."""
        parts = []
        init = []
        if not r["sku_code"]:
            init.append("Product SKU")
        if not r["run_date"]:
            init.append("Run date")
        if not (r["location"] or "").strip():
            init.append("Production location")
        if not (r["operators"] or "").strip():
            init.append("Operators")
        if init:
            parts.append("Initiation: " + ", ".join(init))
        for sec in self._run_progress(conn, r)["sections"]:
            if not sec["missing"]:
                continue
            if sec["key"] == "feedstock":
                # "<tote lot>: <field>" items -> one line per tote
                per = {}
                for m in sec["missing"]:
                    lot, _sep, field = m.partition(": ")
                    per.setdefault(lot, []).append(field or lot)
                for lot, fields in per.items():
                    parts.append("Feedstock %s: %s" % (lot, ", ".join(fields)))
            else:
                parts.append("%s: %s" % (sec["label"], ", ".join(sec["missing"])))
        return ("\n• " + "\n• ".join(parts)) if parts else ""

    # ---- production log: revision tracker ----------------------------------- #
    def _item_label(self, section, item):
        if section == "inputs":
            return "Feedstock %s" % (item.get("toteLot") or "#%s" % item["id"])
        if section == "dilutionPasses":
            return "Dilution pass %s" % (item.get("passNo") or "#%s" % item["id"])
        if section == "dilutions":
            return "Dilution tank %s" % (item.get("tank") or "#%s" % item["id"])
        if section == "samplePoints":
            return "Sample point (%s)" % (" ".join(x for x in (item.get("stage"), item.get("type"), item.get("description")) if x) or "#%s" % item["id"])
        if section == "packagingEntries":
            return "Packaging entry %s" % (item.get("containerUnit") or "#%s" % item["id"])
        if section == "attachments":
            return "Document %s" % (item.get("filename") or "#%s" % item["id"])
        return "%s #%s" % (humanize_key(section), item["id"])

    def _flatten_snapshot(self, d):
        flat, items = {}, {}

        def walk(path, v, item):
            if isinstance(v, dict):
                for k, x in v.items():
                    walk(path + (k,), x, item)
            elif isinstance(v, list):
                if all(isinstance(i, dict) and "id" in i for i in v):
                    for i in v:
                        ik = (path[0], i["id"])
                        items[ik] = self._item_label(path[0], i)
                        for k, x in i.items():
                            if k != "id":
                                walk(path + ("#%s" % i["id"], k), x, ik)
                else:
                    flat[path] = (_canon(v), item)
            else:
                flat[path] = (v, item)
        walk((), d, None)
        return flat, items

    def _path_label(self, path, items, item):
        if path[0] == "stages" and len(path) >= 3:
            return "%s \u203a %s" % (STAGE_LABELS.get(path[1], humanize_key(path[1])), humanize_key(path[2]))
        if item is not None:
            return "%s \u203a %s" % (items.get(item, humanize_key(path[0])), humanize_key(path[-1]))
        return humanize_key(path[0]) if len(path) == 1 else " \u203a ".join(humanize_key(x) for x in path)

    def _snapshot_diff(self, before, after):
        fb, ib = self._flatten_snapshot(before)
        fa, ia = self._flatten_snapshot(after)
        added, removed = set(ia) - set(ib), set(ib) - set(ia)
        changes = [{"field": ia[k], "old": None, "new": "added"} for k in sorted(added, key=str)]
        changes += [{"field": ib[k], "old": "present", "new": "removed"} for k in sorted(removed, key=str)]
        for path in sorted(set(fb) | set(fa), key=str):
            vb, itb = fb.get(path, (None, None))
            va, ita = fa.get(path, (None, None))
            item = ita if ita is not None else itb
            if item in added or item in removed:
                continue
            if _fmtval(vb) == _fmtval(va):
                continue
            changes.append({"field": self._path_label(path, {**ib, **ia}, item),
                            "old": _fmtval(vb) or None, "new": _fmtval(va) or None})
        return changes

    def _add_revision(self, conn, rid, kind, user, summary, changes, log_hash=None,
                      category=None, reason=None, amendment_id=None):
        last = conn.execute("SELECT MAX(rev_no) m FROM run_revisions WHERE run_id=?", (rid,)).fetchone()["m"]
        rev = 1 if (last is None and kind == "finalized") else (last or 1) + 1
        conn.execute(
            "INSERT INTO run_revisions (run_id,rev_no,kind,user_name,created_at,summary,changes,log_hash,"
            "category,reason,amendment_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, rev, kind, user["name"] if user else None, now_iso(), summary,
             json.dumps(changes) if changes else None, log_hash, category, reason, amendment_id))
        return rev

    def _run_revisions_public(self, conn, r):
        rows = [{"rev": x["rev_no"], "kind": x["kind"], "by": x["user_name"], "at": x["created_at"],
                 "summary": x["summary"], "changes": json.loads(x["changes"]) if x["changes"] else [],
                 "logHash": x["log_hash"], "reason": x["reason"],
                 "category": self.AMEND_CATEGORIES.get(x["category"], x["category"]) if x["category"] else None}
                for x in conn.execute("SELECT * FROM run_revisions WHERE run_id=? ORDER BY rev_no, id", (r["id"],))]
        if not any(x["rev"] == 1 for x in rows):
            rows.insert(0, {"rev": 1, "kind": "original", "by": r["finalized_by"],
                            "at": r["finalized_at"] or r["created_at"],
                            "summary": "Original record" + ("" if r["release_state"] != "legacy"
                                                            else " (finalized before revision tracking)"),
                            "changes": [], "logHash": None})
        return rows

    # ---- product release: review + Quality sign-off ----------------------- #
    # Finalizing a run holds its finished goods in 'pending_release'. A
    # Production or Quality Manager reviews the production log and signs it
    # off (pending_review -> pending_release), then a Quality Manager signs
    # to release (-> released, lots become 'on_hand' and sellable). Every
    # step re-asks for the signer's password, records who/when/what they
    # attested to and the SHA-256 of the log they saw, and is appended to
    # the hash-chained release_events table. Any later change to the log
    # voids a review/release, returning the run (and its unsold lots) to
    # pending review.
    # Fields that don't belong to the production log itself (audit list, FG
    # inventory movements, the analysis-exclusion flag, release bookkeeping):
    # excluded so moving or selling a lot never "changes the log".
    # Documents are not production-log entries, so they never change the log hash
    # (and never need an amendment).
    _RELEASE_HASH_SKIP = ("edits", "fgLots", "release", "excludeFromStats", "excludeReason",
                          "progress", "revisions", "revision", "attachments", "amendment")

    def _release_snapshot(self, conn, rid):
        r = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        d = self._run_full(conn, r)
        for k in self._RELEASE_HASH_SKIP:
            d.pop(k, None)
        return d

    def _release_snapshot_hash(self, conn, rid):
        return hashlib.sha256(_canon(self._release_snapshot(conn, rid)).encode("utf-8")).hexdigest()

    def _release_lots(self, conn, rid):
        return [fg_public(x) for x in conn.execute(
            "SELECT * FROM fg_lots WHERE run_id=? ORDER BY package_size, fg_lot_number", (rid,))]

    def _set_lot_status(self, conn, rid, from_statuses, to_status):
        """Move this run's lots between statuses; returns what moved."""
        moved = []
        for lot in conn.execute("SELECT * FROM fg_lots WHERE run_id=? AND status IN (%s)"
                                % ",".join("?" * len(from_statuses)), (rid, *from_statuses)).fetchall():
            conn.execute("UPDATE fg_lots SET status=? WHERE id=?", (to_status, lot["id"]))
            moved.append({"lot": lot["fg_lot_number"], "qty": lot["qty"], "from": lot["status"], "to": to_status})
        return moved

    # ---- amend run: controlled changes to a finalized production log ---------- #
    # A finalized run's production log is LOCKED. To change any log entry the
    # user opens an AMENDMENT (reason + category). While it is open the run is
    # 'amending': prior review/release no longer stands, unsold finished goods
    # are held (Pending Release), and the log can be edited. Submitting records
    # ONE revision (reason + field-level diff vs the log as it was when the
    # amendment opened) and sends the run back for review. Documents
    # (/attachments) and the yield-analysis exclusion flag are NOT log entries
    # and never need an amendment; printing labels is client-side only.
    AMEND_CATEGORIES = {
        "data_entry_error": "Data entry error (correction)",
        "late_entry": "Late data entry (completing blank fields)",
        "process_deviation": "Process deviation / investigation finding",
        "additional_samples": "Additional samples taken (new sample entries)",
        "other": "Other",
    }
    _LOG_EXEMPT_SUBPATHS = ("attachments", "amendments", "progress", "lab-results", "sample-label-settings")

    def _open_amendment(self, conn, rid):
        return conn.execute("SELECT * FROM run_amendments WHERE run_id=? AND status='open'", (rid,)).fetchone()

    def _amend_guard(self, conn, method, seg, user):
        """Reject writes to a finalized run's production log unless an amendment is open
        (and then only by a user with the Production Log Amender permission)."""
        if method == "GET" or len(seg) < 3 or not seg[2].isdigit():
            return
        rid = int(seg[2])
        r = conn.execute("SELECT status FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not r or r["status"] != "completed":
            return
        sub = seg[3] if len(seg) > 3 else None
        if sub in self._LOG_EXEMPT_SUBPATHS:
            return
        if len(seg) == 3 and method == "PUT":
            return          # edit_run decides itself (the exclusion flag is exempt)
        if self._open_amendment(conn, rid):
            if not user["can_amend_log"]:
                raise ApiError(403, "Only users with the Production Log Amender permission can edit a run under amendment")
            return
        raise ApiError(409, "This production run is finalized and its log is locked. Use \"Amend run\" "
                            "(with a reason) to change production-log entries.", "amendment_required")

    def _amendment_public(self, a):
        return {"id": a["id"], "runId": a["run_id"], "status": a["status"], "category": a["category"],
                "categoryLabel": self.AMEND_CATEGORIES.get(a["category"], a["category"]), "reason": a["reason"],
                "openedBy": a["opened_by"], "openedAt": a["opened_at"], "priorState": a["prior_state"],
                "closedBy": a["closed_by"], "closedAt": a["closed_at"], "submitComment": a["submit_comment"],
                "revision": a["revision_no"]}

    def _amend_impact(self, conn, r):
        state = r["release_state"]
        return {"state": state, "stateLabel": RELEASE_LABELS.get(state, state),
                "needsSignature": state in ("pending_release", "released", "legacy", "rejected"),
                "lots": self._release_lots(conn, r["id"]), "shipped": self._shipped_units(conn, r["id"])}

    def route_amendments(self, method, seg, conn, user):
        rid = int(seg[2])
        r = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not r or r["status"] != "completed":
            raise ApiError(404, "Finalized production run not found")
        if len(seg) == 4 and method == "GET":
            rows = conn.execute("SELECT * FROM run_amendments WHERE run_id=? ORDER BY id DESC", (rid,)).fetchall()
            op = self._open_amendment(conn, rid)
            return {"amendments": [self._amendment_public(a) for a in rows],
                    "open": self._amendment_public(op) if op else None,
                    "impact": self._amend_impact(conn, r), "categories": self.AMEND_CATEGORIES}
        if len(seg) == 4 and method == "POST":
            return self._amend_open_new(conn, r, user, self._body_json())
        if len(seg) == 6 and seg[4].isdigit():
            a = conn.execute("SELECT * FROM run_amendments WHERE id=? AND run_id=?", (int(seg[4]), rid)).fetchone()
            if not a:
                raise ApiError(404, "Amendment not found")
            if method == "GET" and seg[5] == "preview":
                return self._amend_preview(conn, r, a)
            if method == "POST" and seg[5] in ("submit", "cancel"):
                return self._amend_close(conn, r, a, seg[5], user, self._body_json())
        raise ApiError(404, "Unknown amendment endpoint")

    def _amend_open_new(self, conn, r, user, d):
        rid, state = r["id"], r["release_state"]
        if state == "amending" or self._open_amendment(conn, rid):
            raise ApiError(409, "This run already has an open amendment")
        category = (d.get("category") or "").strip()
        reason = (d.get("reason") or "").strip()
        if category not in self.AMEND_CATEGORIES:
            raise ApiError(400, "Choose the category of this amendment")
        if len(reason) < 5:
            raise ApiError(400, "Enter the reason for amending this run (what is being changed and why)")
        if not user["can_amend_log"]:
            raise ApiError(403, "You don't have permission to amend production logs (ask an administrator for the "
                                "Production Log Amender permission)")
        capacity = None
        if state in ("pending_release", "released", "legacy", "rejected"):
            # Amending product that was reviewed/released is a signed act: re-enter your password.
            if not verify_password(d.get("password") or "", user["password_hash"]):
                raise ApiError(400, "Password is incorrect - your signature was not recorded")
            capacity = "Production Log Amender"
        start = self._release_snapshot(conn, rid)
        start_hash = hashlib.sha256(_canon(start).encode("utf-8")).hexdigest()
        was_complete = 1 if self._run_progress(conn, r)["complete"] else 0
        prior_lots = [{"id": x["id"], "status": x["status"]} for x in conn.execute(
            "SELECT id, status FROM fg_lots WHERE run_id=?", (rid,))]
        held = self._set_lot_status(conn, rid, ("on_hand",), "pending_release") if state in ("released", "legacy") else []
        shipped = self._shipped_units(conn, rid)
        cur = conn.execute(
            "INSERT INTO run_amendments (run_id,status,category,reason,opened_by,opened_by_id,opened_at,prior_state,"
            "prior_review_hash,prior_lots,start_snapshot,start_hash,was_complete) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, "open", category, reason, user["name"], user["id"], now_iso(), state, r["release_review_hash"],
             json.dumps(prior_lots), _canon(start), start_hash, was_complete))
        conn.execute("UPDATE production_runs SET release_state='amending', release_review_hash=NULL WHERE id=?", (rid,))
        release_log(conn, rid, "amendment_opened", user, capacity=capacity,
                    meaning="Amendment opened: the production log is unlocked for correction. Any prior review/release "
                            "no longer stands; finished goods are held until the amendment is re-reviewed.",
                    comment="[%s] %s" % (self.AMEND_CATEGORIES[category], reason), log_hash=start_hash,
                    detail={"from": state, "to": "amending", "amendmentId": cur.lastrowid, "lotsHeld": held,
                            "alreadyShippedLots": shipped})
        return {"amendment": self._amendment_public(conn.execute("SELECT * FROM run_amendments WHERE id=?", (cur.lastrowid,)).fetchone())}

    def _amend_changes(self, conn, rid, a):
        before = json.loads(a["start_snapshot"])
        after = self._release_snapshot(conn, rid)
        return self._snapshot_diff(before, after), after

    def _amend_preview(self, conn, r, a):
        changes, _after = self._amend_changes(conn, r["id"], a)
        prog = self._run_progress(conn, r)
        missing = []
        if a["was_complete"] and not prog["complete"]:
            missing = ["%s: %s" % (s_["label"], ", ".join(s_["missing"])) for s_ in prog["sections"] if s_["missing"]]
        return {"amendment": self._amendment_public(a), "changes": changes, "missingRequired": missing,
                "canSubmit": a["status"] == "open" and bool(changes) and not missing}

    def _amend_close(self, conn, r, a, action, user, d):
        rid = r["id"]
        if a["status"] != "open":
            raise ApiError(409, "This amendment is already %s" % a["status"])
        can_close = bool(user["can_amend_log"] or user["is_production_manager"] or user["is_quality_manager"]
                         or user["role"] == "admin")
        if user["id"] != a["opened_by_id"] and not can_close:
            raise ApiError(403, "Only the person who opened the amendment, an amender, a manager or an administrator can close it")
        if user["id"] == a["opened_by_id"] and not user["can_amend_log"] and not can_close:
            raise ApiError(403, "You no longer have the Production Log Amender permission")
        comment = (d.get("comment") or "").strip() or None
        changes, after = self._amend_changes(conn, rid, a)
        now_hash = hashlib.sha256(_canon(after).encode("utf-8")).hexdigest()
        if action == "cancel":
            if changes:
                raise ApiError(409, "Changes have already been made, so this amendment can't be cancelled - submit it "
                                    "(a reviewer can return it) so the changes stay on record")
            for pl in json.loads(a["prior_lots"] or "[]"):
                cur = conn.execute("SELECT status FROM fg_lots WHERE id=?", (pl["id"],)).fetchone()
                if cur and cur["status"] == "pending_release" and pl["status"] != "pending_release":
                    conn.execute("UPDATE fg_lots SET status=? WHERE id=?", (pl["status"], pl["id"]))
            conn.execute("UPDATE production_runs SET release_state=?, release_review_hash=? WHERE id=?",
                         (a["prior_state"], a["prior_review_hash"], rid))
            conn.execute("UPDATE run_amendments SET status='cancelled', closed_by=?, closed_at=?, submit_comment=? WHERE id=?",
                         (user["name"], now_iso(), comment, a["id"]))
            release_log(conn, rid, "amendment_cancelled", user, capacity=None,
                        meaning="Amendment cancelled with no changes; the run is restored to its prior status.",
                        comment=comment, log_hash=now_hash, detail={"amendmentId": a["id"], "restoredTo": a["prior_state"]})
        else:
            if not changes:
                raise ApiError(409, "No changes were made - cancel the amendment instead")
            if a["was_complete"]:
                prog = self._run_progress(conn, r)
                if not prog["complete"]:
                    miss = "; ".join("%s: %s" % (s_["label"], ", ".join(s_["missing"])) for s_ in prog["sections"] if s_["missing"])
                    raise ApiError(400, "This run was complete before the amendment; required fields can't be left blank. Missing: " + miss)
            summary = "Amendment (%s): %s" % (self.AMEND_CATEGORIES[a["category"]], a["reason"])
            rev = self._add_revision(conn, rid, "amendment", user, summary, changes, now_hash,
                                     category=a["category"], reason=a["reason"], amendment_id=a["id"])
            conn.execute("UPDATE run_amendments SET status='submitted', closed_by=?, closed_at=?, submit_comment=?, revision_no=? WHERE id=?",
                         (user["name"], now_iso(), comment, rev, a["id"]))
            conn.execute("UPDATE production_runs SET release_state='pending_review', release_review_hash=NULL WHERE id=?", (rid,))
            release_log(conn, rid, "amendment_submitted", user, capacity=None,
                        meaning="Amendment submitted: production log changes recorded as Rev %d and sent for re-review." % rev,
                        comment=comment, log_hash=now_hash,
                        detail={"amendmentId": a["id"], "revision": rev, "changes": len(changes), "to": "pending_review"})
        return {"amendment": self._amendment_public(conn.execute("SELECT * FROM run_amendments WHERE id=?", (a["id"],)).fetchone())}

    # ---- data integrity check -------------------------------------------- #
    def _integrity_actor(self, conn, user, d):
        if not (user["role"] == "admin" or user["is_quality_manager"]):
            raise ApiError(403, "Only an administrator or Quality Manager can run the integrity check")

    def _units_accounting(self, conn, rid):
        """Per container unit: packaged (entries), still in FG lots, shipped, disposed."""
        out = {}
        for unit, tot in self._packaging_totals(conn, rid).items():
            out.setdefault(unit, {})["entries"] = tot
        lots = conn.execute("SELECT id, fg_lot_number, package_size, qty FROM fg_lots WHERE run_id=?", (rid,)).fetchall()
        for l in lots:
            u = out.setdefault(l["package_size"], {})
            u["lots"] = u.get("lots", 0) + (l["qty"] or 0)
            u["shipped"] = u.get("shipped", 0) + (conn.execute(
                "SELECT COALESCE(SUM(sl.qty),0) q FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id"
                " WHERE s.status!='cancelled' AND sl.fg_lot_id=?", (l["id"],)).fetchone()["q"])
            u["disposed"] = u.get("disposed", 0) + (conn.execute(
                "SELECT COALESCE(SUM(qty),0) q FROM disposals WHERE entity_type='fg' AND ref=?", (l["fg_lot_number"],)).fetchone()["q"])
        for u in out.values():
            for k in ("entries", "lots", "shipped", "disposed"):
                u.setdefault(k, 0)
            u["accounted"] = u["lots"] + u["shipped"] + u["disposed"]
        return out

    def _integrity_check(self, conn):
        issues = []

        def add(sev, area, run, msg, action=None, repair=None):
            issues.append({"severity": sev, "area": area, "runId": run["id"] if run else None,
                           "run": run["processing_lot"] if run else None, "message": msg,
                           "action": action, "repair": repair})

        unit_litres = packaging_container_litres_map(conn)
        no_entries, incomplete = [], []
        for r in conn.execute("SELECT * FROM production_runs WHERE status='completed' ORDER BY id").fetchall():
            rid, state = r["id"], r["release_state"]
            acct = self._units_accounting(conn, rid)
            has_entries = any(u["entries"] for u in acct.values())
            if not has_entries:
                no_entries.append(r["processing_lot"])
            else:
                for unit, u in sorted(acct.items()):
                    if abs(u["entries"] - u["accounted"]) > 1e-6:
                        add("error", "Finished goods vs packaging", r,
                            "%s: packaging entries total %g but finished-goods lots (%g) + shipped (%g) + disposed (%g) = %g."
                            % (unit, u["entries"], u["lots"], u["shipped"], u["disposed"], u["accounted"]),
                            "Resync the FG lots to the packaging entries (the packaging rows are the source of truth).",
                            "resync_lots")
                expected = self._run_output(conn, rid)[0]
                if abs((r["output_litres"] or 0) - expected) > 0.01:
                    add("warning", "Output litres", r, "Run output is %g L but its packaging entries total %g L."
                        % (r["output_litres"] or 0, expected), "Recompute the run's output litres.", "recompute_output")
                commits = {c["container_unit"]: c["committed_qty"] for c in conn.execute(
                    "SELECT container_unit, committed_qty FROM run_packaging_commits WHERE run_id=?", (rid,))}
                if commits:
                    for unit, u in acct.items():
                        if abs(commits.get(unit, 0) - u["entries"]) > 1e-6:
                            add("warning", "Container / label stock", r,
                                "%s: stock was last committed for %g unit(s) but the packaging entries total %g."
                                % (unit, commits.get(unit, 0), u["entries"]),
                                "Re-commit the net difference to container and label stock.", "recommit_stock")
            for l in conn.execute("SELECT * FROM fg_lots WHERE run_id=?", (rid,)).fetchall():
                if (l["qty"] or 0) < 0:
                    add("error", "Finished goods", r, "Lot %s has a negative quantity (%g)." % (l["fg_lot_number"], l["qty"]))
                if l["status"] == "on_hand" and state in ("pending_review", "pending_release", "returned", "amending") and (l["qty"] or 0) > 0:
                    add("error", "Release gate", r, "Lot %s is On hand (sellable) but its run is '%s'."
                        % (l["fg_lot_number"], RELEASE_LABELS.get(state, state)),
                        "Return the lot to Pending Release.", "fix_lot_status")
                if l["status"] == "pending_release" and state in ("released", "legacy"):
                    add("warning", "Release gate", r, "Lot %s is Pending Release although its run is released."
                        % l["fg_lot_number"], "Release the lot (status On hand).", "fix_lot_status")
            if state in ("pending_release", "released") and r["release_review_hash"]:
                if self._release_snapshot_hash(conn, rid) != r["release_review_hash"]:
                    add("error", "Production log", r,
                        "The production log no longer matches the log that was signed off - it was changed outside the amendment workflow.",
                        "A Quality Manager should reopen the run and re-review it.")
            op = self._open_amendment(conn, rid)
            if op and state != "amending":
                add("error", "Amendment", r, "An amendment is open but the run status is '%s'." % RELEASE_LABELS.get(state, state))
            if state == "amending" and not op:
                add("error", "Amendment", r, "The run is 'Under amendment' but no amendment is open.")
            if op:
                age = (datetime.datetime.utcnow() - datetime.datetime.strptime(op["opened_at"][:19], "%Y-%m-%dT%H:%M:%S")).days
                if age >= 7:
                    add("warning", "Amendment", r, "Amendment opened by %s %d days ago is still open - its finished goods are on hold."
                        % (op["opened_by"], age), "Submit or cancel the amendment.")
            if state != "legacy" and not self._run_progress(conn, r)["complete"]:
                incomplete.append(r["processing_lot"])
        chain = release_verify_chain(conn)
        if not chain["ok"]:
            issues.append({"severity": "error", "area": "Audit trail", "runId": chain["runId"], "run": None,
                           "message": "The release audit trail hash chain is broken at event #%s." % chain["brokenAtEventId"],
                           "action": "Restore from a backup and investigate; this indicates the audit table was altered.", "repair": None})
        if incomplete:
            issues.append({"severity": "info", "area": "Required fields", "runId": None, "run": None,
                           "message": "%d finalized run(s) are missing required production-log fields: %s."
                                      % (len(incomplete), ", ".join(incomplete)),
                           "action": "Complete them through an Amend run (category: late data entry).", "repair": None})
        if no_entries:
            issues.append({"severity": "info", "area": "Finished goods vs packaging", "runId": None, "run": None,
                           "message": "%d earlier run(s) have no packaging entries (finalized before the Packaging table), so their "
                                      "finished goods can't be cross-checked: %s." % (len(no_entries), ", ".join(no_entries)),
                           "action": None, "repair": None})
        order = {"error": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda i: (order[i["severity"]], i["runId"] or 0))
        for n, i in enumerate(issues, 1):
            i["id"] = n
        return {"checkedAt": now_iso(), "chain": chain,
                "summary": {s_: sum(1 for i in issues if i["severity"] == s_) for s_ in ("error", "warning", "info")},
                "issues": issues}

    def _integrity_repair(self, conn, user, d):
        self._integrity_actor(conn, user, d)
        if not verify_password(d.get("password") or "", user["password_hash"]):
            raise ApiError(400, "Password is incorrect - the repair was not applied")
        kind = d.get("kind")
        r = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='completed'", (d.get("runId"),)).fetchone()
        if not r:
            raise ApiError(404, "Production run not found")
        rid = r["id"]
        unit_litres = packaging_container_litres_map(conn)
        detail = {"kind": kind}
        if kind == "resync_lots":
            deltas = {}
            for unit, u in self._units_accounting(conn, rid).items():
                delta = u["entries"] - u["accounted"]
                if abs(delta) > 1e-6:
                    self._adjust_fg_lot(conn, r, unit, delta, unit_litres)
                    deltas[unit] = delta
            if not deltas:
                raise ApiError(409, "Nothing to resync - the lots already match the packaging entries")
            detail["lotAdjustments"] = deltas
            self._sync_completed_output(conn, rid)
        elif kind == "recompute_output":
            self._sync_completed_output(conn, rid)
        elif kind == "recommit_stock":
            self._commit_packaging_stock(conn, rid, user["name"])
        elif kind == "fix_lot_status":
            moved = []
            for l in conn.execute("SELECT * FROM fg_lots WHERE run_id=?", (rid,)).fetchall():
                want = None
                if l["status"] == "on_hand" and r["release_state"] in ("pending_review", "pending_release", "returned", "amending"):
                    want = "pending_release"
                elif l["status"] == "pending_release" and r["release_state"] in ("released", "legacy"):
                    want = "on_hand"
                if want:
                    conn.execute("UPDATE fg_lots SET status=? WHERE id=?", (want, l["id"]))
                    moved.append({"lot": l["fg_lot_number"], "from": l["status"], "to": want})
            detail["lots"] = moved
        else:
            raise ApiError(400, "Unknown repair")
        release_log(conn, rid, "integrity_repair", user, capacity="Administrator" if user["role"] == "admin" else "Quality Manager",
                    meaning="Derived records re-synchronised with the production log by the integrity check.",
                    comment=kind, detail=detail)
        return self._integrity_check(conn)

    def _run_output(self, conn, run_id):
        """(output litres, IBC count) of a run from its packaging rows. A finished-goods lot keeps the litres per unit it was created with,
        so a lot that exists decides the size of its unit; a later change to the container's fill volume only affects units without a lot."""
        unit_litres = packaging_container_litres_map(conn)
        for lot in conn.execute("SELECT package_size, litres_each FROM fg_lots WHERE run_id=? AND litres_each IS NOT NULL", (run_id,)):
            unit_litres[lot["package_size"]] = lot["litres_each"]
        output, ibc = 0.0, 0
        for unit, qty in self._packaging_totals(conn, run_id).items():
            qty = num(qty)
            if unit in unit_litres and qty > 0:
                output += unit_litres[unit] * qty
                if is_ibc_unit(unit):
                    ibc += int(qty)
        return round(output, 2), ibc

    def _sync_completed_output(self, conn, run_id):
        output, ibc = self._run_output(conn, run_id)
        conn.execute("UPDATE production_runs SET output_litres=?, ibc_used=? WHERE id=?", (output, ibc, run_id))

    def route_integrity(self, method, seg, conn, user):
        self._integrity_actor(conn, user, {})
        if seg == ["api", "integrity"] and method == "GET":
            return self._integrity_check(conn)
        if seg == ["api", "integrity", "repair"] and method == "POST":
            return self._integrity_repair(conn, user, self._body_json())
        raise ApiError(404, "Unknown integrity endpoint")

    def _shipped_units(self, conn, rid):
        """Units of this run already shipped on non-cancelled shipments -- the app
        can't recall them, so a void/reopen records them for follow-up."""
        return [{"lot": x["fg_lot_number"], "qty": x["qty"], "shipment": x["shipment_no"]} for x in conn.execute(
            "SELECT l.fg_lot_number, l.qty, s.shipment_no FROM shipment_lines l"
            " JOIN shipments s ON s.id=l.shipment_id JOIN fg_lots f ON f.id=l.fg_lot_id"
            " WHERE f.run_id=? AND s.status!='cancelled' ORDER BY s.id", (rid,))]

    def _release_void(self, conn, r, user, now_hash, reason):
        rid = r["id"]
        was = r["release_state"]
        # A lot already released (on_hand) goes back into quarantine; units that
        # already shipped can't be recalled by the app, so they're listed.
        moved = self._set_lot_status(conn, rid, ("on_hand",), "pending_release") if was == "released" else []
        shipped = self._shipped_units(conn, rid)
        conn.execute("UPDATE production_runs SET release_state='pending_review', release_review_hash=NULL WHERE id=?", (rid,))
        release_log(conn, rid, "voided", user, capacity="System",
                    meaning="Review/release voided: the production log no longer matches the log that was signed.",
                    comment=reason, log_hash=now_hash,
                    detail={"from": was, "to": "pending_review", "lotsReturned": moved, "alreadyShippedLots": shipped})

    def _release_signer(self, conn, user, d, need_quality):
        """Re-authenticates the signer (password) and checks their sign-off permission."""
        if not verify_password(d.get("password") or "", user["password_hash"]):
            raise ApiError(400, "Password is incorrect - your signature was not recorded")
        is_qm, is_pm = bool(user["is_quality_manager"]), bool(user["is_production_manager"])
        if need_quality:
            if not is_qm:
                raise ApiError(403, "Only a Quality Manager can sign this step")
            return "Quality Manager"
        if not (is_qm or is_pm):
            raise ApiError(403, "Only a Production Manager or Quality Manager can sign this step")
        want = (d.get("capacity") or "").strip()
        if want == "Quality Manager" and is_qm:
            return "Quality Manager"
        if want == "Production Manager" and is_pm:
            return "Production Manager"
        return "Production Manager" if is_pm else "Quality Manager"

    def _release_events_public(self, conn, rid):
        return [{"id": e["id"], "type": e["event_type"], "user": e["user_name"], "email": e["user_email"],
                 "capacity": e["capacity"], "meaning": e["meaning"], "comment": e["comment"],
                 "logHash": e["log_hash"], "detail": json.loads(e["detail"]) if e["detail"] else None,
                 "at": e["created_at"], "entryHash": e["entry_hash"]}
                for e in conn.execute("SELECT * FROM release_events WHERE run_id=? ORDER BY id", (rid,))]

    def _release_summary(self, conn, r):
        lots = self._release_lots(conn, r["id"])
        ev = self._release_events_public(conn, r["id"])
        last = {}
        for e in ev:
            last[e["type"]] = e
        return {"id": r["id"], "lot": r["processing_lot"], "sku": r["sku_code"], "runDate": r["run_date"],
                "outputLitres": r["output_litres"], "finalizedAt": r["finalized_at"], "finalizedBy": r["finalized_by"],
                "state": r["release_state"], "label": RELEASE_LABELS.get(r["release_state"], "—"),
                "lots": lots,
                "amendment": (lambda am: self._amendment_public(am) if am else None)(self._open_amendment(conn, r["id"])),
                "reviewedBy": (last.get("review_approved") or {}).get("user"),
                "reviewedAt": (last.get("review_approved") or {}).get("at"),
                "releasedBy": (last.get("released") or {}).get("user"),
                "releasedAt": (last.get("released") or {}).get("at"),
                "releasedComment": (last.get("released") or {}).get("comment"),
                "lab": coa_evaluate(conn, r)["summary"]}

    def route_release(self, method, seg, query, conn, user):
        me = {"canReview": bool(user["is_production_manager"] or user["is_quality_manager"]),
              "canRelease": bool(user["is_quality_manager"])}
        if seg == ["api", "release"] and method == "GET":
            runs = [self._release_summary(conn, r) for r in conn.execute(
                "SELECT * FROM production_runs WHERE status='completed' AND release_state IS NOT NULL"
                " AND release_state!='legacy' ORDER BY id DESC")]
            return {"runs": runs, "me": me,
                    "legacyCount": conn.execute("SELECT COUNT(*) c FROM production_runs WHERE release_state='legacy'").fetchone()["c"]}
        if seg == ["api", "release", "verify"] and method == "GET":
            return release_verify_chain(conn)
        if len(seg) >= 4 and seg[2] == "runs" and seg[3].isdigit():
            rid = int(seg[3])
            r = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='completed'", (rid,)).fetchone()
            if not r:
                raise ApiError(404, "Production run not found")
            if len(seg) == 4 and method == "GET":
                now_hash = self._release_snapshot_hash(conn, rid)
                out = self._release_summary(conn, r)
                out.update({"run": self._run_full(conn, r), "events": self._release_events_public(conn, rid),
                            "me": me, "currentLogHash": now_hash, "reviewedLogHash": r["release_review_hash"],
                            "logMatchesReview": (r["release_review_hash"] == now_hash) if r["release_review_hash"] else None,
                            "chain": release_verify_chain(conn)})
                return out
            if len(seg) == 5 and method == "POST" and seg[4] in ("review", "release", "reopen", "resubmit"):
                return self._release_action(conn, r, seg[4], self._body_json(), user)
        raise ApiError(404, "Unknown release endpoint")

    def _release_action(self, conn, r, action, d, user):
        rid, state = r["id"], r["release_state"]
        comment = (d.get("comment") or "").strip() or None
        decision = (d.get("decision") or "").strip()

        if action == "review":
            if state != "pending_review":
                raise ApiError(409, "This run is not awaiting production-log review (status: %s)" % RELEASE_LABELS.get(state, state))
            if decision not in ("approve", "return"):
                raise ApiError(400, "Choose approve or return for correction")
            if decision == "return" and not comment:
                raise ApiError(400, "Enter what needs to be corrected before returning the run")
            capacity = self._release_signer(conn, user, d, need_quality=False)
            now_hash = self._release_snapshot_hash(conn, rid)
            if decision == "approve":
                conn.execute("UPDATE production_runs SET release_state='pending_release', release_review_hash=? WHERE id=?",
                             (now_hash, rid))
                release_log(conn, rid, "review_approved", user, capacity=capacity,
                            meaning="I have reviewed the production log for %s and confirm it is complete and accurate." % r["processing_lot"],
                            comment=comment, log_hash=now_hash, detail={"from": "pending_review", "to": "pending_release"})
            else:
                conn.execute("UPDATE production_runs SET release_state='returned', release_review_hash=NULL WHERE id=?", (rid,))
                release_log(conn, rid, "review_returned", user, capacity=capacity,
                            meaning="Production log returned for correction; not approved.",
                            comment=comment, log_hash=now_hash, detail={"from": "pending_review", "to": "returned"})
        elif action == "resubmit":
            if state != "returned":
                raise ApiError(409, "Only a run returned for correction can be resubmitted")
            if not comment:
                raise ApiError(400, "Describe what was corrected")
            now_hash = self._release_snapshot_hash(conn, rid)
            conn.execute("UPDATE production_runs SET release_state='pending_review' WHERE id=?", (rid,))
            release_log(conn, rid, "resubmitted", user, capacity=None,
                        meaning="Corrections made; production log resubmitted for review.",
                        comment=comment, log_hash=now_hash, detail={"from": "returned", "to": "pending_review"})
        elif action == "release":
            if state != "pending_release":
                raise ApiError(409, "This run has not passed production-log review (status: %s)" % RELEASE_LABELS.get(state, state))
            if decision not in ("release", "reject"):
                raise ApiError(400, "Choose release or reject")
            if decision == "reject" and not comment:
                raise ApiError(400, "Enter the reason for rejecting this product")
            failed = []
            if decision == "release":
                lab = coa_evaluate(conn, r)["summary"]
                if lab["missingRequired"]:
                    raise ApiError(409, "Release blocked: required lab results are not on file yet (%s). "
                                        "Enter them with the run's Lab results button (Production tab)." % ", ".join(lab["missingRequired"]))
                failed = lab["failed"]
                if failed and not comment:
                    raise ApiError(400, "Results outside specification (%s). Enter a comment explaining why this lot is being released." % ", ".join(failed))
            capacity = self._release_signer(conn, user, d, need_quality=True)
            now_hash = self._release_snapshot_hash(conn, rid)
            if now_hash != r["release_review_hash"]:
                # Belt and braces: a change that slipped past the edit hook.
                self._release_void(conn, r, user, now_hash, "Production log differs from the log that was reviewed")
                raise ApiError(409, "The production log changed after it was reviewed - it has been returned to review")
            if decision == "release":
                moved = self._set_lot_status(conn, rid, ("pending_release",), "on_hand")
                conn.execute("UPDATE production_runs SET release_state='released' WHERE id=?", (rid,))
                release_log(conn, rid, "released", user, capacity=capacity,
                            meaning=("I release this product for sale with results outside specification (%s), for the reason given." % ", ".join(failed))
                            if failed else "I confirm this product conforms to specification and is released for sale.",
                            comment=comment, log_hash=now_hash,
                            detail=dict({"from": "pending_release", "to": "released", "lots": moved}, **({"outOfSpec": failed} if failed else {})))
            else:
                moved = self._set_lot_status(conn, rid, ("pending_release",), "hold")
                conn.execute("UPDATE production_runs SET release_state='rejected' WHERE id=?", (rid,))
                release_log(conn, rid, "release_rejected", user, capacity=capacity,
                            meaning="Product rejected; held and not released for sale.",
                            comment=comment, log_hash=now_hash,
                            detail={"from": "pending_release", "to": "rejected", "lots": moved})
        elif action == "reopen":
            if state not in ("released", "rejected", "pending_release", "returned"):
                raise ApiError(409, "This run is already awaiting review")
            if not comment:
                raise ApiError(400, "Enter the reason for reopening this run")
            capacity = self._release_signer(conn, user, d, need_quality=True)
            now_hash = self._release_snapshot_hash(conn, rid)
            moved = []
            if state == "released":
                moved = self._set_lot_status(conn, rid, ("on_hand",), "pending_release")
            elif state == "rejected":
                moved = self._set_lot_status(conn, rid, ("hold",), "pending_release")
            shipped = self._shipped_units(conn, rid)
            conn.execute("UPDATE production_runs SET release_state='pending_review', release_review_hash=NULL WHERE id=?", (rid,))
            release_log(conn, rid, "reopened", user, capacity=capacity,
                        meaning="Run reopened: prior review/release no longer stands; a new review is required.",
                        comment=comment, log_hash=now_hash,
                        detail={"from": state, "to": "pending_review", "lotsReturned": moved, "alreadyShippedLots": shipped})
        return self._release_summary(conn, conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone())

    def route_production(self, method, seg, conn, user):
        if seg == ["api", "production"] and method == "GET":
            runs = []
            for r in conn.execute(
                    "SELECT * FROM production_runs WHERE status='completed' ORDER BY run_date DESC, id DESC"):
                runs.append(self._run_full(conn, r))
            return {"runs": runs}
        if seg == ["api", "production"] and method == "POST":
            return self.create_run(conn, user)
        if seg == ["api", "production", "drafts"] and method == "GET":
            return self.list_drafts(conn)
        if seg == ["api", "production", "drafts"] and method == "POST":
            return self.save_draft(conn, None, user)
        if len(seg) == 4 and seg[2] == "drafts" and seg[3].isdigit():
            rid = int(seg[3])
            if method == "GET":
                return self.get_draft(conn, rid)
            if method == "PUT":
                return self.save_draft(conn, rid, user)
            if method == "DELETE":
                return self.delete_draft(conn, rid, user)
        if len(seg) >= 4 and seg[2].isdigit() and seg[3] == "amendments":
            return self.route_amendments(method, seg, conn, user)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "progress" and method == "GET":
            r = conn.execute("SELECT * FROM production_runs WHERE id=?", (int(seg[2]),)).fetchone()
            if not r:
                raise ApiError(404, "Production run not found")
            out = {"progress": self._run_progress(conn, r)}
            if r["status"] == "completed":
                out["revisions"] = self._run_revisions_public(conn, r)
            return out
        if len(seg) == 5 and seg[2] == "drafts" and seg[3].isdigit() and seg[4] == "finalize" and method == "POST":
            return self.finalize_draft(conn, int(seg[3]), user)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "sample-labels" and method == "GET":
            # the run's logged sample points with their label settings and collection times -- works for a draft and a finalized run
            r = conn.execute("SELECT * FROM production_runs WHERE id=?", (int(seg[2]),)).fetchone()
            if not r:
                raise ApiError(404, "Production run not found")
            points = []
            locked = {x["sample_point_id"]: x["n"] for x in conn.execute(
                "SELECT sample_point_id, COUNT(*) n FROM samples WHERE run_id=? AND requisition_id IS NOT NULL GROUP BY sample_point_id", (r["id"],))}
            for pt in conn.execute("SELECT * FROM run_sample_points WHERE run_id=? ORDER BY created_at, id", (r["id"],)):
                stage = pt["stage"] or "homogenization"
                _abbr, label, col = sample_stage_info(stage)
                collected = (r[col] if col and col in r.keys() else None) or pt["created_at"]
                points.append({"id": pt["id"], "stage": stage, "stageLabel": label, "type": pt["type"] or "Slurry",
                               "description": pt["description"] or "Microbial", "qty": max(1, int(pt["qty"] or 1)), "collectedAt": collected,
                               "labelType": pt["label_type"] if pt["label_type"] in ("detailed", "simplified") else "detailed",
                               "numbered": bool(pt["label_numbered"]), "lockedCount": locked.get(pt["id"], 0),
                               "labelName": pt["label_name"] or sample_label_name(conn, stage),
                               "defaultLabelName": sample_label_name(conn, stage)})
            return {"lot": r["processing_lot"], "lotSimplified": lot_simplified(r["processing_lot"]), "points": points}
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "sample-label-settings" and method == "PUT":
            # per sample point: label type (detailed / simplified), numbering and name; the samples' IDs follow (refresh_sample_ids)
            r = conn.execute("SELECT * FROM production_runs WHERE id=?", (int(seg[2]),)).fetchone()
            if not r:
                raise ApiError(404, "Production run not found")
            for item in (self._body_json().get("points") or []):
                pt = conn.execute("SELECT * FROM run_sample_points WHERE id=? AND run_id=?", (item.get("id"), r["id"])).fetchone()
                if not pt:
                    continue
                ltype = item.get("labelType")
                if ltype not in ("detailed", "simplified"):
                    raise ApiError(400, "Label type must be Detailed or Simplified")
                name = (item.get("labelName") or "").strip()[:60]
                default = sample_label_name(conn, pt["stage"] or "homogenization")
                conn.execute("UPDATE run_sample_points SET label_type=?, label_numbered=?, label_name=? WHERE id=?",
                             (ltype, 1 if item.get("numbered") else 0, name if name and name != default else None, pt["id"]))
            refresh_sample_ids(conn, r["id"])
            return {"ok": True}
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "qc-checks" and method == "GET":
            return {"qcChecks": self._qc_checks_public(conn, int(seg[2]))}
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "edits" and method == "GET":
            return {"edits": self._run_edits(conn, int(seg[2]))}
        if len(seg) == 3 and seg[2].isdigit() and method == "PUT":
            return self.edit_run(conn, int(seg[2]), user)
        if len(seg) >= 4 and seg[2].isdigit() and seg[3] == "lab-results":
            return self.route_lab_results(method, seg, conn, user)
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
            if len(seg) == 6 and seg[4].isdigit() and seg[5] == "preview" and method == "GET":
                a = conn.execute("SELECT * FROM run_attachments WHERE id=? AND run_id=?", (int(seg[4]), rid)).fetchone()
                if not a:
                    raise ApiError(404, "Document not found")
                name = (a["filename"] or "").lower()
                if not (name.endswith(".docx") or name.endswith(".xlsx")):
                    raise ApiError(400, "A preview is available for Word (.docx) and Excel (.xlsx) documents; other files open with View.")
                try:
                    with open(os.path.join(UPLOAD_DIR, a["stored_name"]), "rb") as f:
                        raw = f.read()
                except OSError:
                    raise ApiError(404, "File missing on disk")
                return {"filename": a["filename"], "kind": "docx" if name.endswith(".docx") else "xlsx",
                        "html": docx_to_html(raw) if name.endswith(".docx") else xlsx_to_html(raw)}
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "feedstock-photo" and method == "POST":
            return self.feedstock_photo_draft(conn, int(seg[2]), user)
        if len(seg) == 5 and seg[2].isdigit() and seg[3] == "inputs" and seg[4].isdigit() and method == "PUT":
            return self.update_run_input(conn, int(seg[2]), int(seg[4]), user)
        if (len(seg) == 6 and seg[2].isdigit() and seg[3] == "feedstock" and seg[4].isdigit()
                and seg[5] == "save" and method == "POST"):
            return self.save_feedstock_input(conn, int(seg[2]), int(seg[4]), user)
        if (len(seg) == 5 and seg[2].isdigit() and seg[3] == "feedstock" and seg[4].isdigit()
                and method == "DELETE"):
            return self.release_feedstock_tote(conn, int(seg[2]), int(seg[4]), user)
        if (len(seg) == 6 and seg[2].isdigit() and seg[3] == "inputs" and seg[4].isdigit()
                and seg[5] == "photo" and method == "POST"):
            return self.upload_input_photo(conn, int(seg[2]), int(seg[4]), user)
        if len(seg) == 5 and seg[2].isdigit() and seg[3] == "stages" and method == "PUT":
            return self.save_stage(conn, int(seg[2]), seg[4], user)
        if len(seg) >= 4 and seg[2].isdigit() and seg[3] == "dilution-passes":
            return self.route_dilution_passes(method, seg, conn, user)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "dilutions":
            rid = int(seg[2])
            if not conn.execute("SELECT 1 FROM production_runs WHERE id=?", (rid,)).fetchone():
                raise ApiError(404, "Production run not found")
            if method == "GET":
                return {"dilutions": self._dilutions_public(conn, rid)}
            if method == "POST":
                return self.add_dilution(conn, rid, user)
        if len(seg) == 5 and seg[2].isdigit() and seg[3] == "dilutions" and seg[4].isdigit():
            rid, did = int(seg[2]), int(seg[4])
            if method == "PUT":
                return self.update_dilution(conn, rid, did, user)
            if method == "DELETE":
                return self.delete_dilution(conn, rid, did)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "sample-points":
            rid = int(seg[2])
            if not conn.execute("SELECT 1 FROM production_runs WHERE id=?", (rid,)).fetchone():
                raise ApiError(404, "Production run not found")
            if method == "GET":
                return {"samplePoints": self._sample_points_public(conn, rid)}
            if method == "POST":
                return self.add_sample_point(conn, rid, user)
        if len(seg) == 5 and seg[2].isdigit() and seg[3] == "sample-points" and seg[4].isdigit():
            rid, spid = int(seg[2]), int(seg[4])
            if method == "PUT":
                return self.update_sample_point(conn, rid, spid, user)
            if method == "DELETE":
                return self.delete_sample_point(conn, rid, spid, user)
        if len(seg) == 4 and seg[2].isdigit() and seg[3] == "packaging-entries":
            rid = int(seg[2])
            if not conn.execute("SELECT 1 FROM production_runs WHERE id=?", (rid,)).fetchone():
                raise ApiError(404, "Production run not found")
            if method == "GET":
                return {"packagingEntries": self._packaging_entries_public(conn, rid)}
            if method == "POST":
                return self.add_packaging_entry(conn, rid, user)
        if len(seg) == 5 and seg[2].isdigit() and seg[3] == "packaging-entries" and seg[4].isdigit():
            rid, peid = int(seg[2]), int(seg[4])
            if method == "PUT":
                return self.update_packaging_entry(conn, rid, peid, user)
            if method == "DELETE":
                return self.delete_packaging_entry(conn, rid, peid, user)
        raise ApiError(404, "Unknown production endpoint")

    def _attachments(self, conn, run_id):
        return [{"id": r["id"], "filename": r["filename"], "contentType": r["content_type"],
                 "size": r["size"], "uploadedBy": r["uploaded_by"], "uploadedAt": r["uploaded_at"]}
                for r in conn.execute(
                    "SELECT * FROM run_attachments WHERE run_id=? ORDER BY uploaded_at DESC, id DESC",
                    (run_id,))]

    def _check_storage_room(self, conn, nbytes):
        """Refuse an upload that would fill the document store or the disk (every user can upload, and the disk is shared with the database)."""
        used = sum(conn.execute("SELECT COALESCE(SUM(size),0) FROM %s" % t).fetchone()[0] for t in ("run_attachments", "tote_attachments", "sop_documents"))
        if used + nbytes > MAX_UPLOAD_TOTAL_BYTES:
            raise ApiError(507, "The document store is full (%d MB used of %d MB). Ask an administrator to remove old documents."
                           % (used // (1024 * 1024), MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)))
        try:
            free = shutil.disk_usage(UPLOAD_DIR if os.path.isdir(UPLOAD_DIR) else os.path.dirname(UPLOAD_DIR)).free
        except OSError:
            return
        if free - nbytes < MIN_FREE_DISK_BYTES:
            raise ApiError(507, "The server is almost out of disk space; the upload was refused.")

    def _write_upload(self, directory, raw, ext):
        """Write an uploaded file under an opaque name. The path is remembered so it is deleted again if the request fails before its commit."""
        os.makedirs(directory, exist_ok=True)
        stored = secrets.token_hex(8) + ext
        path = os.path.join(directory, stored)
        with open(path, "wb") as f:
            f.write(raw)
        self._created_files = getattr(self, "_created_files", []) + [path]
        return stored

    def _remove_files(self, paths):
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def _store_attachment(self, conn, run_id, filename, content_type, data_b64, uploaded_by):
        """Decode+store one base64 file and insert its run_attachments row.
        Shared by generic document uploads and every stage/tote photo slot."""
        filename = (filename or "document").strip().replace("\\", "/").split("/")[-1] or "document"
        data_b64 = data_b64 or ""
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
        check_upload_filename(filename)
        self._check_storage_room(conn, len(raw))
        stored = self._write_upload(UPLOAD_DIR, raw, os.path.splitext(filename)[1][:12])
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO run_attachments (run_id,filename,content_type,size,stored_name,"
            "uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, filename, served_type(filename)[0], len(raw),
             stored, uploaded_by, now_iso()))
        return cur.lastrowid

    def add_attachment(self, conn, run_id, user):
        d = self._body_json()
        self._store_attachment(conn, run_id, d.get("filename"), d.get("contentType"),
                                d.get("dataB64") or "", user["name"] if user else None)
        return {"attachments": self._attachments(conn, run_id)}

    def _delete_attachment_row(self, conn, aid):
        """Remove an attachment's file+row by id, if it exists. Silent no-op
        if it doesn't (used when replacing a stage/tote photo)."""
        row = conn.execute("SELECT * FROM run_attachments WHERE id=?", (aid,)).fetchone()
        if not row:
            return
        try:
            os.remove(os.path.join(UPLOAD_DIR, row["stored_name"]))
        except OSError:
            pass
        conn.execute("DELETE FROM run_attachments WHERE id=?", (aid,))

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

    # ---- feedstock characterization (run_inputs) --------------------------- #
    def _run_inputs_public(self, conn, run_id):
        out = []
        for r in conn.execute(
                "SELECT ri.*, t.lot_number, t.site_code, t.species_code FROM run_inputs ri "
                "JOIN tote_lots t ON t.id=ri.tote_lot_id WHERE ri.run_id=? ORDER BY t.lot_number",
                (run_id,)):
            out.append({
                "id": r["id"], "toteLotId": r["tote_lot_id"], "toteLot": r["lot_number"],
                "site": r["site_code"], "species": r["species_code"],
                "loadedAt": r["loaded_at"], "surfacePhoto": r["surface_photo"],
                "striationPhoto": r["striation_photo"], "ph": r["ph"],
                "phMeasuredAt": r["ph_measured_at"], "orp": r["orp"],
                "orpRange": r["orp_range"], "weightKg": r["weight_kg"],
                "volumeL": r["volume_l"], "densityKgL": r["density_kg_l"],
                "odour": r["odour"], "odourOther": r["odour_other"],
                "odourIntensity": r["odour_intensity"],
                "decision": r["decision"] if r["decision_set"] else None,
                "rejectionReason": r["rejection_reason"], "notes": r["notes"],
            })
        return out

    def _apply_feedstock_detail(self, conn, input_id, fd, tote_lot_id=None):
        """Apply staged draft characterization (+ already-uploaded photo attachment
        ids) onto a run_inputs row. If the decision is (or becomes) 'rejected',
        also relocate the tote to QAQC Hold — it's excluded from production
        selection and run calculations from that point on."""
        if not fd:
            return
        updates = {}
        for col, key, kind in self.INPUT_FIELDS:
            if key not in fd:
                continue
            if col == "decision" and fd[key] not in ("accepted", "rejected"):
                continue   # an undecided card never overwrites (the column is NOT NULL)
            updates[col] = numn(fd[key]) if kind == "num" else ((fd[key] or "").strip() or None)
        if fd.get("decision") in ("accepted", "rejected"):
            updates["decision_set"] = 1
        if fd.get("surfacePhotoId"):
            updates["surface_photo"] = fd["surfacePhotoId"]
        if fd.get("striationPhotoId"):
            updates["striation_photo"] = fd["striationPhotoId"]
        if updates:
            sets = ", ".join("%s=?" % c for c in updates)
            conn.execute("UPDATE run_inputs SET %s WHERE id=?" % sets, (*updates.values(), input_id))
        if updates.get("decision") == "rejected" and tote_lot_id:
            conn.execute("UPDATE tote_lots SET location=?, status='hold', run_id=NULL WHERE id=?",
                         (QAQC_HOLD_LOCATION, tote_lot_id))

    def update_run_input(self, conn, run_id, input_id, user):
        row = conn.execute("SELECT * FROM run_inputs WHERE id=? AND run_id=?",
                           (input_id, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Feedstock input not found")
        d = self._body_json()
        if "decision" in d and d["decision"] not in ("accepted", "rejected"):
            raise ApiError(400, "Choose Accepted or Rejected for the feedstock decision")
        if "decision" in d and self._run_is_completed(conn, run_id) and (not row["decision_set"] or d["decision"] != row["decision"]):
            # accepted / rejected decides which totes were processed: it sets the run's input weight, the totes' status and the IBC pool,
            # and none of those is re-derived after finalize -- so the decision is fixed with the run
            raise ApiError(409, "The accepted / rejected decision cannot be changed once the run is finalized: it decides which totes "
                                "were processed and the run's input weight. Other characterization details can still be amended.",
                           "decision_locked")
        self._apply_feedstock_detail(conn, input_id, d, row["tote_lot_id"])
        return {"inputs": self._run_inputs_public(conn, run_id)}

    # Field labels for the Feedstock Stability log, shared by every path that
    # writes a tote's captured characterization there directly (a tote
    # rejected from a production run, or any save from Feedstock Inventory's
    # own Detail card, which has no run to hang a run_inputs row off of).
    CHARACTERIZATION_LABELS = [
        ("loadedAt", "Loaded at"), ("ph", "pH"), ("orp", "ORP (mV)"),
        ("orpRange", "ORP classification"), ("weightKg", "Weight (kg)"),
        ("volumeL", "Volume (L)"), ("densityKgL", "Density (kg/L)"),
        ("odour", "Odour"), ("odourOther", "Odour (other)"),
        ("odourIntensity", "Odour intensity"), ("rejectionReason", "Rejection reason"),
        ("notes", "Notes"),
    ]

    def _latest_characterization(self, conn, tote_lot_id, tote):
        """Best-known current value for each Feedstock characterization
        field, to pre-fill a fresh card with -- pH/ORP come from the tote's
        own current reading; every other field has no persisted 'current'
        column, so it's pulled from its own most recent Feedstock Stability
        log entry, if any. Decision and rejection reason are deliberately
        left out: a new characterization always starts as a fresh accepted
        inspection, never a carry-over of a past rejection."""
        kind_for = {key: kind for _, key, kind in self.INPUT_FIELDS}
        out = {"ph": tote["ph"], "orp": tote["orp"]}
        for key, label in self.CHARACTERIZATION_LABELS:
            if key in out or key == "rejectionReason":
                continue
            last = conn.execute(
                "SELECT new_value FROM tote_stability_log WHERE tote_lot_id=? AND field=?"
                " ORDER BY id DESC LIMIT 1", (tote_lot_id, label)).fetchone()
            val = last["new_value"] if last else None
            if val is not None and kind_for.get(key) == "num":
                val = numn(val)
            out[key] = val
        return out

    def _log_characterization_fields(self, conn, tote_lot_id, user, fd, note, run_id=None):
        """Write a Feedstock Stability log row only for fields that actually
        changed since they were last captured -- re-saving a card with the
        same values (e.g. touching just one field) shouldn't spam the log
        with rows that all read 'unchanged'. pH/ORP are diffed against the
        tote's current reading (also its 'From' baseline, since every writer
        of those two columns keeps this log in step with them); every other
        field has no persisted 'current value' outside this log, so it's
        diffed against its own most recent entry for this tote (or treated
        as new/changed if there isn't one yet). A photo is diffed the same
        way, by attachment id, so re-submitting an already-logged photo on an
        unrelated field change doesn't create a duplicate row either."""
        tote = conn.execute("SELECT ph, orp FROM tote_lots WHERE id=?", (tote_lot_id,)).fetchone()
        current_for = {"ph": tote["ph"], "orp": tote["orp"]}
        for key, label in self.CHARACTERIZATION_LABELS:
            val = fd.get(key)
            if val in (None, ""):
                continue
            if key in current_for:
                old = current_for[key]
            else:
                last = conn.execute(
                    "SELECT new_value FROM tote_stability_log WHERE tote_lot_id=? AND field=?"
                    " ORDER BY id DESC LIMIT 1", (tote_lot_id, label)).fetchone()
                old = last["new_value"] if last else None
            if _fmtval(old) == _fmtval(val):
                continue
            self._log_stability(conn, tote_lot_id, user, label, old, val, note, run_id=run_id)
        for slot_key, label in (("surfacePhotoId", "Surface photo"),
                                 ("striationPhotoId", "Settling/striation photo")):
            att_id = fd.get(slot_key)
            if not att_id:
                continue
            last_photo = conn.execute(
                "SELECT attachment_id FROM tote_stability_log WHERE tote_lot_id=? AND field=?"
                " ORDER BY id DESC LIMIT 1", (tote_lot_id, label)).fetchone()
            if last_photo and last_photo["attachment_id"] == att_id:
                continue
            if run_id:
                att = conn.execute("SELECT filename FROM run_attachments WHERE id=? AND run_id=?",
                                   (att_id, run_id)).fetchone()
            else:
                att = conn.execute("SELECT filename FROM tote_attachments WHERE id=? AND tote_lot_id=?",
                                   (att_id, tote_lot_id)).fetchone()
            self._log_stability(conn, tote_lot_id, user, label, None,
                               att["filename"] if att else "Photo", note,
                               run_id=run_id, attachment_id=att_id)

    def _apply_ph_orp_override(self, conn, tote, fd):
        """pH/ORP captured on a characterization save become the tote's
        current reading (same columns Feedstock Inventory's own Update/pH
        flow writes to), so the main table and future 'From' values reflect
        it. Returns the (ph, ph_updated, orp, orp_updated) tuple to persist."""
        ph = numn(fd.get("ph")) if fd.get("ph") not in (None, "") else None
        orp = numn(fd.get("orp")) if fd.get("orp") not in (None, "") else None
        ph_updated = today_iso() if ph is not None else tote["ph_updated"]
        orp_updated = today_iso() if orp is not None else tote["orp_updated"]
        return (ph if ph is not None else tote["ph"], ph_updated,
                orp if orp is not None else tote["orp"], orp_updated)

    @staticmethod
    def _tote_available_for_run(conn, tote, run_id):
        """A tote may be put into a run only if it is in stock, already locked to THIS run, or was rejected from this run (a changed decision)."""
        st = tote["status"]
        if st == "in_stock":
            return True
        if st == "wip":
            return tote["run_id"] == run_id
        if st == "hold":
            return conn.execute("SELECT 1 FROM tote_stability_log WHERE tote_lot_id=? AND run_id=? AND field='Decision' AND new_value='rejected'",
                                (tote["id"], run_id)).fetchone() is not None
        return False

    def _apply_tote_characterization(self, conn, run_id, tote_lot_id, fd, user, processing_lot):
        """Shared by every path that locks a tote's Feedstock characterization
        into an in-progress run -- the card's own Save button, and the outer
        Save & close / draft-save flow. Every populated field (plus any
        photos) is written to the tote's permanent Feedstock Stability log,
        with pH/ORP overriding its current reading, exactly like Feedstock
        Inventory's own Update flow. An accepted tote also gets/keeps a
        run_inputs row and moves to status='wip' -- tied to this run via
        run_id -- so it can never be picked for another run until this one
        finishes (-> consumed) or is discarded (-> back to in_stock). A
        rejected tote is pulled out of the run instead: no run_inputs row,
        relocated to QAQC Hold. Returns True if rejected."""
        tote = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tote_lot_id,)).fetchone()
        if not tote:
            raise ApiError(404, "Tote not found")
        if not self._tote_available_for_run(conn, tote, run_id):
            raise ApiError(409, "Tote %s is not available for this run (status: %s%s)" % (
                tote["lot_number"], tote["status"], ", locked to another run or batch" if tote["status"] == "wip" else ""))
        fd = fd or {}
        decision = fd.get("decision") or "accepted"
        existing_input = conn.execute(
            "SELECT * FROM run_inputs WHERE run_id=? AND tote_lot_id=?",
            (run_id, tote_lot_id)).fetchone()

        if decision == "rejected":
            # Any characterization already locked in for this tote on this
            # run is superseded by the stability-log entries below.
            if existing_input:
                conn.execute("DELETE FROM run_inputs WHERE id=?", (existing_input["id"],))
            note = "Rejected from production run %s" % processing_lot
            self._log_characterization_fields(conn, tote_lot_id, user, fd, note, run_id=run_id)
            self._log_stability(conn, tote_lot_id, user, "Decision", None, "rejected", note, run_id=run_id)
            self._log_stability(conn, tote_lot_id, user, "Location", tote["location"],
                               QAQC_HOLD_LOCATION, note)
            self._log_stability(conn, tote_lot_id, user, "Status", tote["status"], "hold", note)
            ph, ph_updated, orp, orp_updated = self._apply_ph_orp_override(conn, tote, fd)
            conn.execute(
                "UPDATE tote_lots SET location=?, status='hold', ph=?, ph_updated=?, orp=?, orp_updated=?,"
                " run_id=NULL WHERE id=?",
                (QAQC_HOLD_LOCATION, ph, ph_updated, orp, orp_updated, tote_lot_id))
            return True

        # Accepted: log any captured fields, create/update this tote's
        # run_inputs row (same shape as finalize), and lock it to this run.
        note = "Characterized during production run %s" % processing_lot
        self._log_characterization_fields(conn, tote_lot_id, user, fd, note, run_id=run_id)
        if existing_input:
            input_id = existing_input["id"]
        else:
            cur = conn.cursor()
            cur.execute("INSERT INTO run_inputs (run_id,tote_lot_id) VALUES (?,?)", (run_id, tote_lot_id))
            input_id = cur.lastrowid
        self._apply_feedstock_detail(conn, input_id, fd, tote_lot_id)
        ph, ph_updated, orp, orp_updated = self._apply_ph_orp_override(conn, tote, fd)
        if tote["status"] != "wip":
            self._log_stability(conn, tote_lot_id, user, "Status", tote["status"], "wip", note, run_id=run_id)
        conn.execute(
            "UPDATE tote_lots SET status='wip', run_id=?, ph=?, ph_updated=?, orp=?, orp_updated=? WHERE id=?",
            (run_id, ph, ph_updated, orp, orp_updated, tote_lot_id))
        return False

    def save_feedstock_input(self, conn, run_id, tote_lot_id, user):
        """Per-tote 'lock in' Save for the Feedstock characterization card
        while a run is still in progress (draft) -- see
        _apply_tote_characterization for what actually happens."""
        run = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='draft'",
                           (run_id,)).fetchone()
        if not run:
            raise ApiError(404, "Draft not found")
        d = self._body_json()
        if d.get("decision") not in ("accepted", "rejected"):
            raise ApiError(400, "A decision (accepted/rejected) is required")
        rejected = self._apply_tote_characterization(conn, run_id, tote_lot_id, d, user, run["processing_lot"])
        return {"rejected": rejected}

    def release_feedstock_tote(self, conn, run_id, tote_lot_id, user):
        """Un-selecting a tote from an in-progress run's Feedstock section
        (the picker checkbox): discards any run_inputs row staged for it and
        releases it back to in_stock, available again for this or any other
        run. Also scrubs it out of the draft's own persisted selection right
        away, so a later Resume doesn't show it as still picked."""
        run = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='draft'",
                           (run_id,)).fetchone()
        if not run:
            raise ApiError(404, "Draft not found")
        tote = conn.execute("SELECT * FROM tote_lots WHERE id=? AND run_id=?",
                            (tote_lot_id, run_id)).fetchone()
        if not tote:
            raise ApiError(404, "Tote not found on this run")
        conn.execute("DELETE FROM run_inputs WHERE run_id=? AND tote_lot_id=?", (run_id, tote_lot_id))
        note = "Removed from production run %s" % run["processing_lot"]
        if tote["status"] == "wip":
            self._log_stability(conn, tote_lot_id, user, "Status", "wip", "in_stock", note, run_id=run_id)
        conn.execute("UPDATE tote_lots SET status='in_stock', run_id=NULL WHERE id=?", (tote_lot_id,))
        try:
            dd = json.loads(run["draft_data"] or "{}")
        except ValueError:
            dd = {}
        dd["toteIds"] = [t for t in (dd.get("toteIds") or []) if t != tote_lot_id]
        fdet = dd.get("feedstockDetails") or {}
        fdet.pop(str(tote_lot_id), None)
        dd["feedstockDetails"] = fdet
        conn.execute("UPDATE production_runs SET draft_data=? WHERE id=?", (json.dumps(dd), run_id))
        return {"ok": True}

    def _store_tote_attachment(self, conn, tote_id, filename, content_type, data_b64, uploaded_by):
        """Decode+store one base64 file and insert its tote_attachments row --
        the tote-scoped counterpart to _store_attachment (run-scoped)."""
        filename = (filename or "photo").strip().replace("\\", "/").split("/")[-1] or "photo"
        data_b64 = data_b64 or ""
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
        check_upload_filename(filename)
        self._check_storage_room(conn, len(raw))
        stored = self._write_upload(UPLOAD_DIR, raw, os.path.splitext(filename)[1][:12])
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tote_attachments (tote_lot_id,filename,content_type,size,stored_name,"
            "uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?)",
            (tote_id, filename, served_type(filename)[0], len(raw), stored,
             uploaded_by, now_iso()))
        return cur.lastrowid

    def upload_tote_photo(self, conn, tote_id, user):
        """Photo upload for Feedstock Inventory's Detail card -- a tote isn't
        tied to any production run there, so this stores into
        tote_attachments instead of run_attachments."""
        if not conn.execute("SELECT 1 FROM tote_lots WHERE id=?", (tote_id,)).fetchone():
            raise ApiError(404, "Tote not found")
        d = self._body_json()
        slot = d.get("slot")
        if slot not in ("surface", "striation"):
            raise ApiError(400, "Invalid photo slot")
        new_id = self._store_tote_attachment(conn, tote_id, d.get("filename") or "feedstock.jpg",
                                             d.get("contentType"), d.get("dataB64") or "",
                                             user["name"] if user else None)
        return {"attachmentId": new_id}

    def characterize_tote(self, conn, tote_id, user):
        """Feedstock Inventory's Detail card: the same characterization
        capture as a production run's Feedstock section, but for a tote on
        its own -- there's no run_inputs row to keep it in, so every field
        (plus photos) always lands on the tote's Feedstock Stability log.
        pH/ORP additionally become the tote's current reading. A rejected
        decision also relocates it to QAQC Hold, same as from a run."""
        tote = conn.execute("SELECT * FROM tote_lots WHERE id=?", (tote_id,)).fetchone()
        if not tote:
            raise ApiError(404, "Tote not found")
        d = self._body_json()
        decision = d.get("decision") or "accepted"
        if decision not in ("accepted", "rejected"):
            raise ApiError(400, "Invalid decision")
        note = ("Rejected via Feedstock Inventory" if decision == "rejected"
                else "Characterized via Feedstock Inventory")
        self._log_characterization_fields(conn, tote_id, user, d, note)
        ph, ph_updated, orp, orp_updated = self._apply_ph_orp_override(conn, tote, d)
        if decision == "rejected":
            self._log_stability(conn, tote_id, user, "Decision", None, "rejected", note)
            self._log_stability(conn, tote_id, user, "Location", tote["location"], QAQC_HOLD_LOCATION, note)
            self._log_stability(conn, tote_id, user, "Status", tote["status"], "hold", note)
            conn.execute(
                "UPDATE tote_lots SET location=?, status='hold', ph=?, ph_updated=?, orp=?, orp_updated=?"
                " WHERE id=?",
                (QAQC_HOLD_LOCATION, ph, ph_updated, orp, orp_updated, tote_id))
        else:
            conn.execute("UPDATE tote_lots SET ph=?, ph_updated=?, orp=?, orp_updated=? WHERE id=?",
                         (ph, ph_updated, orp, orp_updated, tote_id))
        return {"rejected": decision == "rejected",
                "tote": self._tote_with_last_updated(conn, conn.execute(
                    "SELECT * FROM tote_lots WHERE id=?", (tote_id,)).fetchone())}

    def upload_input_photo(self, conn, run_id, input_id, user):
        row = conn.execute("SELECT * FROM run_inputs WHERE id=? AND run_id=?",
                           (input_id, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Feedstock input not found")
        d = self._body_json()
        slot = d.get("slot")
        if slot not in ("surface", "striation"):
            raise ApiError(400, "Invalid photo slot")
        col = "surface_photo" if slot == "surface" else "striation_photo"
        tote = conn.execute("SELECT lot_number FROM tote_lots WHERE id=?", (row["tote_lot_id"],)).fetchone()
        label = "Surface" if slot == "surface" else "Settling-Striation"
        filename = d.get("filename") or ("%s %s.jpg" % (tote["lot_number"] if tote else "tote", label))
        new_id = self._store_attachment(conn, run_id, filename, d.get("contentType"),
                                        d.get("dataB64") or "", user["name"] if user else None)
        old_id = row[col]
        conn.execute("UPDATE run_inputs SET %s=? WHERE id=?" % col, (new_id, input_id))
        if old_id:
            self._delete_attachment_row(conn, old_id)
        return {"inputs": self._run_inputs_public(conn, run_id)}

    def feedstock_photo_draft(self, conn, run_id, user):
        """Upload a feedstock photo for a tote before it has a run_inputs row
        (still a draft) — stored as an ordinary attachment; the client stashes
        the returned id in feedstockDetails and it's copied in at finalize."""
        run = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='draft'",
                           (run_id,)).fetchone()
        if not run:
            raise ApiError(404, "Draft not found")
        d = self._body_json()
        slot = d.get("slot")
        if slot not in ("surface", "striation"):
            raise ApiError(400, "Invalid photo slot")
        new_id = self._store_attachment(conn, run_id, d.get("filename") or "feedstock.jpg",
                                        d.get("contentType"), d.get("dataB64") or "",
                                        user["name"] if user else None)
        return {"attachmentId": new_id}

    # ---- process stages (Homogenization / Extraction / Separation / ------- #
    # ---- Pasteurization / Packaging) ---------------------------------------#
    def save_stage(self, conn, rid, stage, user):
        if stage not in self.STAGE_FIELDS:
            raise ApiError(404, "Unknown stage")
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not run:
            raise ApiError(404, "Production run not found")
        d = self._body_json()
        updates = {}
        for col, key, kind in self.STAGE_FIELDS[stage]:
            if key not in d:
                continue
            if kind == "num":
                updates[col] = numn(d[key])
            elif kind == "bool":
                updates[col] = 1 if d[key] else 0
            else:
                updates[col] = (d[key] or "").strip() or None
        if updates:
            sets = ", ".join("%s=?" % c for c in updates)
            conn.execute("UPDATE production_runs SET %s WHERE id=?" % sets, (*updates.values(), rid))
            self._log_qc_field_changes(conn, rid, stage, updates, user["name"] if user else None)
        # The Packaging section's one Save button covers the packagedAt/QC/
        # Sample Point fields above *and* commits the Packaging table's net
        # container changes -- this is the "once changes have been saved"
        # moment the ledger reflects.
        if stage == "packaging":
            if run["status"] == "completed":
                self._ensure_packaging_baseline(conn, rid)
            self._commit_packaging_stock(conn, rid, user["name"] if user else None)
        # Likewise Dilution & Preservation's Save commits the net change in
        # citric acid / potassium sorbate / sodium benzoate used.
        if stage == "dilution":
            self._recompute_dilution_totals(conn, rid)
            self._commit_reagent_usage(conn, rid, user["name"] if user else None)
        return {"run": run_public(conn.execute(
            "SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone())}

    # ---- Dilution & Preservation: repeatable tank entries ------------------#
    def _dilutions_public(self, conn, run_id):
        out = []
        for r in conn.execute(
                "SELECT * FROM run_dilutions WHERE run_id=? ORDER BY created_at, id", (run_id,)):
            out.append({
                "id": r["id"], "tank": r["tank"], "volumeInitialL": r["volume_initial_l"],
                "waterRequiredL": r["water_required_l"], "volumeFinalL": r["volume_final_l"],
                "sorbateRequiredKg": r["sorbate_required_kg"],
                "benzoateRequiredKg": r["benzoate_required_kg"],
                "preservativesAdded": bool(r["preservatives_added"]),
                "preservativesAddedAt": r["preservatives_added_at"], "citricKg": r["citric_kg"],
                "samplesTaken": bool(r["samples_taken"]), "samplesTakenAt": r["samples_taken_at"],
                "notes": r["notes"], "createdAt": r["created_at"]})
        return out

    def _apply_dilution_fields(self, conn, did, d):
        updates = {}
        for col, key, kind in self.DILUTION_FIELDS:
            if key not in d:
                continue
            if kind == "num":
                updates[col] = numn(d[key])
            elif kind == "bool":
                updates[col] = 1 if d[key] else 0
            else:
                updates[col] = (d[key] or "").strip() or None
        if updates:
            sets = ", ".join("%s=?" % c for c in updates)
            conn.execute("UPDATE run_dilutions SET %s WHERE id=?" % sets, (*updates.values(), did))

    # ---- additional dilution passes ---------------------------------------- #
    PASS_FIELDS = [
        ("tank5a_l", "tank5aL", "num"), ("tank5b_l", "tank5bL", "num"), ("receiving", "receivingTanks", "text"),
        ("tank6a_start_l", "tank6aStartL", "num"), ("tank6b_start_l", "tank6bStartL", "num"),
        ("tds_pct", "tdsPct", "num"), ("max_transfer_l", "maxTransferL", "num"),
        ("transfer_rec_l", "recommendedTransferL", "num"), ("water_rec_l", "recommendedWaterL", "num"),
        ("product_transferred_l", "productTransferredL", "num"), ("water_added_l", "waterAddedL", "num"),
        ("tank6a_final_l", "tank6aFinalL", "num"), ("tank6b_final_l", "tank6bFinalL", "num"),
        ("ksorbate_added_l_6a", "ksorbateAddedL6a", "num"), ("ksorbate_added_l_6b", "ksorbateAddedL6b", "num"),
        ("nabenzoate_added_l_6a", "nabenzoateAddedL6a", "num"), ("nabenzoate_added_l_6b", "nabenzoateAddedL6b", "num"),
        ("final_variance_pct", "finalVariancePct", "num"),
        ("measured_ph", "measuredPh", "num"), ("citric_kg", "citricKg", "num"),
    ]

    def _dilution_passes_public(self, conn, run_id):
        out = []
        for r in conn.execute("SELECT * FROM run_dilution_passes WHERE run_id=? ORDER BY pass_no, id", (run_id,)):
            d = {"id": r["id"], "passNo": r["pass_no"]}
            for col, key, _kind in self.PASS_FIELDS:
                d[key] = r[col]
            out.append(d)
        return out

    def _recompute_dilution_totals(self, conn, run_id):
        """Run-level Ksorbate / sodium benzoate added (L) = pass 1 (its per-tank values) plus every
        additional pass, so the reagent deduction (which reads the run totals) covers all passes.
        A run still on the older single-total layout (no receiving tank chosen) is left alone."""
        r = conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
        if not r or not r["pasteurization_receiving"]:
            return
        extra = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(ksorbate_added_l_6a,0)+COALESCE(ksorbate_added_l_6b,0)),0) k,"
            " COALESCE(SUM(COALESCE(nabenzoate_added_l_6a,0)+COALESCE(nabenzoate_added_l_6b,0)),0) n"
            " FROM run_dilution_passes WHERE run_id=?", (run_id,)).fetchone()
        npasses = conn.execute("SELECT COUNT(*) c FROM run_dilution_passes WHERE run_id=?", (run_id,)).fetchone()["c"]

        def total(a, b, more):
            if a is None and b is None and not npasses:
                return None          # nothing entered yet: leave "not recorded", don't invent a 0
            return round((a or 0) + (b or 0) + more, 4)
        conn.execute("UPDATE production_runs SET dilution_ksorbate_added_l=?, dilution_nabenzoate_added_l=? WHERE id=?",
                     (total(r["dilution_ksorbate_added_l_6a"], r["dilution_ksorbate_added_l_6b"], extra["k"]),
                      total(r["dilution_nabenzoate_added_l_6a"], r["dilution_nabenzoate_added_l_6b"], extra["n"]), run_id))

    def _apply_pass_fields(self, conn, pid, d):
        updates = {}
        for col, key, kind in self.PASS_FIELDS:
            if key not in d:
                continue
            updates[col] = numn(d[key]) if kind == "num" else ((d[key] or "").strip() or None)
        if updates:
            sets = ", ".join("%s=?" % c for c in updates)
            conn.execute("UPDATE run_dilution_passes SET %s WHERE id=?" % sets, (*updates.values(), pid))

    def route_dilution_passes(self, method, seg, conn, user):
        rid = int(seg[2])
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not run:
            raise ApiError(404, "Production run not found")
        uname = user["name"] if user else None
        if len(seg) == 4 and method == "POST":
            if not run["pasteurization_receiving"]:
                raise ApiError(400, "Complete the first pass's dilution plan (receiving tanks) before adding another pass")
            n = conn.execute("SELECT COALESCE(MAX(pass_no),1) m FROM run_dilution_passes WHERE run_id=?", (rid,)).fetchone()["m"] + 1
            cur = conn.execute("INSERT INTO run_dilution_passes (run_id,pass_no,created_at) VALUES (?,?,?)", (rid, n, now_iso()))
            self._apply_pass_fields(conn, cur.lastrowid, self._body_json())
        elif len(seg) == 5 and seg[4].isdigit():
            pid = int(seg[4])
            row = conn.execute("SELECT * FROM run_dilution_passes WHERE id=? AND run_id=?", (pid, rid)).fetchone()
            if not row:
                raise ApiError(404, "Dilution pass not found")
            if method == "PUT":
                self._apply_pass_fields(conn, pid, self._body_json())
            elif method == "DELETE":
                last = conn.execute("SELECT MAX(pass_no) m FROM run_dilution_passes WHERE run_id=?", (rid,)).fetchone()["m"]
                if row["pass_no"] != last:
                    raise ApiError(400, "Only the most recent pass can be removed")
                conn.execute("DELETE FROM run_dilution_passes WHERE id=?", (pid,))
            else:
                raise ApiError(405, "Method not allowed")
        elif method != "GET":
            raise ApiError(405, "Method not allowed")
        if method != "GET":
            # run totals = pass 1 + all additional passes; the net change is deducted/refunded
            self._recompute_dilution_totals(conn, rid)
            self._commit_reagent_usage(conn, rid, uname)
        return {"dilutionPasses": self._dilution_passes_public(conn, rid)}

    def add_dilution(self, conn, run_id, user):
        cur = conn.cursor()
        cur.execute("INSERT INTO run_dilutions (run_id,created_at) VALUES (?,?)", (run_id, now_iso()))
        did = cur.lastrowid
        self._apply_dilution_fields(conn, did, self._body_json())
        return {"dilutions": self._dilutions_public(conn, run_id)}

    def update_dilution(self, conn, run_id, did, user):
        row = conn.execute("SELECT * FROM run_dilutions WHERE id=? AND run_id=?",
                           (did, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Dilution entry not found")
        self._apply_dilution_fields(conn, did, self._body_json())
        return {"dilutions": self._dilutions_public(conn, run_id)}

    def delete_dilution(self, conn, run_id, did):
        row = conn.execute("SELECT * FROM run_dilutions WHERE id=? AND run_id=?",
                           (did, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Dilution entry not found")
        conn.execute("DELETE FROM run_dilutions WHERE id=?", (did,))
        return {"dilutions": self._dilutions_public(conn, run_id)}

    # ---- Sample Point: repeatable sample-collection rows -------------------#
    def _sample_points_public(self, conn, run_id):
        return [{"id": r["id"], "stage": r["stage"], "type": r["type"], "description": r["description"],
                 "qty": r["qty"], "container": r["container"], "createdAt": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM run_sample_points WHERE run_id=? ORDER BY created_at, id", (run_id,))]

    def _apply_sample_point_fields(self, conn, spid, d):
        updates = {}
        for col, key, kind in self.SAMPLE_POINT_FIELDS:
            if key not in d:
                continue
            if kind == "int":
                updates[col] = sample_qty(d[key])
            else:
                updates[col] = (d[key] or "").strip() or None
        if updates:
            sets = ", ".join("%s=?" % c for c in updates)
            conn.execute("UPDATE run_sample_points SET %s WHERE id=?" % sets, (*updates.values(), spid))

    # The LKE characterization Sample Point (Dilution & Preservation) starts with these samples
    # on every new run: (type, description, qty, container). Container stock is consumed like any
    # other sample row (and refunded if the row or the draft is removed); a shortage never blocks
    # creating the run, it just shows as low/negative stock.
    DEFAULT_LKE_SAMPLES = [
        ("Liquid", "Microbial", 1, "50 mL falcon tube"),
        ("Liquid", "Metals & Nutrients", 2, "50 mL falcon tube"),
        ("Liquid", "Retention", 4, "50 mL falcon tube"),
        ("Liquid", "R&D", 2, "1 L bottle"),
    ]

    def _seed_default_samples(self, conn, run_id, lot, user):
        uname = user["name"] if user else None
        for typ, desc, qty, container in self.DEFAULT_LKE_SAMPLES:
            conn.execute("INSERT INTO run_sample_points (run_id,type,description,qty,container,stage,created_at)"
                         " VALUES (?,?,?,?,?,'packaging',?)", (run_id, typ, desc, qty, container, now_iso()))
            row = self._consumable_by_name(conn, container)
            if row:
                self._consume(conn, row["id"], -qty, "Sample point added (default)", lot, uname)

    # Sample Point defaults, applied when the operator CHANGES the description / type (never overriding a value sent
    # in the same request, and only when that container exists in Inventory Items):
    #   description Microbial          -> qty 1, 50 mL falcon tube
    #   description Metals & Nutrients -> qty 2, 50 mL falcon tube
    #   type Solid                     -> 100 g sample bag
    SAMPLE_FALCON = "50 mL falcon tube"
    SAMPLE_BAG = "100 g sample bag"

    def _sample_point_defaults(self, conn, old, d):
        explicit = set(d)
        d = dict(d)

        def has(name):
            return conn.execute("SELECT 1 FROM consumables WHERE name=?", (name,)).fetchone() is not None
        desc = (d.get("description") or "").strip() if "description" in d else None
        if desc and desc != (old["description"] if old else None):
            rule = {"Microbial": (1, self.SAMPLE_FALCON), "Metals & Nutrients": (2, self.SAMPLE_FALCON)}.get(desc)
            if rule:
                if "qty" not in explicit:
                    d["qty"] = rule[0]
                if "container" not in explicit and has(rule[1]):
                    d["container"] = rule[1]
        typ = (d.get("type") or "").strip() if "type" in d else None
        if typ == "Solid" and typ != (old["type"] if old else None):
            if "container" not in explicit and has(self.SAMPLE_BAG):
                d["container"] = self.SAMPLE_BAG
        return d

    def add_sample_point(self, conn, run_id, user):
        # A new row starts as Slurry / Microbial (what the dropdowns show), so the Microbial default
        # (qty 1, 50 mL falcon tube) applies right away and that container is consumed now.
        d = self._body_json()
        stage = (d.get("stage") or "").strip() or None
        d.setdefault("type", "Slurry")
        d.setdefault("description", "Microbial")
        d = self._sample_point_defaults(conn, None, d)
        cur = conn.cursor()
        cur.execute("INSERT INTO run_sample_points (run_id,qty,stage,created_at) VALUES (?,1,?,?)",
                    (run_id, stage, now_iso()))
        spid = cur.lastrowid
        self._apply_sample_point_fields(conn, spid, d)
        row = conn.execute("SELECT * FROM run_sample_points WHERE id=?", (spid,)).fetchone()
        if row["container"]:
            lot = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (run_id,)).fetchone()["processing_lot"]
            self._adjust_container_stock(conn, row["container"], -(row["qty"] or 1), "Sample point added", lot,
                                         user["name"] if user else None)
        return {"samplePoints": self._sample_points_public(conn, run_id)}

    def update_sample_point(self, conn, run_id, spid, user):
        row = conn.execute("SELECT * FROM run_sample_points WHERE id=? AND run_id=?",
                           (spid, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Sample point entry not found")
        d = self._sample_point_defaults(conn, row, self._body_json())
        old_container, old_qty = row["container"], row["qty"] or 0
        new_container = ((d["container"] or "").strip() or None) if "container" in d else old_container
        new_qty = old_qty
        if "qty" in d:
            q = sample_qty(d["qty"])
            new_qty = q if q is not None else old_qty
        lot = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (run_id,)).fetchone()["processing_lot"]
        uname = user["name"] if user else None
        if new_container == old_container:
            self._adjust_container_stock(conn, new_container, old_qty - new_qty, "Sample point updated", lot, uname)
        else:
            self._adjust_container_stock(conn, old_container, old_qty, "Sample point updated (container changed)", lot, uname)
            self._adjust_container_stock(conn, new_container, -new_qty, "Sample point updated (container changed)", lot, uname)
        self._apply_sample_point_fields(conn, spid, d)
        return {"samplePoints": self._sample_points_public(conn, run_id)}

    def delete_sample_point(self, conn, run_id, spid, user):
        row = conn.execute("SELECT * FROM run_sample_points WHERE id=? AND run_id=?",
                           (spid, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Sample point entry not found")
        lot = conn.execute("SELECT processing_lot FROM production_runs WHERE id=?", (run_id,)).fetchone()["processing_lot"]
        self._adjust_container_stock(conn, row["container"], row["qty"] or 0, "Sample point removed", lot,
                                      user["name"] if user else None)
        conn.execute("DELETE FROM run_sample_points WHERE id=?", (spid,))
        return {"samplePoints": self._sample_points_public(conn, run_id)}

    # ---- Packaging: repeatable container-unit/qty rows --------------------#
    def _packaging_entries_public(self, conn, run_id):
        return [{"id": r["id"], "containerUnit": r["container_unit"], "qty": r["qty"],
                 "createdAt": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM run_packaging_entries WHERE run_id=? ORDER BY created_at, id", (run_id,))]

    def _apply_packaging_entry_fields(self, conn, peid, d):
        updates = {}
        for col, key, kind in self.PACKAGING_ENTRY_FIELDS:
            if key not in d:
                continue
            if kind == "num":
                v = numn(d[key])
                if d[key] not in (None, "") and v is None:
                    raise ApiError(400, "Quantity must be a number")
                if v is not None and v < 0:
                    raise ApiError(400, "Quantity cannot be negative")
                updates[col] = v
            else:
                updates[col] = (d[key] or "").strip() or None
        if updates:
            sets = ", ".join("%s=?" % c for c in updates)
            conn.execute("UPDATE run_packaging_entries SET %s WHERE id=?" % sets, (*updates.values(), peid))

    # ---- packaging edits on a FINALIZED run ------------------------------- #
    # A draft's Packaging rows only count once committed (Save / finalize). A
    # finalized run's rows are the source of truth for everything derived from
    # them -- finished-goods lots, container + label stock, output litres -- so
    # every add/edit/remove there re-derives those in the SAME transaction
    # (a failure, e.g. not enough stock or units already shipped, rolls the
    # edit back). Without this the Product Release page / FG lots went stale.
    def _run_is_completed(self, conn, run_id):
        r = conn.execute("SELECT status FROM production_runs WHERE id=?", (run_id,)).fetchone()
        return bool(r and r["status"] == "completed")

    def _packaging_totals(self, conn, run_id):
        return {r["container_unit"]: (r["total"] or 0) for r in conn.execute(
            "SELECT container_unit, SUM(qty) total FROM run_packaging_entries"
            " WHERE run_id=? AND container_unit IS NOT NULL GROUP BY container_unit", (run_id,))}

    def _ensure_packaging_baseline(self, conn, run_id):
        """A finalized run with no commit rows (finalized before the commit
        ledger existed) already had its stock effects applied the old way:
        record today's totals as committed -- no stock movement -- so the next
        net-change commit only acts on the edit being made, not the whole run."""
        if conn.execute("SELECT 1 FROM run_packaging_commits WHERE run_id=?", (run_id,)).fetchone():
            return
        totals = self._packaging_totals(conn, run_id)
        run = conn.execute("SELECT sku_code FROM production_runs WHERE id=?", (run_id,)).fetchone()
        for unit, qty in totals.items():
            conn.execute("INSERT INTO run_packaging_commits (run_id,container_unit,committed_qty) VALUES (?,?,?)",
                         (run_id, unit, qty))
            label = conn.execute("SELECT id FROM consumables WHERE label_sku_code=? AND label_package=?",
                                 (run["sku_code"], unit)).fetchone() if run else None
            if label and qty and not conn.execute(
                    "SELECT 1 FROM run_label_commits WHERE run_id=? AND consumable_id=?", (run_id, label["id"])).fetchone():
                conn.execute("INSERT INTO run_label_commits (run_id,consumable_id,committed_qty) VALUES (?,?,?)",
                             (run_id, label["id"], qty))

    @staticmethod
    def _lot_status_for_run(run):
        st = run["release_state"]
        if st in (None, "legacy", "released"):
            return "on_hand"
        return "hold" if st == "rejected" else "pending_release"

    def _adjust_fg_lot(self, conn, run, unit, delta, unit_litres):
        lot_no = "%s-%s" % (run["processing_lot"], unit)
        lot = conn.execute("SELECT * FROM fg_lots WHERE run_id=? AND fg_lot_number=?", (run["id"], lot_no)).fetchone()
        if not lot:
            if delta > 0 and unit in unit_litres:
                conn.execute(
                    "INSERT INTO fg_lots (fg_lot_number,sku_code,run_id,package_size,qty,litres_each,produced_date,"
                    "tds,location,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (lot_no, run["sku_code"], run["id"], unit, delta, unit_litres[unit], run["run_date"],
                     run["target_tds"], run["location"], self._lot_status_for_run(run), now_iso()))
            return
        new_qty = (lot["qty"] or 0) + delta
        if new_qty < 0:
            raise ApiError(400, "Cannot reduce %s by %g: only %g unit(s) are still on hand (the rest were shipped, "
                                "moved or disposed). Resolve those first." % (unit, -delta, lot["qty"] or 0))
        status = lot["status"]
        if new_qty > 0 and status in ("sold", "disposed"):
            status = self._lot_status_for_run(run)
        conn.execute("UPDATE fg_lots SET qty=?, status=? WHERE id=?", (new_qty, status, lot["id"]))

    def _sync_completed_packaging(self, conn, run_id, user, before):
        """Re-derive everything computed from a finalized run's packaging rows."""
        uname = user["name"] if user else None
        self._commit_packaging_stock(conn, run_id, uname)     # container + FG-label stock, net change
        run = conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
        after = self._packaging_totals(conn, run_id)
        unit_litres = packaging_container_litres_map(conn)
        for unit in set(before) | set(after):
            delta = after.get(unit, 0) - before.get(unit, 0)
            if delta:
                self._adjust_fg_lot(conn, run, unit, delta, unit_litres)
        self._sync_completed_output(conn, run_id)

    def add_packaging_entry(self, conn, run_id, user):
        # On a DRAFT, rows are freely added/edited/removed and none of it
        # touches container stock; only committing (the Packaging section's
        # Save button, or finalize) nets the total per container against what
        # was last committed (see _commit_packaging_stock). On a FINALIZED run
        # every change is applied immediately -- see _sync_completed_packaging.
        completed = self._run_is_completed(conn, run_id)
        if completed:
            self._ensure_packaging_baseline(conn, run_id)
            before = self._packaging_totals(conn, run_id)
        d = self._body_json()
        default_unit = conn.execute(
            "SELECT name FROM consumables WHERE is_container=1 AND litres_each IS NOT NULL"
            " ORDER BY name LIMIT 1").fetchone()
        default_unit = default_unit["name"] if default_unit else None
        cur = conn.cursor()
        cur.execute("INSERT INTO run_packaging_entries (run_id,container_unit,qty,created_at) VALUES (?,?,1,?)",
                    (run_id, default_unit, now_iso()))
        peid = cur.lastrowid
        self._apply_packaging_entry_fields(conn, peid, d)
        if completed:
            self._sync_completed_packaging(conn, run_id, user, before)
        return {"packagingEntries": self._packaging_entries_public(conn, run_id)}

    def update_packaging_entry(self, conn, run_id, peid, user):
        row = conn.execute("SELECT * FROM run_packaging_entries WHERE id=? AND run_id=?",
                           (peid, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Packaging entry not found")
        completed = self._run_is_completed(conn, run_id)
        if completed:
            self._ensure_packaging_baseline(conn, run_id)
            before = self._packaging_totals(conn, run_id)
        d = self._body_json()
        self._apply_packaging_entry_fields(conn, peid, d)
        if completed:
            self._sync_completed_packaging(conn, run_id, user, before)
        return {"packagingEntries": self._packaging_entries_public(conn, run_id)}

    def delete_packaging_entry(self, conn, run_id, peid, user):
        row = conn.execute("SELECT * FROM run_packaging_entries WHERE id=? AND run_id=?",
                           (peid, run_id)).fetchone()
        if not row:
            raise ApiError(404, "Packaging entry not found")
        completed = self._run_is_completed(conn, run_id)
        if completed:
            self._ensure_packaging_baseline(conn, run_id)
            before = self._packaging_totals(conn, run_id)
        conn.execute("DELETE FROM run_packaging_entries WHERE id=?", (peid,))
        if completed:
            self._sync_completed_packaging(conn, run_id, user, before)
        return {"packagingEntries": self._packaging_entries_public(conn, run_id)}

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
        # An excluded run must say why (audit trail for the Yield & Usage report).
        if "exclude_from_stats" in updates or "exclude_reason" in updates:
            final_flag = updates.get("exclude_from_stats", run["exclude_from_stats"])
            final_reason = updates.get("exclude_reason", run["exclude_reason"])
            if final_flag and not (final_reason or "").strip():
                raise ApiError(400, "Enter a reason for excluding this run from the yield & usage analysis")
        if not updates:
            return {"run": run_public(run), "edits": self._run_edits(conn, rid), "changed": 0}
        # Locked once finalized: only the yield-analysis exclusion flag may change
        # without an amendment; run date, reagents, location, operators and notes
        # are production-log entries.
        if run["status"] == "completed" and [c for c in updates if c not in ("exclude_from_stats", "exclude_reason")]:
            if not self._open_amendment(conn, rid):
                raise ApiError(409, "This production run is finalized and its log is locked. Use \"Amend run\" "
                                    "(with a reason) to change production-log entries.", "amendment_required")
            if not user or not user["can_amend_log"]:
                raise ApiError(403, "Only users with the Production Log Amender permission can edit a run under amendment")

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

    def create_run(self, conn, user):
        return self._finalize_run(conn, self._body_json(), user=user)

    def _finalize_run(self, conn, d, existing=None, user=None):
        """Validate a run's inputs/outputs and apply the tote-consumption,
        consumable-deduction and FG-lot side effects. With `existing` (a draft
        row), converts it to status='completed' in place; otherwise inserts a
        brand-new completed run."""
        tote_ids = [int(x) for x in (d.get("toteIds") or [])]
        if not tote_ids:
            raise ApiError(400, "Select at least one stabilized tote to process")
        sku = (d.get("sku") or "").strip()
        sku_row = conn.execute("SELECT * FROM fg_skus WHERE code=?", (sku,)).fetchone()
        if not sku_row:
            raise ApiError(400, "Choose a product SKU")
        sku_species = [r["species_code"] for r in conn.execute(
            "SELECT species_code FROM fg_sku_species WHERE sku_code=?", (sku,))]
        species = sku_species[0] if len(sku_species) == 1 else None
        # Packaging entries are saved incrementally to their own table (like
        # Sample Point), not part of this request body -- read whatever's
        # already there for this run. A brand-new one-shot run (no `existing`
        # draft) has no run_id yet to have attached any to, so it's simply
        # empty in that path.
        packaging_entries = []
        if existing:
            packaging_entries = [dict(r) for r in conn.execute(
                "SELECT container_unit, qty FROM run_packaging_entries WHERE run_id=?", (existing["id"],))]
            # Finalizing implicitly "saves" the Packaging table too, in case
            # the operator never clicked its Save button -- the ledger still
            # only gets one net-change line per container, same as a normal save.
            self._commit_packaging_stock(conn, existing["id"], user["name"] if user else None, sku=sku)
            # Same for the Dilution & Preservation reagents (citric acid,
            # potassium sorbate, sodium benzoate).
            self._commit_reagent_usage(conn, existing["id"], user["name"] if user else None)
        target_tds = sku_row["tds_target"]  # fixed product spec, not user-entered
        citric = num(d.get("citricKg"))
        sorbate = num(d.get("sorbateKg"))
        location = (d.get("location") or "").strip() or None
        run_date = (d.get("runDate") or today_iso()).strip()
        notes = d.get("notes")
        operators = (d.get("operators") or "").strip() or None
        feedstock_details = d.get("feedstockDetails") or {}

        # Validate totes are available -- either never touched yet (in_stock)
        # or already locked to this same draft as WIP by an earlier
        # per-tote/draft save.
        rows = conn.execute(
            "SELECT * FROM tote_lots WHERE id IN (%s)" % ",".join("?" * len(tote_ids)),
            tote_ids).fetchall()
        if len(rows) != len(tote_ids):
            raise ApiError(400, "Some selected totes were not found")
        for r in rows:
            if r["status"] not in ("in_stock", "wip"):
                raise ApiError(400, "Tote %s is not available (status: %s)" % (r["lot_number"], r["status"]))
            if r["status"] == "wip" and r["run_id"] != (existing["id"] if existing else None):
                raise ApiError(409, "Tote %s is locked to another run or batch" % r["lot_number"])
        if existing:
            # a tote this draft locked as WIP that is missing from the request would stay locked to a finished run for good
            left_out = [t["lot_number"] for t in conn.execute(
                "SELECT lot_number FROM tote_lots WHERE run_id=? AND status='wip' AND id NOT IN (%s)" % ",".join("?" * len(tote_ids)),
                (existing["id"], *tote_ids))]
            if left_out:
                raise ApiError(409, "Tote(s) %s are locked to this run but were not included in the finalize request. "
                                    "Reload the run and try again." % ", ".join(left_out))

        # A tote marked rejected during characterization contributes nothing to
        # this run — no input weight, no consumption — but is still logged (see
        # the run_inputs insert below) as part of the receiving inspection.
        def _decision_for(tote_id):
            fd = feedstock_details.get(str(tote_id)) or feedstock_details.get(tote_id)
            return (fd or {}).get("decision", "accepted")
        accepted_ids = {r["id"] for r in rows if _decision_for(r["id"]) != "rejected"}
        accepted_rows = [r for r in rows if r["id"] in accepted_ids]
        if not accepted_rows:
            raise ApiError(400, "At least one accepted tote is required to process a run")
        input_kg = round(sum((r["avg_weight_kg"] or 0) for r in accepted_rows), 2)

        # Output litres = sum of packaged litres. The container stock itself
        # (including the "IBC" pool) was already deducted live as each
        # packaging entry was added/edited (see add/update/delete_packaging_
        # entry) -- ibc_used here is purely the display stat on the run
        # summary card, not a second consumption.
        unit_litres = packaging_container_litres_map(conn)
        output_litres = 0.0
        ibc_used = 0
        pack_totals = {}                              # container unit -> total qty (several rows may name the same container)
        for pe in packaging_entries:
            unit = pe["container_unit"]
            qty = num(pe["qty"])
            if unit not in unit_litres or qty <= 0:
                continue
            pack_totals[unit] = pack_totals.get(unit, 0) + qty
        for unit, qty in pack_totals.items():
            output_litres += unit_litres[unit] * qty
            if is_ibc_unit(unit):
                ibc_used += int(qty)
        output_litres = round(output_litres, 2)

        # Consumables (citric, sorbate): a shortage never blocks the run; on-hand goes negative.
        citric_row = self._consumable_by_name(conn, "Citric Acid")
        sorbate_row = self._consumable_by_name(conn, "Potassium Sorbate")

        # Processing lot number: reserved at draft creation (or, for a direct
        # one-shot run, right here) from the row's own id — see lot_number_for.
        ts = now_iso()
        cur = conn.cursor()
        if existing:
            run_id = existing["id"]
            lot = existing["processing_lot"]
            if lot.startswith("DRAFT-"):  # legacy placeholder never numbered — heal it now
                lot = lot_number_for(existing["created_at"], run_id)
            # citric_kg/sorbate_kg add the payload amounts (always 0 from the
            # SPA) onto whatever _commit_reagent_usage already accumulated
            # from Dilution & Preservation saves, instead of overwriting it.
            cur.execute(
                "UPDATE production_runs SET processing_lot=?, run_date=?, species_code=?, sku_code=?,"
                " input_kg=?, target_tds=?, output_litres=?, citric_kg=COALESCE(citric_kg,0)+?,"
                " sorbate_kg=COALESCE(sorbate_kg,0)+?, ibc_used=?,"
                " location=?, notes=?, operators=?, status='completed', draft_data=NULL,"
                " finalized_at=?, finalized_by=?, release_state='pending_review' WHERE id=?",
                (lot, run_date, species, sku, input_kg, target_tds, output_litres,
                 citric, sorbate, ibc_used, location, notes, operators, ts,
                 user["name"] if user else None, run_id))
        else:
            placeholder = "TEMP-" + secrets.token_hex(6)
            cur.execute(
                "INSERT INTO production_runs (processing_lot,run_date,species_code,sku_code,input_kg,"
                "target_tds,output_litres,citric_kg,sorbate_kg,ibc_used,location,notes,operators,"
                "status,created_at,finalized_at,finalized_by,release_state)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'completed', ?, ?, ?, 'pending_review')",
                (placeholder, run_date, species, sku, input_kg, target_tds, output_litres,
                 citric, sorbate, ibc_used, location, notes, operators, ts, ts,
                 user["name"] if user else None))
            run_id = cur.lastrowid
            lot = lot_number_for(ts, run_id)
            cur.execute("UPDATE production_runs SET processing_lot=? WHERE id=?", (lot, run_id))

        # Log every selected tote's receiving inspection, carrying over any
        # characterization staged while this was still a draft (photos already
        # uploaded as attachments) — but only *consume* the accepted ones. A
        # tote already WIP from an earlier per-tote/draft save already has a
        # run_inputs row (and its own stability-log trail) from that save --
        # reuse it here instead of inserting a second one for the same tote.
        # A rejected tote's _apply_feedstock_detail call relocates it to QAQC
        # Hold (status='hold') and gets no run_id.
        for r in rows:
            existing_input = conn.execute(
                "SELECT id FROM run_inputs WHERE run_id=? AND tote_lot_id=?", (run_id, r["id"])).fetchone()
            if existing_input:
                input_id = existing_input["id"]
            else:
                cur.execute("INSERT INTO run_inputs (run_id,tote_lot_id) VALUES (?,?)", (run_id, r["id"]))
                input_id = cur.lastrowid
            fd = feedstock_details.get(str(r["id"])) or feedstock_details.get(r["id"])
            self._apply_feedstock_detail(conn, input_id, fd, r["id"])
            if r["id"] in accepted_ids:
                cur.execute("UPDATE tote_lots SET status='consumed', run_id=? WHERE id=?", (run_id, r["id"]))

        # Deduct consumables.
        uname = user["name"] if user else None
        if citric and citric_row:
            self._consume(conn, citric_row["id"], -citric, "Production run", lot, uname)
        if sorbate and sorbate_row:
            self._consume(conn, sorbate_row["id"], -sorbate, "Production run", lot, uname)
        # The IBC totes the stabilized kelp was stored in are now emptied by
        # processing and return to the USED-IBC pool (one per tote actually
        # processed — a rejected tote's IBC was never emptied).
        used_row = self._consumable_by_name(conn, "Used 1,000 L IBC Tote")
        if used_row and accepted_rows:
            self._consume(conn, used_row["id"], len(accepted_rows), "Emptied by processing", lot, uname)

        # Create FG lots, one per container unit (rows naming the same container were added up above: the lot number is unique).
        fg_created = []
        for unit, qty in pack_totals.items():
            fg_lot = "%s-%s" % (lot, unit)
            cur.execute(
                "INSERT INTO fg_lots (fg_lot_number,sku_code,run_id,package_size,qty,litres_each,"
                "produced_date,tds,location,status,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?, 'pending_release', ?)",
                (fg_lot, sku, run_id, unit, qty, unit_litres[unit], run_date,
                 target_tds, location, ts))
            fg_created.append(fg_lot)
            # (FG labels were consumed with the containers by the packaging
            # commit above -- see _commit_label_stock.)

        # Required-field gate: every required production-log field must have a
        # value. Raising here rolls the whole finalize back (single transaction),
        # so a run missing data is never half-finalized.
        problems = self._required_problems(
            conn, conn.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone())
        if problems:
            raise ApiError(400, "Cannot finalize - required fields are missing:" + problems)
        self._add_revision(conn, run_id, "finalized", user, "Run finalized (original record)", [],
                           self._release_snapshot_hash(conn, run_id))
        # Hold the finished goods for release: the run enters the review queue
        # (its FG lots were created 'pending_release' above).
        release_log(conn, run_id, "submitted", user, capacity=None,
                    meaning="Production run finalized; finished goods held pending release.",
                    log_hash=self._release_snapshot_hash(conn, run_id),
                    detail={"to": "pending_review", "fgLots": fg_created})
        return {"processingLot": lot, "runId": run_id, "inputKg": input_kg,
                "outputLitres": output_litres, "fgLots": fg_created}

    # ---- production run drafts (save progress, resume, discard) ---------- #
    def list_drafts(self, conn):
        drafts = []
        for r in conn.execute("SELECT * FROM production_runs WHERE status='draft' ORDER BY id DESC"):
            dd = run_public(r)
            dd["toteLots"] = self._tote_lot_numbers(conn, dd["toteIds"])
            dd["dilutions"] = self._dilutions_public(conn, r["id"])
            dd["samplePoints"] = self._sample_points_public(conn, r["id"])
            dd["packagingEntries"] = self._packaging_entries_public(conn, r["id"])
            dd["progress"] = self._run_progress(conn, r)
            drafts.append(dd)
        return {"drafts": drafts}

    def _tote_lot_numbers(self, conn, ids):
        if not ids:
            return []
        rows = conn.execute(
            "SELECT lot_number FROM tote_lots WHERE id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall()
        return [r["lot_number"] for r in rows]

    def get_draft(self, conn, rid):
        r = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='draft'", (rid,)).fetchone()
        if not r:
            raise ApiError(404, "Draft not found")
        d = run_public(r)
        d["dilutions"] = self._dilutions_public(conn, rid)
        d["dilutionPasses"] = self._dilution_passes_public(conn, rid)
        d["samplePoints"] = self._sample_points_public(conn, rid)
        d["packagingEntries"] = self._packaging_entries_public(conn, rid)
        d["progress"] = self._run_progress(conn, r)
        return {"run": d}

    def save_draft(self, conn, rid, user):
        """Create (rid=None) or update (rid given) a run in progress.
        Beyond storing the snapshot, every selected tote gets the same
        lock-in treatment as the characterization card's own Save button
        (see _apply_tote_characterization) -- accepted totes move to WIP,
        tied to this run until it's finalized or discarded; a rejected one
        is pulled out of the selection entirely before it's persisted."""
        d = self._body_json()
        sku = (d.get("sku") or "").strip() or None
        species = None
        target_tds = None
        if sku:
            sku_row = conn.execute("SELECT * FROM fg_skus WHERE code=?", (sku,)).fetchone()
            if sku_row:
                target_tds = sku_row["tds_target"]
                sku_species = [r["species_code"] for r in conn.execute(
                    "SELECT species_code FROM fg_sku_species WHERE sku_code=?", (sku,))]
                species = sku_species[0] if len(sku_species) == 1 else None
        citric = num(d.get("citricKg"))
        sorbate = num(d.get("sorbateKg"))
        location = (d.get("location") or "").strip() or None
        run_date = (d.get("runDate") or today_iso()).strip()
        notes = d.get("notes")
        operators = (d.get("operators") or "").strip() or None
        tote_ids = [int(x) for x in (d.get("toteIds") or [])]
        feedstock_details = d.get("feedstockDetails") or {}

        if rid is None:
            cur = conn.cursor()
            placeholder = "TEMP-" + secrets.token_hex(6)
            ts = now_iso()
            cur.execute(
                "INSERT INTO production_runs (processing_lot,run_date,species_code,sku_code,"
                "target_tds,citric_kg,sorbate_kg,location,notes,operators,status,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?, 'draft', ?)",
                (placeholder, run_date, species, sku, target_tds, citric, sorbate,
                 location, notes, operators, ts))
            rid = cur.lastrowid
            # Reserved immediately — this is the run's permanent number, not a
            # placeholder that gets swapped out at finalize.
            conn.execute("UPDATE production_runs SET processing_lot=? WHERE id=?",
                         (lot_number_for(ts, rid), rid))
            self._seed_default_samples(conn, rid, lot_number_for(ts, rid), user)
        else:
            run = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
            if not run:
                raise ApiError(404, "Draft not found")
            if run["status"] != "draft":
                raise ApiError(409, "This run has already been finalized")
            # citric_kg / sorbate_kg / nabenzoate_kg are deliberately NOT written
            # here: the run's reagent totals are accumulated by
            # _commit_reagent_usage from Dilution & Preservation saves, and the
            # SPA's draft payload never carries them (it would reset them to 0).
            conn.execute(
                "UPDATE production_runs SET run_date=?, species_code=?, sku_code=?, target_tds=?,"
                " location=?, notes=?, operators=? WHERE id=?",
                (run_date, species, sku, target_tds,
                 location, notes, operators, rid))

        processing_lot = conn.execute(
            "SELECT processing_lot FROM production_runs WHERE id=?", (rid,)).fetchone()["processing_lot"]
        rejected_ids = []
        kept_ids = []
        for tid in tote_ids:
            fd = feedstock_details.get(str(tid)) or feedstock_details.get(tid) or {}
            if self._apply_tote_characterization(conn, rid, tid, fd, user, processing_lot):
                rejected_ids.append(tid)
            else:
                kept_ids.append(tid)
        feedstock_details = {k: v for k, v in feedstock_details.items() if int(k) not in rejected_ids}
        draft_data = json.dumps({"toteIds": kept_ids, "feedstockDetails": feedstock_details})
        conn.execute("UPDATE production_runs SET draft_data=? WHERE id=?", (draft_data, rid))
        return {"run": run_public(conn.execute(
            "SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()),
            "rejectedToteIds": rejected_ids}

    def delete_draft(self, conn, rid, user):
        r = conn.execute("SELECT * FROM production_runs WHERE id=? AND status='draft'", (rid,)).fetchone()
        if not r:
            raise ApiError(404, "Draft not found")
        # Any tote this draft had locked to WIP is released back to stock --
        # otherwise it'd be stuck WIP forever, tied to a run that no longer exists.
        note = "Production run %s discarded" % r["processing_lot"]
        for t in conn.execute("SELECT * FROM tote_lots WHERE run_id=? AND status='wip'", (rid,)):
            self._log_stability(conn, t["id"], user, "Status", "wip", "in_stock", note, run_id=rid)
            conn.execute("UPDATE tote_lots SET status='in_stock', run_id=NULL WHERE id=?", (t["id"],))
        # Any container stock this draft already consumed is refunded --
        # otherwise it'd stay consumed forever against a run that no longer
        # exists. Sample Point containers deduct live, so every current row
        # is refunded; the Packaging table only ever commits (deducts) a net
        # amount on Save/finalize, so only what's actually in
        # run_packaging_commits needs refunding -- any not-yet-saved edits in
        # run_packaging_entries never touched stock in the first place.
        uname = user["name"] if user else None
        for pc in conn.execute("SELECT container_unit, committed_qty FROM run_packaging_commits WHERE run_id=?", (rid,)):
            self._adjust_container_stock(conn, pc["container_unit"], pc["committed_qty"] or 0, note,
                                          r["processing_lot"], uname)
        for lc in conn.execute("SELECT consumable_id, committed_qty FROM run_label_commits WHERE run_id=?", (rid,)):
            if lc["committed_qty"]:
                self._consume(conn, lc["consumable_id"], lc["committed_qty"], note, r["processing_lot"], uname)
        for sp in conn.execute("SELECT container, qty FROM run_sample_points WHERE run_id=?", (rid,)):
            self._adjust_container_stock(conn, sp["container"], sp["qty"] or 0, note, r["processing_lot"], uname)
        # Reagents (citric acid / potassium sorbate / sodium benzoate) commit a
        # net amount on Dilution & Preservation Save/finalize, so refund
        # exactly what run_reagent_commits holds.
        for rc in conn.execute("SELECT reagent, committed_kg, consumable_id FROM run_reagent_commits WHERE run_id=?", (rid,)):
            row = (conn.execute("SELECT * FROM consumables WHERE id=?", (rc["consumable_id"],)).fetchone()
                   if rc["consumable_id"] else self._consumable_by_name(conn, rc["reagent"]))
            if row and rc["committed_kg"]:
                self._consume(conn, row["id"], rc["committed_kg"], note, r["processing_lot"], uname)
        self._files_to_remove += [os.path.join(UPLOAD_DIR, a["stored_name"]) for a in conn.execute(
            "SELECT stored_name FROM run_attachments WHERE run_id=?", (rid,))]
        conn.execute("DELETE FROM production_runs WHERE id=?", (rid,))
        return {"ok": True}

    def finalize_draft(self, conn, rid, user):
        existing = conn.execute("SELECT * FROM production_runs WHERE id=?", (rid,)).fetchone()
        if not existing:
            raise ApiError(404, "Draft not found")
        if existing["status"] != "draft":
            raise ApiError(409, "This run has already been finalized")
        return self._finalize_run(conn, self._body_json(), existing=existing, user=user)

    # ---- finished goods --------------------------------------------------- #
    FG_EDIT_STATUSES = ("on_hand", "hold", "sold")

    def _edit_fg_lot(self, conn, it, user):
        """PUT /api/fg/:id. Status, units on hand and TDS decide whether and how much product can be sold, so a change needs a
        manager (hold / un-hold: a Quality Manager; units, TDS, on-hand <-> sold: a Production or Quality Manager), a reason, and is
        written to the audit chain. A lot's location may be changed by anyone (and goes in the move log)."""
        d = self._body_json()
        if d.get("location"):
            self._ensure_location(conn, d.get("location"))
        changes = {}
        if "status" in d and d["status"] != it["status"]:
            new_status = d["status"]
            if new_status not in self.FG_EDIT_STATUSES:
                raise ApiError(400, "Status must be On hand, Hold or Sold (Pending Release and Disposed are set by their own processes)")
            if it["status"] not in self.FG_EDIT_STATUSES:
                label = "Pending Release" if it["status"] == "pending_release" else it["status"].replace("_", " ")
                raise ApiError(400, "This lot is %s: its status cannot be changed here" % label + (
                    " - use Product Release to review and release it" if it["status"] == "pending_release" else ""))
            run_state = conn.execute("SELECT release_state FROM production_runs WHERE id=?", (it["run_id"],)).fetchone()
            run_state = run_state["release_state"] if run_state else None
            if new_status == "on_hand" and run_state not in (None, "legacy", "released"):
                raise ApiError(400, "This lot's production run has not been released - use Product Release")
            changes["status"] = (it["status"], new_status)
        if "qty" in d:
            new_qty = numn(d["qty"])
            if new_qty is None or new_qty != new_qty or new_qty in (float("inf"), float("-inf")) or new_qty < 0:
                raise ApiError(400, "Units on hand must be a number, zero or more")
            if new_qty != it["qty"]:
                changes["qty"] = (it["qty"], new_qty)
        if "tds" in d:
            new_tds = numn(d["tds"])
            if new_tds is not None and (new_tds != new_tds or new_tds in (float("inf"), float("-inf")) or new_tds < 0):
                raise ApiError(400, "TDS must be a number, zero or more")
            if new_tds != it["tds"]:
                changes["tds"] = (it["tds"], new_tds)
        new_loc = it["location"]
        if "location" in d and (d["location"] or None) != it["location"]:
            new_loc = (d["location"] or "").strip() or None
        capacity = None
        if changes:
            hold_involved = "status" in changes and "hold" in changes["status"]
            if hold_involved:
                self._require_quality_manager(user, "place a finished-goods lot on hold or release it from hold")
            elif not self._is_manager(user):
                raise ApiError(403, "Only a Production Manager or Quality Manager can change a lot's status, units on hand or TDS")
            reason = (d.get("reason") or "").strip()
            if len(reason) < 3:
                raise ApiError(400, "Enter the reason for this change (it is recorded in the audit trail)")
            capacity = "Quality Manager" if self._is_quality_manager(user) else "Production Manager"
        conn.execute("UPDATE fg_lots SET qty=?, status=?, location=?, tds=? WHERE id=?",
                     (changes["qty"][1] if "qty" in changes else it["qty"], changes["status"][1] if "status" in changes else it["status"],
                      new_loc, changes["tds"][1] if "tds" in changes else it["tds"], it["id"]))
        if new_loc != it["location"]:
            self._log_move(conn, "fg", it["id"], it["fg_lot_number"], it["location"], new_loc, it["qty"], today_iso(), "Edited")
        if changes:
            release_log(conn, it["run_id"] or 0, "fg_lot_edited", user, capacity=capacity,
                        meaning="Finished-goods lot %s edited: %s." % (it["fg_lot_number"], ", ".join(sorted(changes))),
                        comment=(d.get("reason") or "").strip(),
                        detail={"lot": it["fg_lot_number"], "changes": {k: {"from": v[0], "to": v[1]} for k, v in changes.items()}})
        return {"fg": fg_public(conn.execute("SELECT * FROM fg_lots WHERE id=?", (it["id"],)).fetchone())}

    def route_fg(self, method, seg, query, conn, user):
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
                return self._edit_fg_lot(conn, it, user)
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
        # Validate every line against on-hand stock before committing anything. The same lot on several lines is ONE demand on that lot.
        wanted = {}
        for ln in raw_lines:
            qty = num(ln.get("qty"))
            try:
                fid = int(ln.get("fgLotId"))
            except (TypeError, ValueError):
                continue
            if qty <= 0 or qty != qty or qty == float("inf"):
                continue
            wanted[fid] = wanted.get(fid, 0) + qty
        prepared = []
        for fid, qty in wanted.items():
            fg = conn.execute("SELECT * FROM fg_lots WHERE id=?", (fid,)).fetchone()
            if not fg:
                continue
            if fg["status"] != "on_hand":
                raise ApiError(400, "%s cannot be shipped: it is not released for sale (status: %s)"
                               % (fg["fg_lot_number"], "Pending Release" if fg["status"] == "pending_release" else fg["status"]))
            rs = conn.execute("SELECT release_state FROM production_runs WHERE id=?", (fg["run_id"],)).fetchone()
            if rs and rs["release_state"] not in (None, "legacy", "released"):
                raise ApiError(400, "%s cannot be shipped: its production run has not been released" % fg["fg_lot_number"])
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
            # relative and conditional: stock can never go below zero, whatever else ran in between
            cur.execute("UPDATE fg_lots SET qty=qty-?, status=CASE WHEN qty-?<=0 THEN 'sold' ELSE status END WHERE id=? AND qty>=?",
                        (qty, qty, fg["id"], qty))
            if cur.rowcount != 1:
                raise ApiError(409, "%s no longer has %g units on hand" % (fg["fg_lot_number"], qty))
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
                    # Restocked units return to sellable stock only if the run is still released.
                    rs = conn.execute("SELECT release_state FROM production_runs WHERE id=?",
                                      (fg["run_id"],)).fetchone()
                    back = "on_hand" if (not rs or rs["release_state"] in (None, "legacy", "released")) else "pending_release"
                    # units coming back to a lot that was disposed in the meantime need a Quality decision: hold them
                    status = back if fg["status"] == "sold" and nq > 0 else ("hold" if fg["status"] == "disposed" and nq > 0 else fg["status"])
                    conn.execute("UPDATE fg_lots SET qty=?, status=? WHERE id=?", (nq, status, fg["id"]))
        elif old_status == "cancelled" and new_status != "cancelled":
            for ln in conn.execute("SELECT * FROM shipment_lines WHERE shipment_id=?", (r["id"],)):
                fg = conn.execute("SELECT * FROM fg_lots WHERE id=?", (ln["fg_lot_id"],)).fetchone()
                if fg:
                    if fg["status"] in ("pending_release", "hold"):
                        raise ApiError(400, "%s is not released for sale, so this shipment cannot be reinstated"
                                       % fg["fg_lot_number"])
                    nq = (fg["qty"] or 0) - (ln["qty"] or 0)
                    if nq < 0:
                        raise ApiError(400, "Only %g of %s on hand: this shipment (%g) cannot be reinstated"
                                       % (fg["qty"] or 0, fg["fg_lot_number"], ln["qty"] or 0))
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
                raise ApiError(404, "Item not found")
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
                                  "WHERE status='completed' AND sku_code=? AND run_date<?", (key, frm)).fetchone()["s"]
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
                                  "WHERE status='completed' AND sku_code=? AND run_date>=? AND run_date<=?",
                                  (key, frm, to)):
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

    # ---- Quality Control charts ------------------------------------------------------ #
    def _qc_can_set_limits(self, user):
        return bool(user and (user["role"] == "admin" or user["is_quality_manager"]))

    def route_qc_charts(self, method, seg, conn, user):
        if seg == ["api", "qc-charts"] and method == "GET":
            return self._qc_chart_data(conn, user)
        if seg == ["api", "qc-charts", "limits"] and method == "PUT":
            if not self._qc_can_set_limits(user):
                raise ApiError(403, "Only an administrator or a Quality Manager can set control limits")
            d = self._body_json()
            metric, section = str(d.get("metric") or ""), str(d.get("section") or "")
            if not re.fullmatch(r"[a-z]+:[a-z_0-9]+", metric) or section not in QC_SECTIONS:
                raise ApiError(400, "Unknown measurement or process section")

            def num(k):
                v = d.get(k)
                if v in (None, ""):
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    raise ApiError(400, "Limits must be numbers")
            lcl, ucl, center = num("lcl"), num("ucl"), num("center")
            if lcl is not None and ucl is not None and lcl >= ucl:
                raise ApiError(400, "The lower control limit must be below the upper control limit")
            if center is not None and ((lcl is not None and center < lcl) or (ucl is not None and center > ucl)):
                raise ApiError(400, "The centre line must sit between the control limits")
            note = (str(d.get("note") or "").strip()[:200]) or None
            who, now = (user["name"] if user else None), now_iso()
            if lcl is None and ucl is None and center is None:
                conn.execute("DELETE FROM qc_chart_limits WHERE metric=? AND section=?", (metric, section))
            else:
                conn.execute("INSERT OR REPLACE INTO qc_chart_limits (metric,section,lcl,ucl,center,note,set_by,set_at) VALUES (?,?,?,?,?,?,?,?)",
                             (metric, section, lcl, ucl, center, note, who, now))
            conn.execute("INSERT INTO qc_chart_limit_log (metric,section,lcl,ucl,center,note,set_by,set_at) VALUES (?,?,?,?,?,?,?,?)",
                         (metric, section, lcl, ucl, center, note, who, now))
            return {"ok": True}
        raise ApiError(404, "Unknown QC chart endpoint")

    def _qc_chart_data(self, conn, user):
        """Everything the Quality Control page charts: completed runs with their grouping attributes, every measurable value (process QC checks per
        section, feedstock per tote, laboratory results) as points keyed by run, specification reference lines, and the manually set control limits."""
        runs = conn.execute("SELECT * FROM production_runs WHERE status='completed' ORDER BY run_date, id").fetchall()
        sku_names = {r["code"]: r["name"] for r in conn.execute("SELECT code,name FROM fg_skus")}
        site_names = {r["code"]: r["name"] for r in conn.execute("SELECT code,name FROM sites")}
        sp_names = {r["code"]: (r["common"] or r["name"]) for r in conn.execute("SELECT * FROM species")}
        totes = {}
        for t in conn.execute("SELECT run_id, site_code, species_code, stabilization_method FROM tote_lots WHERE status='consumed' AND run_id IS NOT NULL"):
            totes.setdefault(t["run_id"], []).append(t)
        run_ids = {r["id"] for r in runs}
        run_out = []
        for r in runs:
            rt = totes.get(r["id"], [])
            run_out.append({
                "id": r["id"], "lot": r["processing_lot"], "date": r["run_date"], "month": (r["run_date"] or "")[:7], "sku": r["sku_code"],
                "skuName": sku_names.get(r["sku_code"], r["sku_code"]),
                "species": sorted({sp_names.get(t["species_code"], t["species_code"]) for t in rt if t["species_code"]}),
                "farms": sorted({site_names.get(t["site_code"], t["site_code"]) for t in rt if t["site_code"]}),
                "stabilization": sorted({t["stabilization_method"] for t in rt if t["stabilization_method"]}),
                "operators": r["operators"], "location": r["location"], "excluded": bool(r["exclude_from_stats"]), "excludeReason": r["exclude_reason"]})
        measures = {}

        def add(group, key, label, unit, nonneg, section, point):
            m = measures.setdefault(key, {"key": key, "group": group, "label": label, "unit": unit, "nonNegative": nonneg, "sections": {}})
            sec = m["sections"].setdefault(section, {"key": section, "label": QC_SECTIONS[section][0], "order": QC_SECTIONS[section][1], "points": []})
            sec["points"].append(point)
        # process QC checks (the production log's QC Check boxes) and a few process measurements
        for r in runs:
            for fid, stage, _sl, subtitle, lab, _unit in QC_FIELD_REGISTRY:
                v = r[fid]
                mk = QC_MEASURE_KEYS.get(lab)
                if v is None or not mk:
                    continue
                info = QC_MEASURE_INFO[mk]
                add("qc", "qc:" + mk, info[0], info[1], info[2], qc_section_of(stage, subtitle), {"r": r["id"], "v": float(v)})
            if r["dilution_measured_ph"] is not None:
                add("qc", "qc:ph", "pH", "", True, "dilution", {"r": r["id"], "v": float(r["dilution_measured_ph"])})
            if r["dilution_final_variance_pct"] is not None:
                info = QC_MEASURE_INFO["volume_variance"]
                add("qc", "qc:volume_variance", info[0], info[1], info[2], "dilution", {"r": r["id"], "v": float(r["dilution_final_variance_pct"])})
            if r["homog_tds_pct"] and r["extraction_tds_pct"] is not None:
                info = QC_MEASURE_INFO["extraction_eff"]
                add("qc", "qc:extraction_eff", info[0], info[1], info[2], "extraction",
                    {"r": r["id"], "v": round((r["extraction_tds_pct"] - r["homog_tds_pct"]) / r["homog_tds_pct"] * 100.0, 2)})
        # feedstock: one point per tote (several per run -- this is where within-run variation shows)
        for x in conn.execute("SELECT ri.run_id, ri.ph, ri.orp, t.lot_number FROM run_inputs ri JOIN tote_lots t ON t.id=ri.tote_lot_id "
                              "WHERE ri.decision!='rejected'"):
            if x["run_id"] not in run_ids:
                continue
            if x["ph"] is not None:
                add("feedstock", "feed:ph", "Feedstock pH (per tote)", "", True, "feedstock", {"r": x["run_id"], "v": float(x["ph"]), "n": x["lot_number"]})
            if x["orp"] is not None:
                add("feedstock", "feed:orp", "Feedstock ORP (per tote)", "mV", False, "feedstock", {"r": x["run_id"], "v": float(x["orp"]), "n": x["lot_number"]})
        # laboratory results (numeric ones; metals in ppm). A "<" result is a detection limit: kept, flagged, left out of the statistics.
        specs = {sp["code"]: sp for sp in conn.execute("SELECT * FROM coa_specs")}
        for x in conn.execute("SELECT * FROM lab_results WHERE voided_at IS NULL AND spec_code IS NOT NULL ORDER BY id"):
            sp = specs.get(x["spec_code"])
            if not sp or sp["basis"] in ("run", "absent") or x["value_num"] is None or x["run_id"] not in run_ids:
                continue
            v, unit = float(x["value_num"]), sp["unit"] or ""
            if sp["basis"] == "metal":
                f = COA_PPM_FACTORS.get((x["unit"] or "").strip().lower())
                if f is None:
                    continue
                v, unit = v * f, "ppm"
            add("lab", "lab:" + sp["code"], sp["name"], unit, True, "lab",
                {"r": x["run_id"], "v": v, "q": x["qualifier"] or "", "when": x["report_date"] or (x["entered_at"] or "")[:10], "n": x["report_number"]})
        # specification reference lines (the Certificate of Analysis limits, in the chart's unit)
        refs = {}
        rate = float(get_setting_value(conn, "coa_application_rate_kg_ha", 0) or 0)
        periods = float(get_setting_value(conn, "coa_application_periods", 1) or 1) or 1.0
        for code in ("ph", "tds"):
            sp = specs.get(code)
            if sp and (sp["min_val"] is not None or sp["max_val"] is not None):
                refs["qc:%s|packaging" % code] = {"min": sp["min_val"], "max": sp["max_val"], "label": "CoA specification"}
        for sp in specs.values():
            if sp["basis"] == "value":
                refs["lab:%s|lab" % sp["code"]] = {"min": sp["min_val"], "max": sp["max_val"], "exclusive": bool(sp["max_exclusive"]), "label": "CoA specification"}
            elif sp["basis"] == "metal" and sp["max_val"] and rate > 0:
                refs["lab:%s|lab" % sp["code"]] = {"max": sp["max_val"] * 1e6 / (rate * periods), "label": "CoA limit (as ppm)"}
        group_order = {"qc": 0, "feedstock": 1, "lab": 2}
        out_measures = []
        for m in sorted(measures.values(), key=lambda m: (group_order[m["group"]], m["label"].lower())):
            secs = sorted(m["sections"].values(), key=lambda sct: sct["order"])
            out_measures.append(dict(m, sections=secs))
        limits = {"%s|%s" % (r["metric"], r["section"]): {"lcl": r["lcl"], "ucl": r["ucl"], "center": r["center"], "note": r["note"], "setBy": r["set_by"], "setAt": r["set_at"]}
                  for r in conn.execute("SELECT * FROM qc_chart_limits")}
        history = {}
        for r in conn.execute("SELECT * FROM qc_chart_limit_log ORDER BY id DESC LIMIT 300"):
            h = history.setdefault("%s|%s" % (r["metric"], r["section"]), [])
            if len(h) < 6:
                h.append({"lcl": r["lcl"], "ucl": r["ucl"], "center": r["center"], "note": r["note"], "setBy": r["set_by"], "setAt": r["set_at"]})
        st = lambda k, d: float(get_setting_value(conn, k, d))
        return {"runs": run_out, "measures": out_measures, "refs": refs, "limits": limits, "history": history,
                "canSetLimits": self._qc_can_set_limits(user),
                "settings": {"sigma": st("qc_chart_sigma", 3), "minProvisional": int(st("qc_chart_min_n_provisional", 8)),
                             "minEstablished": int(st("qc_chart_min_n_established", 20)), "runLength": int(st("qc_chart_run_length", 7)),
                             "trendLength": int(st("qc_chart_trend_length", 6))}}

    # ---- Yield & Usage: observed conversion rates + consumption per run ------ #
    YU_DIMS = ("sku", "species", "farm", "stabilization", "harvest_month", "processing_month")
    YU_MIXED = "\x00MIXED"   # sorts first; a run with >1 distinct value of a ticked dimension

    @staticmethod
    def _yu_stats(values):
        vals = [v for v in values if v is not None]
        if not vals:
            return None
        return {"n": len(vals), "median": round(statistics.median(vals), 4),
                "min": round(min(vals), 4), "max": round(max(vals), 4)}

    @staticmethod
    def _yu_category(reason, is_container, label_sku):
        """Which usage family a ledger line belongs to, or None to ignore it
        (the Used-IBC inflow and harvest check-in aren't run consumption)."""
        r = reason or ""
        if r.startswith("Emptied by processing") or r.startswith("Harvest check-in"):
            return None
        if label_sku:
            return "label"
        if is_container:
            return "sample" if r.startswith("Sample point") else "packaging"
        return "reagent"

    def route_yield_usage(self, query, conn):
        """Read-only report over completed production runs: two conversion
        rates (process = output L / measured input kg; harvest = output L /
        stored batch-average input kg) and what each run consumed (from the
        consumable ledger, ref = processing lot), grouped by SKU x feedstock
        source. Excluded runs never count toward group stats."""
        frm = query.get("from", [""])[0]
        to = query.get("to", [""])[0]
        # groupBy is a comma-separated list of dimensions (empty = one "All runs"
        # group); order follows YU_DIMS, not the query, so titles are stable.
        raw_dims = query.get("groupBy", ["sku,species,farm"])[0]
        asked = {d.strip() for d in raw_dims.split(",") if d.strip() and d.strip() != "none"}  # "none": parse_qs drops a blank value
        bad = asked - set(self.YU_DIMS)
        if bad:
            raise ApiError(400, "Unknown groupBy dimension: %s" % ", ".join(sorted(bad)))
        dims = [d for d in self.YU_DIMS if d in asked]
        include_excluded = query.get("includeExcluded", ["0"])[0] in ("1", "true")
        min_runs = int(get_setting_value(conn, "yield_report_min_runs", 5))
        sql = "SELECT * FROM production_runs WHERE status='completed'"
        args = []
        if frm:
            sql += " AND run_date>=?"; args.append(frm)
        if to:
            sql += " AND run_date<=?"; args.append(to)
        runs = conn.execute(sql + " ORDER BY run_date, id", args).fetchall()
        sku_names = {r["code"]: r["name"] for r in conn.execute("SELECT code,name FROM fg_skus")}
        site_names = {r["code"]: r["name"] for r in conn.execute("SELECT code,name FROM sites")}
        sp_names = {r["code"]: (r["common"] or r["name"]) for r in conn.execute("SELECT * FROM species")}

        def chunks(seq, n=500):
            for i in range(0, len(seq), n):
                yield seq[i:i + n]

        ids = [r["id"] for r in runs]
        totes, measured, usage_rows = {}, {}, {}
        for part in chunks(ids):
            ph = ",".join("?" * len(part))
            for t in conn.execute(
                    "SELECT id, run_id, site_code, species_code, stabilization_method, checkin_date FROM tote_lots"
                    " WHERE status='consumed' AND run_id IN (%s)" % ph, part):
                totes.setdefault(t["run_id"], []).append(t)
            for ri in conn.execute(
                    "SELECT ri.run_id, ri.tote_lot_id, ri.weight_kg FROM run_inputs ri"
                    " JOIN tote_lots t ON t.id=ri.tote_lot_id AND t.run_id=ri.run_id AND t.status='consumed'"
                    " WHERE ri.run_id IN (%s) AND ri.decision='accepted'" % ph, part):
                measured.setdefault(ri["run_id"], {})[ri["tote_lot_id"]] = ri["weight_kg"]
        lot_to_id = {r["processing_lot"]: r["id"] for r in runs}
        lots = list(lot_to_id)
        for part in chunks(lots):
            ph = ",".join("?" * len(part))
            for u in conn.execute(
                    "SELECT t.ref, t.reason, t.delta, c.id cid, c.name, c.unit, c.is_container, c.label_sku_code"
                    " FROM consumable_txns t JOIN consumables c ON c.id=t.consumable_id"
                    " WHERE t.ref IN (%s)" % ph, part):
                cat = self._yu_category(u["reason"], u["is_container"], u["label_sku_code"])
                if not cat:
                    continue
                slot = usage_rows.setdefault(lot_to_id[u["ref"]], {}).setdefault(
                    (u["cid"], cat), {"item": u["name"], "unit": u["unit"], "category": cat, "net": 0.0})
                slot["net"] += -u["delta"]

        run_out = []
        for r in runs:
            rt = totes.get(r["id"], [])
            pairs = sorted({(t["site_code"], t["species_code"]) for t in rt})
            sites = sorted({p[0] for p in pairs if p[0]})
            species = sorted({p[1] for p in pairs if p[1]})
            mixed = len(pairs) > 1
            meas = measured.get(r["id"], {})
            weights = [meas.get(t["id"]) for t in rt]
            process_kg = (round(sum(weights), 2) if rt and all(w is not None for w in weights) else None)
            harvest_kg = r["input_kg"] or 0
            out_l = r["output_litres"] or 0
            u_items = [dict(item=v["item"], unit=v["unit"], category=v["category"], amount=round(v["net"], 4))
                       for v in usage_rows.get(r["id"], {}).values()]
            flags = []
            if not rt:
                flags.append("no_source")
            if rt and process_kg is None:
                flags.append("missing_measured_weight")
            if mixed:
                flags.append("mixed")
            if not u_items:
                flags.append("no_usage_data")
            if harvest_kg <= 0 or out_l <= 0:
                flags.append("no_output")
            excluded = bool(r["exclude_from_stats"])
            # Harvest date = when the totes were checked in (a range if the run
            # blended several days); processing date = when the run was
            # finalized, falling back to the run date for runs finalized before
            # finalized_at was recorded (marked estimated).
            h_dates = sorted({t["checkin_date"] for t in rt if t["checkin_date"]})
            fin = r["finalized_at"]
            run_out.append({
                "id": r["id"], "lot": r["processing_lot"], "date": r["run_date"], "sku": r["sku_code"],
                "harvestDateFrom": h_dates[0] if h_dates else None,
                "harvestDateTo": h_dates[-1] if h_dates else None,
                "processingDate": fin[:10] if fin else r["run_date"],
                "processingDateEstimated": not fin,
                "finalPh": r["packaging_qc_ph"], "finalTds": r["packaging_tds_pct"],
                # Extraction efficiency (%) = (TDS after extraction - TDS before) / TDS before,
                # using the Extraction Performance and Homogenization Lot characterization TDS.
                "tdsBeforeExtraction": r["homog_tds_pct"], "tdsAfterExtraction": r["extraction_tds_pct"],
                "extractionEfficiency": (round((r["extraction_tds_pct"] - r["homog_tds_pct"]) / r["homog_tds_pct"] * 100, 1)
                                         if r["homog_tds_pct"] and r["extraction_tds_pct"] is not None else None),
                "farms": sites, "species": species, "mixed": mixed,
                "stabilization": sorted({t["stabilization_method"] for t in rt if t["stabilization_method"]}),
                "harvestKg": round(harvest_kg, 2), "processKg": process_kg, "outputL": round(out_l, 2),
                "harvestRate": round(out_l / harvest_kg, 3) if harvest_kg > 0 and out_l > 0 else None,
                "processRate": round(out_l / process_kg, 3) if process_kg and out_l > 0 else None,
                "weightDiffPct": (round((process_kg - harvest_kg) / harvest_kg * 100, 1)
                                  if process_kg is not None and harvest_kg > 0 else None),
                "excluded": excluded, "excludeReason": r["exclude_reason"], "flags": flags,
                "usage": u_items})

        # ---- group (excluded runs are listed but never counted) ----
        def dim_values(run, dim):
            if dim == "sku":
                return [run["sku"]]
            if dim == "species":
                return run["species"]
            if dim == "farm":
                return run["farms"]
            if dim == "stabilization":
                return run["stabilization"]
            if dim == "harvest_month":
                return sorted({d[:7] for d in (run["harvestDateFrom"], run["harvestDateTo"]) if d})
            return [run["processingDate"][:7]] if run["processingDate"] else []

        def group_key(run):
            key = []
            for dim in dims:
                vals = dim_values(run, dim)
                key.append(self.YU_MIXED if len(vals) > 1 else (vals[0] if vals else None))
            return tuple(key)

        def dim_label(dim, val):
            if val == self.YU_MIXED:
                return "Mixed"
            if val is None:
                return "Not recorded"
            if dim == "sku":
                return sku_names.get(val, val)
            if dim == "species":
                return sp_names.get(val, val)
            if dim == "farm":
                return site_names.get(val, val)
            return val

        buckets = {}
        for run in run_out:
            if not run["excluded"]:
                buckets.setdefault(group_key(run), []).append(run)
        groups = []
        for key, grp in sorted(buckets.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
            labels = [dim_label(d, v) for d, v in zip(dims, key)]
            mixed = self.YU_MIXED in key
            with_usage = [g for g in grp if g["usage"]]
            items = {}
            for g in with_usage:
                for u in g["usage"]:
                    if u["amount"] <= 0:
                        continue
                    items.setdefault((u["item"], u["category"], u["unit"]), []).append((g, u["amount"]))
            usage = []
            # reagents, then packaging (incl. sample containers), then finished-goods labels
            usage_order = {"reagent": 0, "packaging": 1, "sample": 1, "label": 2}
            for (item, cat, unit), pairs_ in sorted(items.items(), key=lambda kv: (usage_order.get(kv[0][1], 9), kv[0][0])):
                usage.append({
                    "item": item, "category": cat, "unit": unit,
                    "usedIn": len(pairs_), "ofRuns": len(with_usage),
                    "total": round(sum(a for _g, a in pairs_), 4),
                    "perKLOutput": self._yu_stats([a / g["outputL"] * 1000 for g, a in pairs_ if g["outputL"] > 0]),
                    "perTonneProcess": self._yu_stats([a / g["processKg"] * 1000 for g, a in pairs_ if g["processKg"]]),
                    "perTonneHarvest": self._yu_stats([a / g["harvestKg"] * 1000 for g, a in pairs_ if g["harvestKg"] > 0])})
            proc = [g for g in grp if g["processRate"] is not None]
            harv = [g for g in grp if g["harvestRate"] is not None]
            groups.append({
                "dims": dict(zip(dims, labels)), "title": " · ".join(labels) if labels else "All runs",
                "mixed": mixed,
                "runs": len(grp), "lowSample": len(grp) < min_runs,
                "harvestKg": round(sum(g["harvestKg"] for g in grp), 2),
                "processKg": round(sum(g["processKg"] for g in proc), 2),
                "outputL": round(sum(g["outputL"] for g in grp), 2),
                "processRate": dict(self._yu_stats([g["processRate"] for g in proc]) or {},
                                    pooled=(round(sum(g["outputL"] for g in proc) / sum(g["processKg"] for g in proc), 3)
                                            if proc and sum(g["processKg"] for g in proc) else None)) if proc else None,
                "harvestRate": dict(self._yu_stats([g["harvestRate"] for g in harv]) or {},
                                    pooled=(round(sum(g["outputL"] for g in harv) / sum(g["harvestKg"] for g in harv), 3)
                                            if harv and sum(g["harvestKg"] for g in harv) else None)) if harv else None,
                "usageRuns": len(with_usage), "usage": usage})
        shown = run_out if include_excluded else [r for r in run_out if not r["excluded"]]
        for r in shown:
            r["skuName"] = sku_names.get(r["sku"], r["sku"])
            r["farmNames"] = [site_names.get(s, s) for s in r["farms"]]
            r["speciesNames"] = [sp_names.get(s, s) for s in r["species"]]
        return {"from": frm or None, "to": to or None, "groupBy": dims, "minRuns": min_runs,
                "includeExcluded": include_excluded, "excludedCount": sum(1 for r in run_out if r["excluded"]),
                "groups": groups, "runs": shown}

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
            "COALESCE(SUM(citric_kg),0) ck, COALESCE(SUM(sorbate_kg),0) sk, "
            "COALESCE(SUM(nabenzoate_kg),0) nk "
            "FROM production_runs WHERE status='completed' AND run_date>=? AND run_date<=?", (start, end)).fetchone()
        prod_by_sku = rows(
            "SELECT sku_code sku, COUNT(*) runs, COALESCE(SUM(output_litres),0) litres "
            "FROM production_runs WHERE status='completed' AND run_date>=? AND run_date<=? GROUP BY sku_code",
            (start, end))

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
            "WHERE status='completed' AND run_date<=? GROUP BY sku_code", (asof,))}
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
                "nabenzoateKg": round(p["nk"] or 0, 1),
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
                if not t or t["status"] not in ("in_stock", "hold"):
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
                self._consume(conn, c["id"], -qty, "Disposal: " + reason, None, user["name"] if user else None)
                log("consumable", c["id"], c["name"], qty, c["unit"])
                disposed += 1
            if not any_qty:
                raise ApiError(400, "Enter a quantity to write off for at least one item")
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


def sample_qty(v):
    """A Sample Point's number of containers: a whole number from 1 to 10 (larger / smaller values are clamped), None when blank."""
    if v in (None, ""):
        return None
    f = numn(v)
    if f is None:
        raise ApiError(400, "Quantity must be a number")
    return max(1, min(10, int(f)))


def is_ibc_unit(unit):
    """True for an IBC tote container ('IBC', '1,000 L IBC', ...): ibc_used counts these."""
    return "IBC" in (unit or "").upper()


def integrity_message(e):
    """A plain-language 409 message for a sqlite3.IntegrityError (the raw text names tables and columns)."""
    text = str(e)
    if text.startswith("Not allowed:"):
        return text                                   # raised by our own trigger (ensure_constraints): already in plain words
    if text.startswith("UNIQUE"):
        return "That number or name is already in use, so the change was not saved. Check for a duplicate and try again."
    if text.startswith("FOREIGN KEY"):
        return "Other records still refer to this item (or the item it refers to no longer exists), so the change was not saved."
    return "The change conflicts with existing data and was not saved."


def num(v, default=0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default       # "nan" / "inf" parse as floats but are never a usable quantity


def numn(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


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


def yield_usage_workbook(data):
    """Two-sheet .xlsx of the Yield & Usage report: the grouped stats and the
    per-run detail (every column the page shows, all three usage bases)."""
    T = lambda v, s=0: ("t", v, s)
    N = lambda v, s=6: (("n", v, s) if v is not None else ("t", "—", 0))
    H = lambda v: ("t", v, 3)
    HR = lambda v: ("t", v, 9)
    sheets = []
    # One label column holding the group title (the ticked dimensions joined);
    # the second column is kept blank-free by folding it into the first.
    DIM_LABEL = {"sku": "Product", "species": "Species", "farm": "Farm", "stabilization": "Stabilization",
                 "harvest_month": "Harvest month", "processing_month": "Processing month"}
    grouped = ", ".join(DIM_LABEL[d] for d in data["groupBy"]) or "None (all runs)"
    rng = lambda a, b: (a if a == b else "%s to %s" % (a, b)) if a else "—"

    s = XlsxSheet("Yield & Usage"); s.set_widths([40, 8, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12])
    s.title("Yield & Usage -- completed, non-excluded runs", 14)
    s.row([T("Range", 7), T("%s to %s" % (data["from"] or "start", data["to"] or "today"))])
    s.row([T("Grouped by", 7), T(grouped)])
    s.section("Conversion rates (L output per kg input)", 14)
    s.row([H("Group"), HR("Runs"), H("Sample"),
           HR("Process n"), HR("Process median"), HR("Process min"), HR("Process max"), HR("Process pooled"),
           HR("Harvest n"), HR("Harvest median"), HR("Harvest min"), HR("Harvest max"), HR("Harvest pooled")])
    for g in data["groups"]:
        pr, hr = g["processRate"] or {}, g["harvestRate"] or {}
        s.row([T(g["title"]), N(g["runs"], 4), T("Low sample" if g["lowSample"] else "OK"),
               N(pr.get("n"), 4), N(pr.get("median")), N(pr.get("min")), N(pr.get("max")), N(pr.get("pooled")),
               N(hr.get("n"), 4), N(hr.get("median")), N(hr.get("min")), N(hr.get("max")), N(hr.get("pooled"))])
    # Usage is grouped Reagents / Packaging / Finished-goods labels. Reagents keep the
    # median/min/max statistics (rounded to 0.1); packaging and labels are whole-unit
    # totals only (rounded to the nearest 1).
    R1 = lambda v: N(round(v, 1), 5) if v is not None else N(None)
    R0 = lambda v: N(round(v), 4) if v is not None else N(None)
    s.section("Reagents (net kg / L consumed per run, from the ledger; rounded to 0.1)", 14)
    s.row([H("Group"), H("Item"), H("Unit"), HR("Used in"), HR("Of runs"), HR("Total"),
           HR("/1000 L out median"), HR("min"), HR("max"),
           HR("/1000 kg process median"), HR("min"), HR("max")])
    for g in data["groups"]:
        for u in g["usage"]:
            if u["category"] != "reagent":
                continue
            ko, tp = u["perKLOutput"] or {}, u["perTonneProcess"] or {}
            s.row([T(g["title"]), T(u["item"]), T(u["unit"]),
                   N(u["usedIn"], 4), N(u["ofRuns"], 4), R1(u["total"]),
                   R1(ko.get("median")), R1(ko.get("min")), R1(ko.get("max")),
                   R1(tp.get("median")), R1(tp.get("min")), R1(tp.get("max"))])
    s.section("Reagents per 1000 kg harvest input", 14)
    s.row([H("Group"), H("Item"), HR("median"), HR("min"), HR("max")])
    for g in data["groups"]:
        for u in g["usage"]:
            if u["category"] != "reagent":
                continue
            th = u["perTonneHarvest"] or {}
            s.row([T(g["title"]), T(u["item"]), R1(th.get("median")), R1(th.get("min")), R1(th.get("max"))])
    for title, cats in (("Packaging (total count used)", ("packaging", "sample")),
                        ("Finished-goods labels (total count used)", ("label",))):
        s.section(title, 14)
        s.row([H("Group"), H("Item"), H("Unit"), HR("Used in"), HR("Of runs"), HR("Total")])
        for g in data["groups"]:
            for u in g["usage"]:
                if u["category"] in cats:
                    s.row([T(g["title"]), T(u["item"]), T(u["unit"]), N(u["usedIn"], 4), N(u["ofRuns"], 4), R0(u["total"])])
    sheets.append(s)

    s = XlsxSheet("Runs"); s.set_widths([22, 16, 22, 22, 22, 22, 16, 12, 12, 12, 12, 12, 10, 10, 14, 10, 26, 30])
    s.title("Runs", 18)
    s.row([H("Lot"), H("Processing date"), H("Harvest date"), H("Product"), H("Farm"), H("Species"),
           H("Stabilization"), HR("Harvest kg"), HR("Process kg"), HR("Output L"),
           HR("Harvest rate"), HR("Process rate"), HR("Final pH"), HR("Final TDS (%)"), HR("Extraction efficiency (%)"),
           H("Excluded"), H("Reason"), H("Flags")])
    for r in data["runs"]:
        s.row([T(r["lot"]),
               T(r["processingDate"] + (" (est.)" if r["processingDateEstimated"] else "")),
               T(rng(r["harvestDateFrom"], r["harvestDateTo"])), T(r["skuName"]),
               T(" / ".join(r["farmNames"]) or "—"), T(" / ".join(r["speciesNames"]) or "—"),
               T(" / ".join(r["stabilization"]) or "—"),
               N(r["harvestKg"], 5), N(r["processKg"], 5), N(r["outputL"], 5), N(r["harvestRate"]), N(r["processRate"]),
               N(r["finalPh"], 6), N(r["finalTds"], 6), N(r["extractionEfficiency"], 6),
               T("Yes" if r["excluded"] else ""), T(r["excludeReason"] or ""), T(", ".join(r["flags"]))])
    sheets.append(s)
    return xlsx_build(sheets)


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
                           ("Citric acid (kg)", pr["citricKg"], 5), ("Potassium sorbate (kg)", pr["sorbateKg"], 5),
                           ("Sodium benzoate (kg)", pr["nabenzoateKg"], 5)]:
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

    s = XlsxSheet("Reagents"); s.set_widths([26, 10, 12, 12, 16])
    s.title("Reagents", 5)
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
    s.section("Reagents / packaging", 3)
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
    s.row([T("Reagent / packaging write-offs", 7), N(dz.get("consumableEvents", 0))])
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
def boot_summary():
    """One line for the log: which database, how big, and how much is in it (ASCII only)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        counts = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("users", "production_runs", "tote_lots", "fg_lots")}
        conn.close()
        return "Database: %s (%.1f MB): %s" % (DB_PATH, os.path.getsize(DB_PATH) / 1048576.0, ", ".join("%d %s" % (v, k.replace("_", " ")) for k, v in counts.items()))
    except Exception as e:  # pragma: no cover
        return "Database: %s (could not be summarised: %s)" % (DB_PATH, e)


def main():
    configure_logging()
    try:
        init_db()
    except Exception:
        logger.critical("The server could not start: the database failed to initialise.", exc_info=True)
        raise SystemExit(1)
    logger.info(boot_summary())
    print("KelpWorks ERP running at http://%s:%s  (DB: %s)  [%s%s]" % (HOST, PORT, DB_PATH, ENV_NAME.upper(), ", restore enabled" if ALLOW_RESTORE else ""))
    if DEV_MODE and ADMIN_PASSWORD == "kelp1234":
        print("Development login (new database): %s / kelp1234" % ADMIN_EMAIL)
    if SECRET_SOURCE != "environment":
        print("NOTE: KELP_ERP_SECRET is not set; using a generated signing key (%s). Set KELP_ERP_SECRET in production." % SECRET_SOURCE)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    stop_backups = threading.Event()
    if NIGHTLY_BACKUP:
        threading.Thread(target=backup_scheduler, args=(stop_backups,), daemon=True, name="nightly-backup").start()
        logger.info("Nightly backup is on: after %02d:00 UTC, keeping the newest %d on this disk.", BACKUP_HOUR_UTC, BACKUP_KEEP)
    else:
        logger.info("Nightly backup is off (KELP_ERP_NIGHTLY_BACKUP=1 turns it on).")
    if 0 < len(ARCHIVE_KEY) < ARCHIVE_KEY_MIN:
        logger.warning("KELP_ERP_ARCHIVE_KEY is shorter than %d characters and is ignored.", ARCHIVE_KEY_MIN)

    def stop(signum, _frame):
        logger.info("Stop requested (signal %s): finishing the requests in progress.", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()      # shutdown() must not run in the thread that serves
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, stop)
    try:
        server.serve_forever()
    finally:
        stop_backups.set()
        deadline = time.time() + SHUTDOWN_WAIT_SECONDS
        while INFLIGHT[0] > 0 and time.time() < deadline:
            time.sleep(0.1)
        server.server_close()
        logger.info("Server stopped.")


if __name__ == "__main__":
    main()

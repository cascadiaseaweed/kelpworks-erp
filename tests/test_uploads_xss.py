"""Uploads, downloads and the browser-facing headers (risk review batch 3: R-05, R-25).

A user can upload files and an admin clicks View, so what a file is served as must never come from the uploader.
"""
import base64
import os
import re
import urllib.error
import urllib.request

import pytest

from factories import finalize_run, finished_run, harvest_totes
from harness import ROOT, Server


def b64(data):
    return base64.b64encode(data).decode("ascii")


def upload(client, run_id, filename, content=b"hello", content_type="application/octet-stream"):
    return client.post("/api/production/%d/attachments" % run_id, {"filename": filename, "contentType": content_type, "dataB64": b64(content)})


def fetch(server, client, url_path):
    """GET a path with the token in ?token= (how the app opens documents) and return (status, headers, body)."""
    req = urllib.request.Request("%s%s%stoken=%s" % (server.url, url_path, "&" if "?" in url_path else "?", client.token))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


@pytest.fixture(scope="module")
def run_id(srv, admin):
    return finished_run(admin)


def attachment_id(response):
    return max(a["id"] for a in response.json["attachments"])      # the one just uploaded


# ---- R-05: what an upload is served as ----

@pytest.mark.parametrize("name", ["evil.html", "page.htm", "image.svg", "script.js", "data.xml", "run.exe", "EVIL.HTML"])
def test_script_capable_files_cannot_be_uploaded(srv, admin, run_id, name):
    r = upload(admin, run_id, name, b"<script>alert(1)</script>", "text/html")
    assert r.status == 400 and "cannot be uploaded" in r.json["error"]


def test_the_client_supplied_type_is_ignored(srv, admin, run_id):
    r = upload(admin, run_id, "notes.txt", b"plain text", "text/html")            # claims to be html
    assert r.status == 200, r.text
    status, headers, body = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, attachment_id(r)))
    assert status == 200 and headers["Content-Type"] == "text/plain"
    assert headers["Content-Disposition"].startswith("attachment")                # a text file is a download, not a page


def test_an_unknown_extension_is_an_opaque_download(srv, admin, run_id):
    r = upload(admin, run_id, "file.weird", b"x", "text/html")
    status, headers, _ = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, attachment_id(r)))
    assert headers["Content-Type"] == "application/octet-stream" and headers["Content-Disposition"].startswith("attachment")


def test_pdfs_and_images_still_open_in_the_browser(srv, admin, run_id):
    r = upload(admin, run_id, "report.pdf", b"%PDF-1.4 test", "text/html")
    status, headers, _ = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, attachment_id(r)))
    assert headers["Content-Type"] == "application/pdf" and headers["Content-Disposition"].startswith("inline")
    r = upload(admin, run_id, "photo.PNG", b"\x89PNG", "application/x-evil")
    status, headers, _ = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, attachment_id(r)))
    assert headers["Content-Type"] == "image/png"
    status, headers, _ = fetch(srv, admin, "/api/production/%d/attachments/%d/download?dl=1" % (run_id, attachment_id(r)))
    assert headers["Content-Disposition"].startswith("attachment")                # ?dl=1 always downloads


def test_a_stored_file_that_was_uploaded_as_html_before_the_fix_is_still_served_safely(srv, admin, run_id):
    """Rows written by the old code kept whatever type the client sent. The type is now taken from the file name, never from the row."""
    r = upload(admin, run_id, "old.txt", b"<script>alert(1)</script>")
    aid = attachment_id(r)
    import sqlite3
    conn = sqlite3.connect(srv.db_path)
    conn.execute("UPDATE run_attachments SET filename='old.html', content_type='text/html' WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    status, headers, _ = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, aid))
    assert headers["Content-Type"] == "application/octet-stream" and headers["Content-Disposition"].startswith("attachment")


def test_sop_documents_cannot_be_html_either(srv, admin):
    r = admin.post("/api/sop-documents", {"name": "Bad SOP", "filename": "sop.html", "contentType": "text/html", "dataB64": b64(b"<b>x</b>")})
    assert r.status == 400


# ---- R-25: headers ----

def test_a_content_type_with_line_breaks_cannot_inject_headers(srv, admin, run_id):
    r = upload(admin, run_id, "inject.txt", b"x", "text/plain\r\nSet-Cookie: pwn=1\r\nX-Injected: yes")
    status, headers, _ = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, attachment_id(r)))
    assert status == 200 and "Set-Cookie" not in headers and "X-Injected" not in headers


def test_filenames_with_non_ascii_characters_download_correctly(srv, admin, run_id):
    name = "Report – Lab é中.pdf"
    r = upload(admin, run_id, name, b"%PDF-1.4")
    status, headers, body = fetch(srv, admin, "/api/production/%d/attachments/%d/download" % (run_id, attachment_id(r)))
    assert status == 200 and body == b"%PDF-1.4"
    disposition = headers["Content-Disposition"]
    assert "filename*=UTF-8''Report%20%E2%80%93%20Lab%20%C3%A9%E4%B8%AD.pdf" in disposition
    assert '"' not in disposition.split("filename=")[1].split(";")[0].strip('"')


def test_every_response_carries_the_browser_protection_headers(srv, admin):
    for response in (srv.client().get("/"), srv.client().get("/api/env"), admin.get("/api/me")):
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "same-origin"
        assert "Access-Control-Allow-Origin" not in response.headers               # the app is same-origin: no other site may read it
    assert "Strict-Transport-Security" not in srv.client().get("/").headers          # plain http (local): no HSTS
    https = srv.client().request("GET", "/api/env", headers={"X-Forwarded-Proto": "https"})
    assert "max-age" in https.headers.get("Strict-Transport-Security", "")


def test_the_app_page_has_a_content_security_policy(srv):
    csp = srv.client().get("/").headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp and "object-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]        # no inline script


def test_the_app_has_no_inline_script_and_the_print_windows_escape_their_data():
    with open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8") as f:
        js = f.read()
    with open(os.path.join(ROOT, "public", "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert "window.onload=" not in js and "<script>" not in js                  # the CSP would block them; printing is driven by printWhenReady
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    for needle in ("escHtml(s.notes)", "escHtml(cust.name", "escHtml(lb.lot)", "escHtml(c)}</td>", "escHtml(ln.lot)"):
        assert needle in js, needle


# ---- R-25: disk ----

def test_the_document_store_has_a_size_limit():
    with Server(env={"KELP_ERP_MAX_UPLOADS_MB": "1"}) as s:
        a = s.admin_client()
        rid = finished_run(a)
        assert upload(a, rid, "one.txt", b"x" * 600_000).status == 200
        full = upload(a, rid, "two.txt", b"x" * 600_000)
        assert full.status == 507 and "document store is full" in full.json["error"]


def uploads_dir_files(s):
    d = os.path.join(s.tmp, "uploads")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_discarding_a_draft_run_removes_its_uploaded_files(srv, admin):
    before = len(uploads_dir_files(srv))
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    assert upload(admin, rid, "temp.txt", b"temporary").status == 200
    assert len(uploads_dir_files(srv)) == before + 1
    assert admin.delete("/api/production/drafts/%d" % rid).status == 200
    assert len(uploads_dir_files(srv)) == before


def test_a_failed_upload_leaves_no_file_behind(srv, admin):
    before = len(uploads_dir_files(srv))
    r = upload(admin, 99999, "orphan.txt", b"nobody owns me")                    # the row cannot be written (no such run)
    assert not r.ok
    assert len(uploads_dir_files(srv)) == before

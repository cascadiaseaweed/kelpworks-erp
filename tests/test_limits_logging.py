"""Request limits, bounded file parsing and server logging (risk review batch 4: R-08, R-09, R-10)."""
import base64
import io
import os
import socket
import time
import zipfile
import zlib

import pytest

from factories import finished_run
from harness import ADMIN_EMAIL, Server
import kelp_erp_server as K


def b64(data):
    return base64.b64encode(data).decode("ascii")


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


def raw_request(server, head, body=b"", read_timeout=10):
    """Send raw bytes and return what the server answers (status line + headers + body), to test what it does BEFORE reading a body."""
    with socket.create_connection(("127.0.0.1", server.port), timeout=read_timeout) as s:
        s.sendall(head + body)
        chunks = []
        try:
            while True:
                data = s.recv(65536)
                if not data:
                    break
                chunks.append(data)
        except (socket.timeout, ConnectionError):
            pass
        return b"".join(chunks)


# ---- R-09: request limits ----

def test_login_accepts_only_a_small_body(srv):
    r = srv.client().post("/api/auth/login", {"email": ADMIN_EMAIL, "password": "x" * 20_000})
    assert r.status == 413


def test_a_huge_declared_body_is_refused_without_reading_it(srv):
    start = time.time()
    answer = raw_request(srv, b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 900000000\r\n\r\n")
    assert b" 413 " in answer.split(b"\r\n", 1)[0] and time.time() - start < 5            # answered at once, no wait for 900 MB


def test_a_negative_content_length_is_rejected(srv):
    answer = raw_request(srv, b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Length: -5\r\n\r\n")
    assert b" 400 " in answer.split(b"\r\n", 1)[0]


def test_an_authenticated_request_over_the_limit_is_refused(srv, admin, run_id):
    head = ("POST /api/production/%d/attachments HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nAuthorization: Bearer %s\r\n"
            "Content-Length: %d\r\n\r\n" % (run_id, admin.token, 41 * 1024 * 1024)).encode("ascii")
    answer = raw_request(srv, head)                                                    # declared 41 MB; the body is never sent or read
    assert b" 413 " in answer.split(b"\r\n", 1)[0] and b"too large" in answer


def test_a_client_that_goes_silent_mid_request_is_dropped():
    with Server(env={"KELP_ERP_SOCKET_TIMEOUT": "2"}) as s:
        start = time.time()
        with socket.create_connection(("127.0.0.1", s.port), timeout=15) as sock:
            sock.sendall(b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n")      # promises a body that never comes
            try:
                data = sock.recv(1024)
            except (socket.timeout, ConnectionError):
                data = None
        assert data in (b"", None) or b" 408 " in data or b" 400 " in data
        assert time.time() - start < 10                         # dropped after about the 2 s timeout, not held open forever
        assert s.client().get("/api/env").status == 200          # and the server carries on


# ---- R-10: bounded parsing of untrusted files ----

def xlsx_with_cell(ref):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="S" sheetId="1"/></sheets></workbook>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1"><v>1</v></c><c r="%s"><v>2</v></c></row></sheetData></worksheet>' % ref)
    return buf.getvalue()


def docx_with_body(xml_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml_bytes)
    return buf.getvalue()


W = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body></w:document>'


def upload(client, rid, name, content):
    r = client.post("/api/production/%d/attachments" % rid, {"filename": name, "contentType": "application/octet-stream", "dataB64": b64(content)})
    r.raise_for_status()
    return max(a["id"] for a in r.json["attachments"])


def preview(client, rid, aid):
    start = time.time()
    r = client.get("/api/production/%d/attachments/%d/preview" % (rid, aid))
    return r, time.time() - start


def test_a_tiny_spreadsheet_with_a_far_away_cell_previews_quickly_and_small(srv, admin, run_id):
    aid = upload(admin, run_id, "bomb.xlsx", xlsx_with_cell("ZZ20000"))            # used to take 12 s and return 126 MB
    r, took = preview(admin, run_id, aid)
    assert r.status == 200 and took < 3 and len(r.body) < 200_000
    assert "first 1000 rows" in r.json["html"]


def test_a_normal_spreadsheet_and_document_still_preview(srv, admin, run_id):
    r, _ = preview(admin, run_id, upload(admin, run_id, "small.xlsx", xlsx_with_cell("C3")))
    assert r.status == 200 and "<td>2</td>" in r.json["html"]
    r, _ = preview(admin, run_id, upload(admin, run_id, "small.docx", docx_with_body(W % b"<w:p><w:r><w:t>Hello world</w:t></w:r></w:p>")))
    assert r.status == 200 and "Hello world" in r.json["html"]


def test_a_document_that_unpacks_to_hundreds_of_megabytes_is_refused_quickly(srv, admin, run_id):
    xml = W % (b"<w:p><w:r><w:t>x</w:t></w:r></w:p>" * 1_000_000)                      # about 36 MB of XML, a few hundred KB zipped
    aid = upload(admin, run_id, "bomb.docx", docx_with_body(xml))
    r, took = preview(admin, run_id, aid)
    assert r.status == 200 and "too large to preview" in r.json["html"] and took < 3 and len(r.body) < 10_000


def test_zip_read_stops_at_the_limit():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.xml", b"0" * (K.MAX_ZIP_MEMBER_BYTES + 1000))
        z.writestr("ok.xml", b"fine")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
        assert K.zip_read(z, "ok.xml") == b"fine"
        with pytest.raises(ValueError):
            K.zip_read(z, "big.xml")


def test_a_pdf_stream_that_inflates_enormously_is_dropped():
    bomb = zlib.compress(b"\0" * (200 * 1024 * 1024), 9)                                  # about 200 KB compressed
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode /Length " + str(len(bomb)).encode() + b" >>\nstream\n" + bomb + b"\nendstream\nendobj\n%%EOF"
    start = time.time()
    objs = K._pdf_objects(pdf)
    assert objs[1][1] == b"" and time.time() - start < 3                                  # nothing 200 MB large is ever built
    assert K._inflate(zlib.compress(b"hello"), 1000) == b"hello"


# ---- R-08: logging ----

def wait_for_log(server, needle, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        text = server.log_tail(200)
        if needle in text:
            return text
        time.sleep(0.2)
    return server.log_tail(200)


def test_the_start_is_logged_with_the_database_and_its_size(srv):
    log = wait_for_log(srv, "Database:")
    assert "Database:" in log and "users" in log and "MB" in log


def test_a_server_error_gives_a_reference_and_leaves_a_traceback_in_the_log(srv, admin):
    r = admin.get("/api/samples?runId=abc")                                              # an unexpected failure (a non-numeric id)
    assert r.status == 500
    message = r.json["error"]
    assert "reference " in message and "invalid literal" not in message                  # no raw exception text for the user
    ref = message.split("reference ")[1].split(")")[0]
    log = wait_for_log(srv, ref)
    assert ref in log and "Traceback" in log and "ValueError" in log


def test_tokens_never_reach_the_log(srv, admin):
    admin.get("/api/reports/xlsx?token=%s" % admin.token)
    log = wait_for_log(srv, "token=REDACTED")
    assert "token=REDACTED" in log and admin.token not in log


def test_api_requests_are_logged_with_their_result(srv, admin):
    admin.get("/api/me")
    assert "GET /api/me -> 200" in wait_for_log(srv, "GET /api/me -> 200")


def test_a_failed_start_says_why_in_the_log():
    junk = os.path.join(os.environ.get("TEMP", "."), "kelp_not_a_db.db")
    with open(junk, "wb") as f:
        f.write(b"this is not a sqlite database" * 50)
    try:
        with pytest.raises(RuntimeError) as err:
            Server(db_path=junk, startup_timeout=15).start()
        assert "could not start" in str(err.value) and "DatabaseError" in str(err.value)
    finally:
        os.remove(junk)

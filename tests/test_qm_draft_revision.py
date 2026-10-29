"""KW-71: tools/qm_draft_revision.py turns the register entries the Quality Manager has approved into a DRAFT revision of the Quality Manual: a change list (.docx) and, from a draft copy of the manual,
a copy with each change as a Word tracked change. It never touches the controlled copy, writes only into the drafts folder, never overwrites, and checks its own result (rejecting every tracked change
gives the original text back; accepting them all holds every proposed wording). Standard library only; every test runs in a temporary folder with a synthetic manual."""
import hashlib
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree

import pytest

from test_changelog_storage import NAME, cw, env, make, row, run  # noqa: F401  (the fixtures and helpers of the storage tests)
from test_qm001_tracking import GOOD_ENTRY, decide, entry, qm, register  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import qm_draft_revision as gen  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DATE = "2026-10-29T12:00:00Z"


def run_xml(text, bold=False):
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else '<w:rPr><w:rFonts w:ascii="Calibri"/></w:rPr>'
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, text)


def p(*runs, style=None):
    ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else "<w:pPr><w:spacing w:after=\"120\"/></w:pPr>"
    return "<w:p>%s%s</w:p>" % (ppr, "".join(runs))


BODY = [
    p(run_xml("2.2.2 User roles"), style="Heading2"),
    p(run_xml("Only a Quality "), run_xml("Manager", bold=True), run_xml(" may place a hold.")),                                  # the sentence is split over three runs
    p(run_xml("The ‘hold’ rule – applies to “all” lots.")),                                            # curly quotes and an en dash
    '<w:tbl><w:tr><w:tc>%s</w:tc></w:tr></w:tbl>' % p(run_xml("Production Manager signs the review.")),                           # a paragraph inside a table cell
    p(run_xml("Stock never blocks a run.")),
    p(run_xml("Stock never blocks a run.")),                                                                                      # repeated: cannot be placed
    p(run_xml("Total: "), '<w:r><w:fldChar w:fldCharType="begin"/></w:r>', '<w:r><w:instrText>PAGE</w:instrText></w:r>', '<w:r><w:fldChar w:fldCharType="end"/></w:r>'),   # a field: not edited
    p(run_xml("2.3 Records are kept for two years.")),                                                                            # the anchor of an addition
    p(run_xml("Revision history is kept by the document owner.")),
]
DOCUMENT = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s><w:body>%s<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>' % (W, "".join(BODY))


def make_manual(path, document=DOCUMENT):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="x"/></Types>')
        z.writestr("word/document.xml", document)
        z.writestr("word/header1.xml", "<w:hdr %s><w:p><w:r><w:t>QM-001 DRAFT</w:t></w:r></w:p></w:hdr>" % W)
        z.writestr("word/media/logo.png", b"\x89PNG-not-really")


def e(change="Replace", before="", after="", ref="C1", insert_after="", section="2.2.2", items="KW-01", reason="because"):
    return {"Ref": ref, "Change": change, "Before": before, "After": after, "Insert after": insert_after, "Manual section / clause": section, "KW items": items, "Reason": reason,
            "Decided By": "N. Wrana", "Decision Date": "2026-10-29"}


def build(*entries):
    return gen.build_tracked(DOCUMENT, list(entries), DATE)


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


# ---- matching and editing --------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_replacement_across_several_runs_is_a_tracked_change_that_can_be_rejected_and_accepted():
    new, results = build(e(before="Only a Quality Manager may place a hold.", after="Only a Quality Manager may place or lift a hold."))
    assert results == [{"ref": "C1", "applied": True, "reason": "replaced in place"}]
    ElementTree.fromstring(new.encode("utf-8"))
    assert "<w:del " in new and "<w:ins " in new and 'w:author="KelpWorks draft"' in new and '<w:delText xml:space="preserve">Manager</w:delText>' in new
    assert gen.texts_of(new, "reject") == gen.texts_of(DOCUMENT, "plain")                                                         # reject all: the original, character for character
    assert "Only a Quality Manager may place or lift a hold." in gen.texts_of(new, "accept")
    gen.check_result(DOCUMENT, new, results, [e(before="x", after="Only a Quality Manager may place or lift a hold.")])


def test_everything_outside_the_changed_paragraph_is_untouched_byte_for_byte():
    new, _ = build(e(before="Only a Quality Manager may place a hold.", after="Something else."))
    old_paras, new_paras = [m[3] for m in gen.paragraphs(DOCUMENT)], [m[3] for m in gen.paragraphs(new)]
    assert len(old_paras) == len(new_paras) and [i for i, (a, b) in enumerate(zip(old_paras, new_paras)) if a != b] == [1]
    assert new.startswith(DOCUMENT[:DOCUMENT.index("Only a Quality")].rsplit("<w:p>", 1)[0])


def test_a_partial_match_keeps_the_formatting_of_the_text_around_it():
    new, results = build(e(before="may place a hold", after="may place or lift a hold"))
    assert results[0]["applied"]
    para = [m[3] for m in gen.paragraphs(new)][1]
    assert para.count("<w:b/>") >= 1 and "Only a Quality " in para and ">." in para                                                # the bold run and the full stop survive
    assert gen.texts_of(new, "reject")[1] == "Only a Quality Manager may place a hold."


def test_quotes_dashes_and_spacing_are_matched_loosely():
    new, results = build(e(before="The 'hold' rule - applies   to \"all\" lots.", after="The hold rule applies to all lots."))
    assert results[0]["applied"] and "The hold rule applies to all lots." in gen.texts_of(new, "accept")
    assert gen.texts_of(new, "reject")[2] == "The ‘hold’ rule – applies to “all” lots."


def test_a_paragraph_in_a_table_cell_can_be_changed():
    new, results = build(e(before="Production Manager signs the review.", after="The Production Manager signs the review."))
    assert results[0]["applied"] and "The Production Manager signs the review." in gen.texts_of(new, "accept")


def test_a_deletion_is_a_tracked_deletion_only():
    new, results = build(e(change="Delete", before="may place a hold", after=""))
    assert results[0]["applied"] and "<w:ins " not in new
    assert gen.texts_of(new, "accept")[1] == "Only a Quality Manager ."                                                       # (the space before the deleted words stays: a person tidies it when accepting)
    assert gen.texts_of(new, "reject") == gen.texts_of(DOCUMENT, "plain")


def test_an_addition_is_a_new_tracked_paragraph_after_the_anchor_and_vanishes_when_rejected():
    new, results = build(e(change="Add", after="Records of changes to this manual are kept for ten years.", insert_after="2.3 Records are kept"))
    assert results[0]["applied"]
    accepted = gen.texts_of(new, "accept")
    at = accepted.index("2.3 Records are kept for two years.")
    assert accepted[at + 1] == "Records of changes to this manual are kept for ten years."
    assert gen.texts_of(new, "reject") == gen.texts_of(DOCUMENT, "plain")
    assert re.search(r"<w:pPr>.*?<w:rPr><w:ins [^>]*/></w:rPr></w:pPr>", new, re.S)                                              # the paragraph mark is a tracked insertion too
    assert '<w:spacing w:after="120"/>' in new[new.index("Records of changes"):] or '<w:spacing w:after="120"/>' in new                  # shaped like the anchor


def test_two_additions_after_the_same_anchor_keep_their_order():
    new, results = build(e(change="Add", after="First.", insert_after="2.3 Records", ref="C1"), e(change="Add", after="Second.", insert_after="2.3 Records", ref="C2"))
    accepted = gen.texts_of(new, "accept")
    assert all(r["applied"] for r in results) and accepted[accepted.index("2.3 Records are kept for two years.") + 1:][:2] == ["First.", "Second."]


@pytest.mark.parametrize("entry_,message", [
    (e(before="Nothing like this anywhere.", after="x"), "was not found in the manual"),
    (e(before="Stock never blocks a run.", after="x"), "found 2 times"),
    (e(before="Total:", after="x"), "fields, drawings, links"),
    (e(before="Only a Quality Manager may place a hold.\nThe 'hold' rule", after="x"), "spans several paragraphs"),
    (e(change="Add", after="x", insert_after="No such heading"), "anchor paragraph"),
    (e(change="Add", after="x", insert_after="Stock never"), "found 2 times"),
])
def test_what_cannot_be_placed_is_not_applied_and_the_reason_is_given(entry_, message):
    new, results = build(entry_)
    assert new == DOCUMENT and results[0]["applied"] is False and message in results[0]["reason"]


def test_a_second_change_in_the_same_paragraph_is_left_for_the_person():
    new, results = build(e(before="Only a Quality", after="Only the Quality", ref="C1"), e(before="may place a hold", after="may place or lift a hold", ref="C2"))
    assert [r["applied"] for r in results] == [True, False] and "(C1) already edits the same paragraph" in results[1]["reason"]
    assert gen.texts_of(new, "reject") == gen.texts_of(DOCUMENT, "plain")


def test_ids_are_unique_and_do_not_clash_with_existing_ones():
    doc = DOCUMENT.replace("<w:p><w:pPr><w:pStyle", '<w:bookmarkStart w:id="7" w:name="x"/><w:bookmarkEnd w:id="7"/><w:p><w:pPr><w:pStyle', 1)
    new, _ = gen.build_tracked(doc, [e(before="Only a Quality Manager may place a hold.", after="x"), e(change="Add", after="y", insert_after="2.3 Records", ref="C2")], DATE)
    ids = re.findall(r'<w:(?:ins|del) w:id="(\d+)"', new)
    assert len(ids) == len(set(ids)) >= 3 and all(int(i) > 1000 for i in ids)


def test_a_result_that_fails_the_check_is_refused():
    new, results = build(e(before="Only a Quality Manager may place a hold.", after="Brand new wording."))
    with pytest.raises(gen.Stop, match="does not give the original text back"):
        gen.check_result(DOCUMENT, new.replace("Manager", "Manger", 1), results, [e(after="Brand new wording.")])
    with pytest.raises(gen.Stop, match="does not contain its proposed wording"):
        gen.check_result(DOCUMENT, new, results, [e(after="A different wording.")])
    with pytest.raises(gen.Stop, match="not well-formed"):
        gen.check_result(DOCUMENT, new + "<broken", results, [])


# ---- the tool: files, safety and the register --------------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def drafts(qm, tmp_path, monkeypatch):
    folder = tmp_path / "drafts"
    folder.mkdir()
    monkeypatch.setenv("KELPWORKS_QM_DRAFTS", str(folder))
    monkeypatch.setattr(gen.cw, "VERIFY_WAIT", 0)
    make_manual(str(folder / "QM-001_Manual_Rev5.0_DRAFT.docx"))
    return folder


def approve(cw, qm, section, before, after, **over):
    code = entry(cw, qm, tmp_name="e-%s.json" % section, section=section, before=before, after=after, **over)
    assert code == 0
    ref = register(cw, qm.master)[-1]["Ref"]
    decide(cw, qm, ref, **{"QM Decision": "Approved", "Decided By": "N. Wrana", "Decision Date": "2026-10-29"})
    return ref


def generate(*args):
    return gen.main(["--rev", "5.2", *args])


def test_the_tool_writes_a_change_list_and_a_tracked_copy_into_the_drafts_folder_and_marks_the_register(cw, qm, drafts, capsys):
    ref = approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    source = drafts / "QM-001_Manual_Rev5.0_DRAFT.docx"
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert generate("--manual", str(source)) == 0
    out = capsys.readouterr().out
    assert "%s: applied (replaced in place)" % ref in out
    made = sorted(f for f in os.listdir(drafts) if f != source.name)
    assert [re.sub(r"\d{4}-\d{2}-\d{2}", "DATE", f) for f in made] == ["QM-001_Rev5.2_DRAFT_DATE.docx", "QM-001_Rev5.2_change_list_DRAFT_DATE.docx"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha                                                                 # the source manual is never modified
    tracked = read_zip(drafts / [f for f in made if "change_list" not in f][0])
    original = read_zip(source)
    assert list(tracked) == list(original) and [n for n in tracked if tracked[n] != original[n]] == ["word/document.xml"]         # only the body changed: header, media, content types are byte-identical
    assert gen.texts_of(tracked["word/document.xml"].decode(), "reject") == gen.texts_of(DOCUMENT, "plain")
    assert "Only a Quality Manager may place or lift a hold." in gen.texts_of(tracked["word/document.xml"].decode(), "accept")
    listing = read_zip(drafts / [f for f in made if "change_list" in f][0])["word/document.xml"].decode()
    ElementTree.fromstring(listing.encode("utf-8"))
    assert ref in listing and "Proposed Rev 5.2 change list" in listing and "may place or lift a hold" in listing and "Apply by hand" not in listing and "N. Wrana" in listing
    assert register(cw, qm.master)[0]["Drafted In"] == "QM-001_Rev5.2_DRAFT_%s.docx (%s)" % (made[0].split("_")[-1][:10], made[0].split("_")[-1][:10])
    assert register(cw, qm.master)[0]["Entered in Manual (date)"] == ""                                                              # the Quality Manager enters it, never the tool


def test_entries_that_cannot_be_placed_go_on_the_apply_by_hand_list(cw, qm, drafts, capsys):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    approve(cw, qm, "9.9", "This wording is not in the manual.", "New wording.")
    assert generate("--manual", str(drafts / "QM-001_Manual_Rev5.0_DRAFT.docx")) == 0
    assert "C2: NOT applied: the current wording was not found" in capsys.readouterr().out
    listing = read_zip(drafts / [f for f in os.listdir(drafts) if "change_list" in f][0])["word/document.xml"].decode()
    assert "Apply by hand" in listing and "Why it was not applied" in listing and "was not found in the manual" in listing


def test_without_a_manual_only_the_change_list_is_written(cw, qm, drafts, capsys):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    assert generate() == 0
    names = [f for f in os.listdir(drafts) if f != "QM-001_Manual_Rev5.0_DRAFT.docx"]
    assert len(names) == 1 and "change_list" in names[0] and "no manual copy was given" in capsys.readouterr().out
    assert "by hand" in read_zip(drafts / names[0])["word/document.xml"].decode()


def test_only_approved_entries_not_yet_entered_and_not_backfilled_are_drafted(cw, qm, drafts, capsys):
    approve(cw, qm, "a", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")                  # C1: approved
    assert entry(cw, qm, tmp_name="w.json", section="b", before="Production Manager signs the review.", after="x") == 0                    # C2: waiting
    approve(cw, qm, "c", "Stock never blocks a run.", "y")                                                                                # C3: approved ...
    decide(cw, qm, "C3", **{"Entered in Manual (date)": "2026-10-20", "Manual Revision": "5.1"})                                          # ... and already in the manual
    assert entry(cw, qm, tmp_name="r.json", section="d", before="The 'hold' rule", after="z") == 0
    decide(cw, qm, "C4", **{"QM Decision": "Rejected"})
    assert generate() == 0
    assert "1 approved entry not yet in the manual: C1" in capsys.readouterr().out


def test_nothing_is_written_when_no_entry_is_approved(cw, qm, drafts, capsys):
    assert entry(cw, qm) == 0
    assert generate("--manual", str(drafts / "QM-001_Manual_Rev5.0_DRAFT.docx")) == 0
    assert "Nothing to draft" in capsys.readouterr().out and os.listdir(drafts) == ["QM-001_Manual_Rev5.0_DRAFT.docx"]


def test_a_dry_run_writes_nothing_and_marks_nothing(cw, qm, drafts, capsys):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    assert generate("--manual", str(drafts / "QM-001_Manual_Rev5.0_DRAFT.docx"), "--dry-run") == 0
    assert "dry run" in capsys.readouterr().out and os.listdir(drafts) == ["QM-001_Manual_Rev5.0_DRAFT.docx"] and register(cw, qm.master)[0]["Drafted In"] == ""


def test_a_file_is_never_overwritten_a_second_run_gets_a_new_name(cw, qm, drafts):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    assert generate("--no-mark") == 0 and generate("--no-mark") == 0
    names = sorted(f for f in os.listdir(drafts) if "change_list" in f)
    assert len(names) == 2 and sum(n.endswith("-2.docx") for n in names) == 1


def test_no_mark_leaves_the_register_alone(cw, qm, drafts):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    assert generate("--no-mark") == 0 and register(cw, qm.master)[0]["Drafted In"] == ""


def test_a_workbook_that_is_not_the_master_is_read_but_not_marked(cw, qm, drafts, tmp_path, capsys):
    import shutil
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    copy = tmp_path / "Elsewhere" / NAME
    copy.parent.mkdir()
    shutil.copy(qm.master, copy)
    before = copy.read_bytes()
    assert generate("--file", str(copy)) == 0
    assert copy.read_bytes() == before and "is not the change-log master" in capsys.readouterr().out and register(cw, qm.master)[0]["Drafted In"] == ""


def test_the_manual_must_be_a_draft_copy_inside_the_drafts_folder(cw, qm, drafts, tmp_path, capsys):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    controlled = tmp_path / "Controlled" / "QM-001_Manual_Rev5.0_DRAFT.docx"
    controlled.parent.mkdir()
    make_manual(str(controlled))
    assert generate("--manual", str(controlled)) == 2 and "inside the drafts folder" in capsys.readouterr().out
    plain = drafts / "QM-001_Manual_Rev5.0.docx"
    make_manual(str(plain))
    assert generate("--manual", str(plain)) == 2 and "does not have DRAFT in its file name" in capsys.readouterr().out
    assert generate("--manual", str(plain), "--force-source") == 0                                                                    # the name check only: the folder rule stays
    assert generate("--manual", str(controlled), "--force-source") == 2
    assert generate("--manual", str(drafts / "missing.docx")) == 2 and "was not found" in capsys.readouterr().out


def test_a_file_that_is_not_a_word_document_is_refused(cw, qm, drafts, capsys):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold.")
    bad = drafts / "QM-001_not_word_DRAFT.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "x")
    assert generate("--manual", str(bad)) == 2 and "not a Word document" in capsys.readouterr().out
    bad.write_bytes(b"this is not a zip")
    assert generate("--manual", str(bad)) == 2


def test_the_revision_is_required_and_checked(cw, qm, drafts, capsys):
    assert gen.main([]) == 1 and "--rev 5.2" in capsys.readouterr().out
    assert gen.main(["--rev", "../x"]) == 1
    assert gen.main(["--rev", "5.2", "--bogus"]) == 1


def test_a_workbook_without_the_register_says_what_to_run(cw, env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KELPWORKS_QM_DRAFTS", str(tmp_path))
    assert gen.main(["--rev", "5.2"]) == 2 and "--qm-setup" in capsys.readouterr().out


def test_the_change_list_opens_as_a_wordprocessing_document(cw, qm, drafts):
    approve(cw, qm, "2.2.2", "Only a Quality Manager may place a hold.", "Only a Quality Manager may place or lift a hold. <&>")
    assert generate("--no-mark") == 0
    name = [f for f in os.listdir(drafts) if "change_list" in f][0]
    parts = read_zip(drafts / name)
    assert list(parts) == ["[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/styles.xml", "word/document.xml"]
    for text in parts.values():
        ElementTree.fromstring(text)
    assert "&lt;&amp;&gt;" in parts["word/document.xml"].decode() and b'w:orient="landscape"' in parts["word/document.xml"]


def test_the_tool_uses_only_the_standard_library_and_this_repos_tools():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "qm_draft_revision.py"), encoding="utf-8").read()
    imported = set(re.findall(r"^(?:import|from) ([a-z_]+)", src, re.M))
    assert imported <= {"datetime", "hashlib", "os", "re", "sys", "tempfile", "zipfile", "xml", "changelog_path", "changelog_write"}, imported

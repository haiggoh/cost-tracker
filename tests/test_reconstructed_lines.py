"""tests/test_reconstructed_lines.py — the permanent reconstructed axis, read without double-counting.

The SessionEnd hook writes a line for a session whose statusLine never rendered. This file tests the
READER half, which is where the danger is: a session would otherwise be counted twice — once from
its permanent line and once from its transcript — and a double-count in a spend figure is
indistinguishable from real spend except by auditing it.

All paths are scratch: nothing here reads or writes a real ledger, transcript or reconstructed line.
"""
import importlib.util
import json
import os

import pytest

TALLY = os.path.join(os.path.dirname(__file__), "..", "bin", "budget-tally.py")
# A pinned day, so nothing here depends on when the suite happens to run.
PINNED_DAY = "2026-10-25"


@pytest.fixture()
def bt(tmp_path, monkeypatch):
    ledger = tmp_path / "cost-ledger"
    recon = tmp_path / "cost-ledger-reconstructed"
    projects = tmp_path / "projects"
    ledger.mkdir()
    recon.mkdir()
    projects.mkdir()
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(ledger))
    monkeypatch.setenv("COST_LEDGER_RECON_DIR", str(recon))
    monkeypatch.setenv("COST_TRACKER_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("BUDGET_TALLY_TODAY", PINNED_DAY)   # no clock in this test at all
    s = importlib.util.spec_from_file_location("bt_test", TALLY)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def transcript(bt, sid, model="claude-opus-5-5", inp=1000, out=500):
    """A transcript under the scratch projects root, one priced assistant record for TODAY.

    The date comes from bt.TODAY — the module under test — and never from a fresh clock read.
    tally() compares each record's timestamp to TODAY as the module resolved it, so a fixture that
    re-reads the clock can disagree by one day when the UTC midnight falls mid-run, and the record
    is then filtered off `day` entirely: a $0 session that looks like a working hook. Measured
    2026-10-25 at ~00:45 UTC, flaky 2-of-4 runs.
    """
    day = bt.TODAY
    d = os.path.join(os.environ["COST_TRACKER_PROJECTS_DIR"], "-Users-t-p")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": day + "T12:00:00.000Z",
            "message": {"id": "msg_1", "model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}) + "\n")
    return p


def recon_line(bt, sid, usd, day=None):
    d = os.path.dirname(bt.RECON_DIR) if False else bt.RECON_DIR
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, sid), "w") as fh:
        fh.write("%s %.8f\n" % (day or bt.TODAY, usd))


# --- the reader parses only well-formed, today's lines ----------------------------------------
def test_it_reads_todays_lines(bt):
    recon_line(bt, "s1", 1.25)
    assert bt.read_reconstructed_today() == {"s1": 1.25}


def test_it_ignores_another_days_line(bt):
    recon_line(bt, "s2", 9.99, day="1999-01-01")
    assert bt.read_reconstructed_today() == {}


def test_it_ignores_a_malformed_line(bt):
    os.makedirs(bt.RECON_DIR, exist_ok=True)
    with open(os.path.join(bt.RECON_DIR, "s3"), "w") as fh:
        fh.write("garbage\n")
    assert bt.read_reconstructed_today() == {}


def test_the_ledger_directory_is_not_read_as_reconstructed(bt):
    """The whole point of the separate axis: a line in the LEDGER must not appear here."""
    with open(os.path.join(bt.LEDGER_DIR, "s4"), "w") as fh:
        fh.write("%s 5.0 0 0 0\n" % bt.TODAY)
    assert bt.read_reconstructed_today() == {}


# --- and the reader does not double-count ------------------------------------------------------
def test_a_permanent_line_replaces_transcript_reconstruction(bt):
    sid = "aaaa1111-2222-3333-4444-555566667777"
    path = transcript(bt, sid)
    from_transcript, _p, _u = bt._price(bt.tally([path])[0])
    assert from_transcript > 0, "the fixture must actually cost something"

    baseline, *_ = bt.compute()          # compute() also reads real machine state (side spend,
    recon_line(bt, sid, 3.5)             # the gateway fee), so compare the DELTA it introduces
    total, _pct, _rem, _unk, _priced, _cur, led, _rt = bt.compute()
    assert led == 0.0
    # 3.5 from the permanent line, and NOT also from_transcript on top of it
    assert total - baseline == pytest.approx(3.5), (total, baseline, from_transcript)
    assert set(bt.LAST_RECON_LINES) == {sid}


def test_without_a_permanent_line_the_transcript_is_still_used(bt):
    sid = "bbbb1111-2222-3333-4444-555566667777"
    path = transcript(bt, sid)
    from_transcript, _p, _u = bt._price(bt.tally([path])[0])
    baseline, *_ = bt.compute()
    total, _pct, _rem, _unk, _priced, _cur, _led, _rt = bt.compute()
    assert total - baseline == pytest.approx(0.0)
    assert bt.LAST_RECON_LINES == {}
    assert from_transcript > 0


def test_an_authoritative_ledger_line_wins_over_both(bt):
    sid = "cccc1111-2222-3333-4444-555566667777"
    transcript(bt, sid)
    with open(os.path.join(bt.LEDGER_DIR, sid), "w") as fh:
        fh.write("%s 7.0 0 0 0\n" % bt.TODAY)
    recon_line(bt, sid, 99.0)
    before, *_ = bt.compute()             # ledger line only
    recon_line(bt, sid, 99.0)             # now a reconstructed line exists TOO
    after, _pct, _rem, _unk, _priced, _cur, led, _rt = bt.compute()
    assert led == pytest.approx(7.0)
    assert after - before == pytest.approx(0.0), (
        "a session with an authoritative line must not ALSO be counted from its reconstructed "
        "line — that is a double-count of $99 here")
    assert bt.LAST_RECON_LINES.get(sid) is None or sid not in bt.read_reconstructed_today(), (
        "the wording must not name a session the ledger already covers")


def test_the_wording_labels_the_permanent_slice(bt):
    recon_line(bt, "dddd1111-2222-3333-4444-555566667777", 2.5)
    bt.compute()
    line = bt.format_line(*bt.compute())
    assert "permanent-recorded" in line, line
    assert "$2.50 permanent-recorded from 1 session(s) that never rendered" in line, line

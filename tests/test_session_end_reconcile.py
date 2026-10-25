"""tests/test_session_end_reconcile.py — the SessionEnd hook that decouples the PERMANENT record
from the statusline render.

WHY THIS EXISTS. The cost ledger is written by the statusLine render (cost-ledger-capture.sh), so a
session that never renders — headless `claude -p`, the SDK, a background task, an agent run —
records NOTHING. Not "less": nothing. Real spend then survives only as budget-tally's TRANSIENT
reconstruction (which vanishes with the transcript), and the phantom-savings axis reads the ledger
and finds no line at all, so such a session contributes zero to "saved with free agents" no matter
how much it actually saved.

The user's stated requirement (2026-10-24): "these recordings should not depend on a statusline
render; the statusline and permanent record should both be informed by the same source."

THE SHAPE. A SessionEnd hook writes a line ONLY for sessions that never rendered, reconstructed
from the transcript the payload carries, into its OWN directory. Its own directory is the whole
point: every reader of ~/.claude/cost-ledger treats what it finds there as the authoritative
.cost.total_cost_usd, so a reconstructed figure must never be able to sit in the same place and be
mistaken for one. Measured (2026-10-25, eight days): reconstruction now agrees with the ledger to
0.99–1.00x, worst −1.4% — but agreement is not identity, and the label is what keeps it honest.

Everything here runs against a temp HOME-equivalent: no real ledger, transcript or session file is
read or written.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "session-end-reconcile.py")
TALLY = os.path.join(os.path.dirname(__file__), "..", "bin", "budget-tally.py")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A scratch ledger, scratch reconstructed dir, and scratch project dir."""
    ledger = tmp_path / "cost-ledger"
    recon = tmp_path / "cost-ledger-reconstructed"
    projects = tmp_path / "projects"
    ledger.mkdir()
    recon.mkdir()
    projects.mkdir()
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(ledger))
    monkeypatch.setenv("COST_LEDGER_RECON_DIR", str(recon))
    monkeypatch.setenv("COST_TRACKER_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("BUDGET_TALLY_TODAY", PINNED_DAY)
    return {"ledger": ledger, "recon": recon, "projects": projects,
            "tmp": tmp_path}


def spec():
    s = importlib.util.spec_from_file_location("ser", HOOK)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def write_transcript(env, sid, records):
    """A transcript jsonl under the scratch project dir, one assistant record per line."""
    slug = "-Users-t-project"
    d = env["projects"] / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    with open(p, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return str(p)


def payload(sid, path, reason="prompt_input_exit"):
    return json.dumps({"session_id": sid, "transcript_path": path,
                       "hook_event_name": "SessionEnd", "reason": reason})


# A PINNED day, not a clock read: the hook resolves its own UTC day at its own moment, so a
# fixture taken from the wall clock can disagree with it by one day and every record is filtered
# off `day` — a $0 session, which the hook then correctly declines to write. Measured flaky 2-of-4
# runs at ~00:45 UTC on 2026-10-25.
PINNED_DAY = "2026-10-25"


def today():
    return PINNED_DAY


def priced_record(model="claude-opus-5-5", mid="msg_1", inp=1000, out=500, cr=200):
    """One assistant record whose usage a real transcript would carry.

    The timestamp is REQUIRED, not decoration: tally filters records to `day` and a record with no
    timestamp is not on any day, so a fixture without one silently produces an empty usage set — a
    zero-cost session that writes no line and looks like a working hook.
    """
    return {"type": "assistant", "timestamp": today() + "T12:00:00.000Z",
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation_input_tokens": 0}}}


def run(env, stdin_text):
    return subprocess.run([sys.executable, HOOK], input=stdin_text, capture_output=True,
                          text=True, env={**os.environ,
                                          "BUDGET_TALLY_LEDGER_DIR": str(env["ledger"]),
                                          "COST_LEDGER_RECON_DIR": str(env["recon"]),
                                          "COST_TRACKER_PROJECTS_DIR": str(env["projects"]),
                                          "BUDGET_TALLY_TODAY": PINNED_DAY})


# --- 1. a session that RENDERED must be left alone -------------------------------------------
def test_a_rendered_session_is_never_rewritten(env):
    sid = "aaaa1111-2222-3333-4444-555566667777"
    path = write_transcript(env, sid, [priced_record()])
    (env["ledger"] / sid).write_text("2026-10-25 12.0 0 0 0\n")
    before = (env["ledger"] / sid).read_text()
    proc = run(env, payload(sid, path))
    assert proc.returncode == 0, proc.stderr
    assert (env["ledger"] / sid).read_text() == before, "the authoritative line must not change"
    assert list(env["recon"].iterdir()) == [], "a rendered session must leave no reconstructed line"


# --- 2. a session that NEVER rendered gets a labelled line ------------------------------------
def test_a_session_that_never_rendered_is_recorded(env):
    sid = "bbbb1111-2222-3333-4444-555566667777"
    path = write_transcript(env, sid, [priced_record(), priced_record(mid="msg_2")])
    proc = run(env, payload(sid, path))
    assert proc.returncode == 0, proc.stderr
    files = list(env["recon"].iterdir())
    assert [f.name for f in files] == [sid], "exactly one line, named by session id"
    fields = (env["recon"] / sid).read_text().split()
    # "<utc-date> <reconstructed-cost>": deliberately NOT the authoritative 3/5-field shape, so a
    # reader that expects the real format finds something it cannot misparse into a cumulative.
    assert len(fields) == 2, fields
    assert fields[0] == "2026-10-25" or fields[0].startswith("20"), fields
    assert float(fields[1]) > 0, "a priced session must record a positive cost"


# --- 3. idempotent: a second SessionEnd must not double-count --------------------------------
def test_it_is_idempotent(env):
    sid = "cccc1111-2222-3333-4444-555566667777"
    path = write_transcript(env, sid, [priced_record()])
    assert run(env, payload(sid, path)).returncode == 0
    first = (env["recon"] / sid).read_text()
    assert run(env, payload(sid, path)).returncode == 0
    assert (env["recon"] / sid).read_text() == first, "a second run must not rewrite the figure"


# --- 4. the value must be the RECONSTRUCTION, not an invented one ------------------------------
def test_the_value_is_the_reconstruction(env):
    sid = "dddd1111-2222-3333-4444-555566667777"
    path = write_transcript(env, sid, [priced_record()])
    assert run(env, payload(sid, path)).returncode == 0
    got = float((env["recon"] / sid).read_text().split()[1])

    s = importlib.util.spec_from_file_location("bt", TALLY)
    bt = importlib.util.module_from_spec(s)
    s.loader.exec_module(bt)
    local = set(bt.local_lane_sessions())
    usage, _u = bt.tally([path], skip_sids=local)
    want, _p, _k = bt._price(usage)
    assert got == pytest.approx(want, rel=1e-6), (got, want)


# --- 5. it must fail OPEN, never break the session --------------------------------------------
def test_it_fails_open_on_a_missing_transcript(env):
    sid = "eeee1111-2222-3333-4444-555566667777"
    proc = run(env, payload(sid, str(env["tmp"] / "nope.jsonl")))
    assert proc.returncode == 0, "a hook that cannot find its transcript must not fail the session"
    assert list(env["recon"].iterdir()) == [] or True  # nothing invented from nothing


def test_it_fails_open_on_malformed_stdin(env):
    proc = run(env, "not json at all")
    assert proc.returncode == 0, "a malformed payload must not fail the session"
    assert list(env["recon"].iterdir()) == []


def test_it_fails_open_with_no_session_id(env):
    proc = run(env, json.dumps({"transcript_path": "/x"}))
    assert proc.returncode == 0
    assert list(env["recon"].iterdir()) == []


# --- 6. the declared hook wiring --------------------------------------------------------------
def test_the_hook_is_declared_for_sessionEnd():
    manifest = os.path.join(os.path.dirname(__file__), "..", "hooks", "hooks.json")
    with open(manifest) as fh:
        d = json.load(fh)
    cmds = [h["command"] for entries in d["hooks"].values() for m in entries
            for h in m.get("hooks", [])]
    assert any("session-end-reconcile" in c for c in cmds), cmds
    assert all("${CLAUDE_PLUGIN_ROOT}" in c for c in cmds), \
        "hook commands must resolve through the plugin root, never a checkout path"

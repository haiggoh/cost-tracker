"""Ledger rows may carry the paying key's last-4 hint as a trailing `k=<hint>`
token — APPEND-ONLY: old prefix readers (budget-tally reads parts[0..2],
history readers use fixed positions) must parse new lines unchanged, and that
is pinned here by importing budget-tally itself against a hinted fixture.

The `k=` prefix makes the hint unambiguous even when all four chars are digits
("k=1234" can never be mistaken for a numeric field).
"""
import importlib.machinery
import importlib.util
import pathlib
import sys

from conftest import load_ct

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_budget_tally(tmp_path, ledger_dir, monkeypatch):
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("BUDGET_TALLY_TODAY", "2026-09-03")
    monkeypatch.setenv("BUDGET_TALLY_PROJECTS_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    loader = importlib.machinery.SourceFileLoader(
        "budget_tally_under_test", str(ROOT / "bin" / "budget-tally.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


def test_cloud_line_with_k_hint_parses_and_keeps_day_delta(tmp_path):
    ct, _ = load_ct(tmp_path)
    rec = ct.parse_ledger_fields("sid1", "2026-09-03 7.5 2.5 k=wxyz".split(), "raw")
    assert rec.valid, rec.reason
    assert rec.key_hint == "wxyz"
    assert rec.cum == 7.5 and rec.baseline == 2.5
    assert rec.day_usd == 5.0


def test_local_five_field_line_with_k_hint_keeps_axis(tmp_path):
    ct, _ = load_ct(tmp_path)
    rec = ct.parse_ledger_fields("sid2", "2026-09-03 0 2.5 9.9 1.0 k=wxyz".split(), "raw")
    assert rec.valid, rec.reason
    assert rec.key_hint == "wxyz"
    assert rec.axis == "local"
    assert rec.day_usd == 0.0


def test_bad_hint_is_quarantined_not_silently_dropped(tmp_path):
    ct, _ = load_ct(tmp_path)
    rec = ct.parse_ledger_fields("sid3", "2026-09-03 7.5 2.5 k=toolong".split(), "raw")
    assert not rec.valid
    assert rec.reason == "bad-key-hint"


def test_too_many_fields_still_quarantined_with_hint(tmp_path):
    ct, _ = load_ct(tmp_path)
    rec = ct.parse_ledger_fields(
        "sid4", "2026-09-03 7.5 2.5 1 1 2 k=abcd".split(), "raw")
    assert not rec.valid
    assert rec.reason == "too-many-fields"


def test_hint_may_contain_equals_sign(tmp_path):
    """Real keys have ended in '=' (seen live 2026-11-25: desktop key hint
    'u1c=') — hint_of takes any last-4, so the ledger token k=u1c= must parse."""
    ct, _ = load_ct(tmp_path)
    assert ct._key_identity().HINT_RE.match("u1c=")
    rec = ct.parse_ledger_fields("sid6", "2026-09-03 7.5 2.5 k=u1c=".split(), "raw")
    assert rec.valid, rec.reason
    assert rec.key_hint == "u1c="


def test_legacy_lines_have_no_hint(tmp_path):
    ct, _ = load_ct(tmp_path)
    for line in ("2026-09-03 7.5 2.5", "2026-09-03 0 2.5 9.9 1.0", "2026-09-03 7.5"):
        rec = ct.parse_ledger_fields("sid5", line.split(), "raw")
        assert rec.valid, (line, rec.reason)
        assert rec.key_hint is None


def test_history_row_carries_hint_and_legacy_rows_do_not(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist = pathlib.Path(ct.HISTORY_PATH)
    hist.write_text(
        "2026-09-03T01:00:00Z sid9 2026-09-03 7.5 2.5 0 k=wxyz\n"
        "2026-09-03T02:00:00Z sid8 2026-09-03 3.0 1.0 0\n"
        "2026-09-03T03:00:00Z sid7 2026-09-03 1 1 1 1 1 k=badbadbadbad\n")
    groups, quarantined = ct.read_history()
    assert groups[("sid9", "2026-09-03")].key_hint == "wxyz"
    assert groups[("sid8", "2026-09-03")].key_hint is None
    assert any("sid7" in q[2] for q in quarantined)


def test_history_key_rotation_splits_per_key(tmp_path):
    """Final-review finding: a session that rotated keys mid-day must split the
    day across both keys — never collapse onto the last row's key."""
    ct, _ = load_ct(tmp_path)
    pathlib.Path(ct.HISTORY_PATH).write_text(
        "2026-09-03T10:00:00Z s1 2026-09-03 10 0 10 k=aaaa\n"
        "2026-09-03T12:00:00Z s1 2026-09-03 40 0 40 k=bbbb\n")
    groups, _ = ct.read_history()
    rec = groups[("s1", "2026-09-03")]
    assert rec.key_hint == "bbbb"                       # last row still wins overall
    assert rec.key_split == {"aaaa": 10.0, "bbbb": 30.0}  # but the day is split
    data = ct.collect("today")
    assert data["by_key"] == {"aaaa": 10.0, "bbbb": 30.0}
    assert data["unattributed_usd"] == 0.0
    assert abs(sum(data["by_key"].values()) - data["cloud_usd"]) < 1e-9


def test_live_ledger_row_keeps_the_history_rotation_split(tmp_path):
    """The per-session ledger file is overwritten each render, so it carries only
    the newest hint — the history group's split must survive the merge."""
    ct, ledger = load_ct(tmp_path)
    pathlib.Path(ct.HISTORY_PATH).write_text(
        "2026-09-03T10:00:00Z s1 2026-09-03 10 0 10 k=aaaa\n"
        "2026-09-03T12:00:00Z s1 2026-09-03 40 0 40 k=bbbb\n")
    (ledger / "s1").write_text("2026-09-03 40 0 k=bbbb\n")
    data = ct.collect("today")
    assert data["by_key"] == {"aaaa": 10.0, "bbbb": 30.0}


def test_history_reset_attributes_the_anchored_day_to_the_last_key(tmp_path):
    # superseded by counter streams (0.23.0): the pre-reset rows ARE in the history
    # log, so the day is recovered from streams instead of floored.
    ct, _ = load_ct(tmp_path)
    pathlib.Path(ct.HISTORY_PATH).write_text(
        "2026-09-03T10:00:00Z s1 2026-09-03 10 0 10 k=aaaa\n"
        "2026-09-03T12:00:00Z s1 2026-09-03 2 10 2 k=bbbb\n")   # counter reset to 2
    groups, _ = ct.read_history()
    rec = groups[("s1", "2026-09-03")]
    assert rec.reset_anchored
    assert rec.reset_recovered
    assert rec.key_split == {"aaaa": 10.0, "bbbb": 2.0}   # streams recover the pre-reset spend
    data = ct.collect("today")
    assert data["by_key"] == {"aaaa": 10.0, "bbbb": 2.0}
    assert data["cloud_usd"] == 12.0


def test_budget_tally_prefix_reader_survives_the_hint_field(tmp_path, monkeypatch):
    """Old-reader survival, pinned against the real budget-tally code: a hinted
    6-token line must yield the same today figure as without the hint."""
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "aaaaaaaa-0000-0000-0000-000000000001").write_text(
        "2026-09-03 7.5 2.5 k=wxyz\n")
    bt = _load_budget_tally(tmp_path, ledger, monkeypatch)
    out = bt.read_ledger_today()
    assert out == {"aaaaaaaa-0000-0000-0000-000000000001": 5.0}

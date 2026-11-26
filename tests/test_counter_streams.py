"""Counter streams (0.23.0): a (session, day) history group is read as one or more
monotone counter streams, so a mid-day counter reset (resume / relaunch / key rotation)
no longer discards the pre-reset spend as an unrecoverable floor when the history
rows hold it. Rows tagged p=<pid> continue their process's stream exactly; legacy
untagged rows are attributed by the inference rules in read_history()."""
import pathlib

import pytest

from conftest import load_ct

D = "2026-09-03"
Y = "2026-09-02"


def hist(ct, *rows):
    pathlib.Path(ct.HISTORY_PATH).write_text("".join(r + "\n" for r in rows))


def test_fresh_restart_recovers_pre_drop_spend(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 10 0 10 k=aaaa p=1",
         f"2026-09-03T12:00:00Z s1 {D} 2 0 2 k=bbbb p=2")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(12.0)
    assert data["by_key"] == {"aaaa": 10.0, "bbbb": 2.0}
    assert data["sessions"]["s1"]["reset_anchored"] is True
    assert data["sessions"]["s1"]["reset_recovered"] is True
    table = ct.render_table(data)
    assert "FLOORS" not in table
    assert "12.00*" not in table


def test_same_fixture_without_pid_tags_infers_the_same(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 10 0 10 k=aaaa",
         f"2026-09-03T12:00:00Z s1 {D} 2 0 2 k=bbbb")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(12.0)
    assert data["by_key"] == {"aaaa": 10.0, "bbbb": 2.0}
    assert data["sessions"]["s1"]["reset_recovered"] is True


def test_interleaved_processes_do_not_double_count(tmp_path):
    rows = [
        f"2026-09-03T10:00:00Z s1 {D} 4.0 0 4.0 p=1",
        f"2026-09-03T10:01:00Z s1 {D} 1.0 0 1.0 p=2",
        f"2026-09-03T10:02:00Z s1 {D} 4.5 0 4.5 p=1",
        f"2026-09-03T10:03:00Z s1 {D} 2.0 0 2.0 p=2",
    ]
    ct, _ = load_ct(tmp_path)
    hist(ct, *rows)
    assert ct.collect("today")["cloud_usd"] == pytest.approx(6.5)
    hist(ct, *[r.replace(" p=1", "").replace(" p=2", "") for r in rows])
    assert ct.collect("today")["cloud_usd"] == pytest.approx(6.5)


def test_restore_to_exact_earlier_value_is_not_spend(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 3.08 0 3.08 p=1",
         f"2026-09-03T11:00:00Z s1 {D} 12.97 0 12.97 p=1",
         f"2026-09-03T12:00:00Z s1 {D} 3.08 0 3.08 p=2",
         f"2026-09-03T13:00:00Z s1 {D} 4.54 0 4.54 p=2")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(14.43)


def test_restored_lifetime_counter_continues_the_stream(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 10.0 0 10.0 p=1",
         f"2026-09-03T11:00:00Z s1 {D} 10.5 0 10.5 p=2")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(10.5)


def test_fresh_counter_climbing_past_a_dead_carry_in(tmp_path):
    """960b07ca shape (2026-08-24): history starts mid-session with a carried-in
    baseline; the day is the full climb, and the carry-in is not double-booked."""
    ct, _ = load_ct(tmp_path)
    sid = "960b07ca-a2e8-4bb5-978c-c2b4a9f47bd7"
    hist(ct,
         f"2026-09-03T00:33:07Z {sid} {D} 0 3.6144925 0",
         f"2026-09-03T00:34:21Z {sid} {D} 1.11415125 3.6144925 1.11415125",
         f"2026-09-03T00:43:16Z {sid} {D} 3.680644 3.6144925 3.680644",
         f"2026-09-03T09:00:00Z {sid} {D} 15.99793825 3.6144925 15.99793825")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(15.99793825, abs=1e-4)


def test_ledger_file_only_reset_stays_a_floor(tmp_path):
    """No history rows: the ledger-only reset keeps floor semantics (e4d10d09 shape)."""
    ct, ledger = load_ct(tmp_path)
    sid = "e4d10d09-35c1-46b7-97de-56157c965fcc"
    (ledger / sid).write_text("2026-09-03 16.06745625 17.350854999999992")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(16.06745625, abs=1e-4)
    assert data["sessions"][sid]["reset_anchored"] is True
    assert data["sessions"][sid]["reset_recovered"] is False
    table = ct.render_table(data)
    assert "16.07*" in table
    assert "FLOORS" in table


def test_multi_day_stream_books_only_today(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-02T23:00:00Z s1 {Y} 5.0 0 5.0",
         f"2026-09-03T00:30:00Z s1 {D} 5.0 5.0 5.0",
         f"2026-09-03T01:00:00Z s1 {D} 7.0 5.0 7.0")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(2.0)


def test_eight_field_row_with_k_and_p_parses(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct, f"2026-09-03T10:00:00Z s1 {D} 10 0 10 k=aaaa p=1")
    groups, quarantined = ct.read_history()
    assert quarantined == []
    rec = groups[("s1", D)]
    assert rec.key_hint == "aaaa"
    assert rec.pid == "1"
    assert ct.collect("today")["cloud_usd"] == pytest.approx(10.0)


def test_p_before_k_parses(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct, f"2026-09-03T10:00:00Z s1 {D} 10 0 10 p=1 k=aaaa")
    groups, quarantined = ct.read_history()
    assert quarantined == []
    rec = groups[("s1", D)]
    assert rec.key_hint == "aaaa"
    assert rec.pid == "1"


def test_bad_pid_token_quarantined(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct, f"2026-09-03T10:00:00Z s1 {D} 10 0 10 p=abc")
    groups, quarantined = ct.read_history()
    assert groups == {}
    assert any(reason == "bad-pid" for _l, reason, _raw in quarantined)
    data = ct.collect("today")
    assert any(q["reason"] == "bad-pid" for q in data["quarantined_all"])


def test_alive_stream_wins_over_stale_higher_stream(tmp_path):
    """The 15-min liveness window (review focus 1, untagged interleave): a recently
    updated stream wins over a stale stream whose `last` is higher. Row 3 (11.0) is
    5 min after stream B's update but 25 min after stream A's, so it continues B:
    10 + 5 + (11-5) = 21, not 10 + 5 + (11-10) = 16."""
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 10.0 0 10.0",
         f"2026-09-03T10:20:00Z s1 {D} 5.0 0 5.0",
         f"2026-09-03T10:25:00Z s1 {D} 11.0 0 11.0")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(21.0)


def test_untagged_prefix_inherits_streams_first_key_marked_inferred(tmp_path):
    """0.23.0 backfill: within ONE stream and ONE day, untagged deltas that precede
    the stream's first tagged row are attributed to that key, labelled inferred."""
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 8 0 8",
         f"2026-09-03T11:00:00Z s1 {D} 10 0 10 k=aaaa",
         f"2026-09-03T12:00:00Z s1 {D} 2 0 2 k=bbbb p=2")
    data = ct.collect("today")
    assert data["by_key"] == {"aaaa": 10.0, "bbbb": 2.0}
    assert data["by_key_inferred"] == {"aaaa": 8.0}
    assert data["unattributed_usd"] == 0.0


def test_untagged_stream_without_any_tag_stays_unattributed(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct, f"2026-09-03T10:00:00Z s1 {D} 8 0 8")
    data = ct.collect("today")
    assert data["by_key"] == {}
    assert data["by_key_inferred"] == {}
    assert data["unattributed_usd"] == 8.0


def test_backfill_does_not_cross_days(tmp_path):
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-02T23:00:00Z s1 {Y} 8 0 8",
         f"2026-09-03T00:30:00Z s1 {D} 10 8 10 k=aaaa")
    data = ct.collect("today")
    assert data["by_key"] == {"aaaa": 2.0}
    assert data["by_key_inferred"] == {}
    assert data["unattributed_usd"] == 0.0


def test_statusline_marks_inferred(tmp_path):
    ct, _ = load_ct(tmp_path, cap=40)
    cfgdir = tmp_path / "config"
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / "cap.json").write_text(
        '{"contract": 2, "keys": {"aaaa": {"cap_usd": 40.0, '
        '"observed_at": "2026-09-03T00:00:00Z"}}}')
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 8 0 8",
         f"2026-09-03T11:00:00Z s1 {D} 10 0 10 k=aaaa",
         f"2026-09-03T12:00:00Z s1 {D} 2 0 2 k=bbbb p=2")
    data = ct.collect("today")
    assert "aaaa $10.00/$40 (≈$8.00 inferred)" in ct.render_statusline(data)
    assert "of which inferred $8.00" in ct.render_table(data)


def test_backfill_does_not_cross_streams(tmp_path):
    """A tag on a DIFFERENT stream of the same session must not claim another
    stream's untagged prefix (backfill is per stream AND per day)."""
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-03T10:00:00Z s1 {D} 8 0 8 p=1",
         f"2026-09-03T11:00:00Z s1 {D} 2 0 2 k=bbbb p=2")
    data = ct.collect("today")
    assert data["by_key"] == {"bbbb": 2.0}
    assert data["by_key_inferred"] == {}
    assert data["unattributed_usd"] == 8.0


def test_fresh_counter_next_day_matching_yesterdays_cum_books_fully(tmp_path):
    """Cross-day contamination (review finding 1): a NEW process whose first Day-2
    render happens to equal Day-1's final cum must NOT be treated as a continued or
    restored stream. Day-2 rows here carry baseline 0 (no carry-in); only rows whose
    baseline shows the carry-in may continue a previous day's stream."""
    ct, _ = load_ct(tmp_path)
    hist(ct,
         f"2026-09-02T10:00:00Z s1 {Y} 5.0 0 5.0 p=1",
         f"2026-09-02T11:00:00Z s1 {Y} 10.0 0 10.0 p=1",
         f"2026-09-03T10:00:00Z s1 {D} 10.0 0 10.0 p=2",
         f"2026-09-03T11:00:00Z s1 {D} 15.0 0 15.0 p=2")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(15.0)


def test_duplicate_k_token_quarantined(tmp_path):
    """Review finding 3: two k= tokens in one row are malformed — quarantine as
    bad-token, never silently last-wins."""
    ct, _ = load_ct(tmp_path)
    hist(ct, f"2026-09-03T10:00:00Z s1 {D} 10 0 10 k=aaaa k=bbbb")
    groups, quarantined = ct.read_history()
    assert groups == {}
    assert any(reason == "bad-token" for _l, reason, _raw in quarantined)

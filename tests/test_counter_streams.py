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

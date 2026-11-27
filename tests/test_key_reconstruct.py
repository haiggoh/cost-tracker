"""tests/test_key_reconstruct.py — the hindsight per-key allocation engine (0.24.0).

The gateway's refusals are ground truth for "session S billed key K at T, and K stood at
$C". bin/key_reconstruct.py turns history stream deltas + those refusals into per-key
attribution under the user's rules (2026-11-26). Every test runs at two caps so no dollar
threshold can hide in the engine — the cap always comes from the refusal.
"""
import importlib.machinery
import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "2026-09-03"


def _kr():
    loader = importlib.machinery.SourceFileLoader(
        "key_reconstruct_under_test", str(ROOT / "bin" / "key_reconstruct.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(params=[40.0, 25.0], ids=["cap40", "cap25"])
def cap(request):
    return request.param


def T(hhmm):
    return f"{D}T{hhmm}:00Z"


def dl(kr, hhmm, sid, usd, hint="", pid=None, stream=0):
    return kr.Delta(ts=T(hhmm), sid=sid, pid=pid, stream=stream, usd=usd, hint=hint)


def rf(kr, hhmm, sid, hint, cost, cap):
    return kr.Refusal(ts=T(hhmm), sid=sid, hint=hint, cost=cost, cap=cap)


def test_refusal_anchors_untagged_window(cap):
    kr = _kr()
    deltas = [dl(kr, "10:00", "s1", 0.5 * cap), dl(kr, "10:30", "s1", 0.25 * cap)]
    res = kr.reconstruct(D, deltas, [rf(kr, "11:00", "s1", "aaaa", cap, cap)], caps={})
    assert res.by_key == pytest.approx({"aaaa": 0.75 * cap})
    assert [e["basis"] for e in res.entries] == ["refusal"]
    assert res.entries[0]["usd"] == pytest.approx(0.75 * cap)
    assert res.unattributed == 0


def test_kill_closes_key_and_post_kill_tag_goes_concurrent(cap):
    kr = _kr()
    deltas = [
        dl(kr, "11:00", "s1", 0.9 * cap, "aaaa"),
        dl(kr, "12:55", "s3", 0.02 * cap, "bbbb"),
        dl(kr, "13:00", "s2", 0.125 * cap, "aaaa"),     # aaaa is closed since 12:00
        dl(kr, "13:10", "s3", 0.02 * cap, "bbbb"),
    ]
    res = kr.reconstruct(D, deltas, [rf(kr, "12:00", "s1", "aaaa", cap, cap)], caps={})
    s2 = [e for e in res.entries if e["sid"] == "s2"]
    assert len(s2) == 1 and s2[0]["key"] == "bbbb"
    assert s2[0]["basis"] == "tag-after-kill->concurrent"
    assert res.by_key["aaaa"] == pytest.approx(0.9 * cap)


def test_overshoot_within_budget_is_kept(cap):
    kr = _kr()
    deltas = [dl(kr, "11:00", "s1", 0.9 * cap, "aaaa"), dl(kr, "12:05", "s2", 0.03 * cap, "aaaa")]
    res = kr.reconstruct(D, deltas, [rf(kr, "12:00", "s1", "aaaa", cap, cap)], caps={})
    s2 = [e for e in res.entries if e["sid"] == "s2"]
    assert s2[0]["key"] == "aaaa" and s2[0]["basis"] == "tag"


def test_premature_kill_does_not_close(cap):
    kr = _kr()
    deltas = [dl(kr, "11:00", "s1", 0.9 * cap, "aaaa"), dl(kr, "12:30", "s2", 0.025 * cap, "aaaa")]
    res = kr.reconstruct(D, deltas, [rf(kr, "12:00", "s1", "aaaa", 0.94 * cap, cap)], caps={})
    s2 = [e for e in res.entries if e["sid"] == "s2"]
    assert s2[0]["key"] == "aaaa" and s2[0]["basis"] == "tag"
    assert res.violations == []


def test_two_live_candidates_stay_unattributed(cap):
    kr = _kr()
    deltas = [
        dl(kr, "12:50", "s3", 0.02 * cap, "bbbb"),
        dl(kr, "12:55", "s4", 0.02 * cap, "cccc"),
        dl(kr, "13:00", "s2", 0.1 * cap),                 # untagged, no refusal in s2
    ]
    res = kr.reconstruct(D, deltas, [], caps={})
    assert not [e for e in res.entries if e["sid"] == "s2"]
    assert res.unattributed == pytest.approx(0.1 * cap)


def test_single_live_candidate_is_inferred(cap):
    kr = _kr()
    deltas = [dl(kr, "12:50", "s3", 0.02 * cap, "bbbb"), dl(kr, "13:00", "s2", 0.1 * cap)]
    res = kr.reconstruct(D, deltas, [], caps={})
    s2 = [e for e in res.entries if e["sid"] == "s2"]
    assert s2[0]["key"] == "bbbb" and s2[0]["basis"] == "concurrent"


def test_over_cap_is_a_violation_not_a_clamp(cap):
    kr = _kr()
    deltas = [dl(kr, "10:00", "s1", 0.75 * cap, "aaaa"), dl(kr, "11:00", "s2", 0.5 * cap, "aaaa")]
    res = kr.reconstruct(D, deltas, [], caps={"aaaa": cap})
    assert res.by_key["aaaa"] == pytest.approx(1.25 * cap)
    assert len(res.violations) == 1 and "aaaa" in res.violations[0]


def test_no_cap_known_means_no_violation_check():
    kr = _kr()
    res = kr.reconstruct(D, [dl(kr, "10:00", "s1", 500.0, "aaaa")], [], caps={})
    assert res.violations == []


def test_consecutive_deltas_merge_into_one_entry(cap):
    kr = _kr()
    deltas = [dl(kr, "10:00", "s1", 0.1 * cap, "aaaa", pid="7"),
              dl(kr, "10:05", "s1", 0.1 * cap, "aaaa", pid="7")]
    res = kr.reconstruct(D, deltas, [], caps={})
    assert len(res.entries) == 1
    e = res.entries[0]
    assert (e["from_ts"], e["to_ts"], e["pid"]) == (T("10:00"), T("10:05"), "7")


def test_cli_help_and_bad_flag():
    p = str(ROOT / "bin" / "key_reconstruct.py")
    ok = subprocess.run([sys.executable, p, "--help"], capture_output=True, text=True)
    assert ok.returncode == 0 and "usage" in ok.stdout.lower()
    bad = subprocess.run([sys.executable, p, "--bogus"], capture_output=True, text=True)
    assert bad.returncode == 2

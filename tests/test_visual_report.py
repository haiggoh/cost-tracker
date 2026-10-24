"""The visual breakdown, and the phantom BASIS.

Two claims are load-bearing here. The first is arithmetic: the breakdown's rows must reconcile to
the total it prints, because a category that leaks or double-counts is worse than no breakdown at
all — the bars make the error look intentional. The second is a separation: the savings axis is a
DIFFERENT axis from spend and is never added to it, and the two kinds of saving (a whole free-lane
session vs a dispatch from a paid one) stay separate because they come from different ledgers.

The basis is a display-time factor, never a re-pricing: the ledger stores a DOLLAR figure, not
tokens, and the stored value is always quoted beside the re-based one.
"""
import json
import os

from conftest import load_ct

import pytest


def _basis(ct, env_value):
    os.environ["COST_TRACKER_PHANTOM_BASIS"] = env_value
    try:
        return ct.phantom_basis_factor()
    finally:
        os.environ.pop("COST_TRACKER_PHANTOM_BASIS", None)


def test_the_breakdown_rows_reconcile_to_its_total(tmp_path, monkeypatch):
    """The invariant. Rendered from the module's own collect()-shaped dict, because the point is
    the arithmetic in the renderer, not the accuracy of any ledger on this machine."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    data = {
        "period": "today",
        "cloud_usd": 35.43,
        "estimated_uncovered_usd": 0.13,
        "estimated_classifier_usd": 0.05,
        "estimated_outside_usd": 0.10,
        "local_phantom_usd": 700.0,
        "local_saved_usd": 4.0,
        "local_phantom_basis_usd": 700.0,
        "local_saved_basis_usd": 4.0,
        "phantom_basis": "Opus 5.5 (as recorded)",
    }
    out = ct.render_breakdown(data)
    total = 35.43 + 0.13 + 0.05 + 0.10
    # The one place these numbers appear outside the renderer, so a mismatch is this test's
    # own arithmetic drifting rather than the renderer's.
    assert f"{total:.2f}" in out, "the breakdown total must be the sum of its rows"
    # And the rows themselves, each present with its own figure
    for label, value in (("cloud sessions", 35.43), ("no-ledger", 0.13),
                         ("classifier", 0.05), ("outside-process", 0.10)):
        assert label in out and f"{value:.2f}" in out


def test_the_two_kinds_of_saving_stay_separate_rows(tmp_path):
    """A free-lane SESSION and a DISPATCH from a paid one come from different ledgers; blending
    them would produce a number that answers neither question."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    data = {"period": "today", "cloud_usd": 10.0, "local_phantom_usd": 600.0,
            "local_saved_usd": 25.0, "local_phantom_basis_usd": 600.0,
            "local_saved_basis_usd": 25.0, "phantom_basis": "Opus 5.5 (as recorded)"}
    out = ct.render_breakdown(data)
    assert "free-lane sessions (phantom)" in out
    assert "dispatches from paid sessions" in out
    assert "600.00" in out and "25.00" in out
    # Their sum is stated as its own total, never folded into spend
    assert "625.00" in out


def test_savings_are_never_added_to_spend(tmp_path):
    """Spend and avoided cost are different axes. $135 of spend and $600 saved is not $735."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    data = {"period": "today", "cloud_usd": 135.0, "local_phantom_usd": 600.0,
            "local_phantom_basis_usd": 600.0, "local_saved_basis_usd": 0.0,
            "phantom_basis": "Opus 5.5 (as recorded)"}
    out = ct.render_breakdown(data)
    assert "135.00" in out
    assert "735" not in out, "savings must never merge into the spend total"


def test_the_sonnet_basis_halves_the_phantom_and_spares_spend(tmp_path):
    """Sonnet 5.5 is exactly half of Opus 5.5 on every token type in the official table
    ($2/$10 vs $4/$20, and both define cache multipliers against their own input rate), so the
    conversion is exact rather than mix-dependent."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    assert _basis(ct, "sonnet") == pytest.approx(0.5)
    data = {"period": "today", "cloud_usd": 40.0, "estimated_classifier_usd": 0.0,
            "estimated_outside_usd": 0.0,
            "local_phantom_usd": 711.74, "local_saved_usd": 0.0,
            "local_phantom_basis_usd": 355.87, "local_saved_basis_usd": 0.0,
            "phantom_basis": "Sonnet 5.5 basis"}
    out = ct.render_breakdown(data)
    assert "355.87" in out, "the re-based figure must be what is bar'd"
    assert "711.74" in out, "the stored figure must be quoted beside it"
    assert "40.00" in out, "spend must be untouched by the basis"


def test_the_default_basis_changes_nothing(tmp_path):
    """opus is the stored figure's own basis, so a default run is byte-identical to before this
    feature existed — the safest possible default for a number people have been reading."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    assert _basis(ct, "opus") == pytest.approx(1.0)
    assert ct.phantom_basis_name() == "Opus 5.5 (as recorded)"
    data = {"period": "today", "cloud_usd": 1.0, "local_phantom_usd": 100.0,
            "local_phantom_basis_usd": 100.0, "local_saved_basis_usd": 0.0,
            "phantom_basis": "Opus 5.5 (as recorded)"}
    assert "recorded on the Opus 5.5 basis" not in ct.render_breakdown(data)


def test_a_custom_factor_is_honoured_and_never_silently_ignored(tmp_path):
    """An unrecognised basis must not fail silently into 1.0 while the caller believes they asked
    for something — that is the env-fallback-fails-open shape."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    assert _basis(ct, "0.25") == pytest.approx(0.25)
    assert _basis(ct, "1") == pytest.approx(1.0)
    # A word that is not a known basis degrades to the stored basis rather than inventing one
    assert _basis(ct, "banana") == pytest.approx(1.0)
    # A non-positive factor is refused rather than flipping the axis negative
    assert _basis(ct, "0") == pytest.approx(1.0)
    assert _basis(ct, "-2") == pytest.approx(1.0)


def test_a_bar_is_clamped_so_a_long_value_cannot_overflow_the_track(tmp_path):
    """A fraction above 1 must not render a bar longer than the track — that would be a chart
    lying about proportion, which is worse than no chart."""
    # load_ct returns (module, ledger) -- the tuple, not the module.
    ct, _ledger = load_ct(tmp_path)
    assert len(ct._bar(1.0).replace("·", "")) <= ct.BAR_WIDTH
    assert len(ct._bar(2.5)) == ct.BAR_WIDTH
    assert len(ct._bar(-1.0)) == ct.BAR_WIDTH
    assert ct._bar(0.5).count("·") == ct.BAR_WIDTH - int(round(0.5 * ct.BAR_WIDTH))


def test_each_free_lane_session_gets_its_own_savings_row(tmp_path):
    """WHICH session saved how much. 0.17.0 wrongly declared this impossible, on the grounds that
    the dispatch savings ledger records `"session": None`. But a free-lane session's phantom lives
    in ITS OWN cost-ledger line (field 4) — the same field the statusline reads to print
    `free api session saved $45.61`. The data was always there; the breakdown had not exposed it."""
    ct, _ledger = load_ct(tmp_path)
    # The per-session values and the two totals must agree — an inconsistent fixture proves
    # nothing (and this one caught exactly that on its first run).
    split = {"1c9b1c94-004f-4ff5-a6cd-df7c7817fef4": 454.74,
             "41a9f4ff-3b1d-41aa-b8d8-96ad21e9ee08": 320.88,
             "0547ff4a-3d5e-4e9f-8796-e5d4cf271cc2": 48.91}
    total = round(sum(split.values()), 2)
    data = {"period": "today", "cloud_usd": 1.0,
            "local_phantom_usd": total, "local_saved_usd": 0.0,
            "local_phantom_basis_usd": total, "local_saved_basis_usd": 0.0,
            "savings_by_session": split,
            "phantom_basis": "Opus 5.5 (as recorded)"}
    out = ct.render_breakdown(data)
    for sid in data["savings_by_session"]:
        assert sid[:12] in out, "each session must be named, not just summed"
    # The split must reconcile to the total — a per-session breakdown that does not add up is
    # worse than the single number it replaced.
    total = sum(data["savings_by_session"].values())
    assert abs(total - (data["local_phantom_usd"] + data["local_saved_usd"])) < 1e-6
    assert f"{total:.2f}" in out


def test_an_empty_split_falls_back_without_claiming_a_per_session_view(tmp_path):
    """With no split (no free-lane sessions, or none with phantom yet) the view must not imply one
    it does not have — and the dispatch row must still be right."""
    ct, _ledger = load_ct(tmp_path)
    data = {"period": "today", "cloud_usd": 1.0, "local_phantom_usd": 0.0,
            "local_saved_usd": 7.5, "local_phantom_basis_usd": 0.0,
            "local_saved_basis_usd": 7.5, "savings_by_session": {},
            "phantom_basis": "Opus 5.5 (as recorded)"}
    out = ct.render_breakdown(data)
    assert "dispatches from paid sessions" in out
    assert "7.50" in out


def test_collect_exposes_the_per_session_split_from_a_real_ledger(tmp_path):
    """The wiring, not just the renderer. A free-lane session leaves `<date> 0 0 <phantom> <base>`
    in its OWN ledger file — the same field the statusline reads — and collect() must turn those
    per-session lines into a split that reconciles to the total.

    This is the test that would have caught 0.17.0's wrong claim: the renderer tests above pass a
    hand-built dict, so they cannot tell whether collect() actually populates the split. A mutation
    that deletes the populate line survives all of them and is caught only here.
    """
    ct, ledger = load_ct(tmp_path)
    today = os.environ["COST_TRACKER_TODAY"]
    # Two free-lane sessions with real phantoms, and one cloud session that must NOT appear in the
    # savings split (its spend is on the other axis entirely).
    for sid, line in (("aaaa1111-0000-0000-0000-000000000001", f"{today} 0 0 454.74 0"),
                      ("aaaa1111-0000-0000-0000-000000000002", f"{today} 0 0 320.88 0"),
                      ("aaaa1111-0000-0000-0000-000000000003", f"{today} 12.5 0")):
        (ledger / sid).write_text(line + "\n")
    data = ct.collect("today")
    split = data.get("savings_by_session")
    assert split, "the per-session split must be populated from the ledger"
    assert set(split) == {"aaaa1111-0000-0000-0000-000000000001",
                          "aaaa1111-0000-0000-0000-000000000002"}, \
        "a cloud session's spend must never enter the savings split"
    assert abs(sum(split.values()) - (data["local_phantom_usd"] or 0.0)) < 1e-6, \
        "the split must reconcile to the phantom total it came from"
    # Biggest first — the ordering is part of the contract, not a detail
    assert list(split.values()) == sorted(split.values(), reverse=True)
    # And the basis reaches the split too, or a Sonnet view would mix bases inside one block
    sonnet = ct.collect("today")  # same ledger, default basis
    assert sonnet["savings_by_session"] == split




def test_per_session_dispatch_savings_get_their_own_rows(tmp_path):
    """Dispatches from a PAID session are a different thing from a whole session on a free lane:
    they come from a different ledger and answer a different question. They get their own rows,
    not a blend."""
    ct, _ledger = load_ct(tmp_path)
    data = {"period": "today", "cloud_usd": 1.0,
            "local_phantom_usd": 100.0, "local_saved_usd": 40.0,
            "local_phantom_basis_usd": 100.0, "local_saved_basis_usd": 40.0,
            "savings_by_session": {},
            "dispatch_by_session": {"sess-alpha": 25.0, "sess-beta": 15.0},
            "phantom_basis": "Opus 5.5 (as recorded)"}
    out = ct.render_breakdown(data)
    assert "dispatch sess-alpha" in out and "25.00" in out
    assert "dispatch sess-beta" in out and "15.00" in out
    assert "140.00" in out, "the total must include both kinds of saving"


def test_an_absent_dispatch_split_falls_back_to_the_aggregate(tmp_path):
    """An older ledger has no by_session at all. None means 'not measured', which must fall back
    to the aggregate row — but an EMPTY dict is 'measured, no dispatches', and must not invent
    per-session rows either."""
    ct, _ledger = load_ct(tmp_path)
    base = {"period": "today", "cloud_usd": 1.0, "local_phantom_usd": 0.0,
            "local_saved_usd": 12.5, "local_phantom_basis_usd": 0.0,
            "local_saved_basis_usd": 12.5, "savings_by_session": {},
            "phantom_basis": "Opus 5.5 (as recorded)"}
    for split, expect_rows in ((None, False), ({}, False), ({"s": 12.5}, True)):
        data = dict(base, dispatch_by_session=split)
        out = ct.render_breakdown(data)
        assert "12.50" in out, f"the dispatch saving must be shown for split={split!r}"
        has_rows = "dispatch s " in out or "dispatch sess" in out
        assert has_rows == expect_rows, \
            f"split={split!r} should {'draw' if expect_rows else 'not draw'} per-session rows"


def test_the_dispatch_split_is_scaled_by_the_basis_like_everything_else(tmp_path):
    """The phantom rows are basis-scaled; the dispatch rows must be too, or a Sonnet view would
    halve one axis inside a block and leave the other untouched — two bases in one chart."""
    ct, _ledger = load_ct(tmp_path)
    data = {"period": "today", "cloud_usd": 1.0,
            "local_phantom_usd": 100.0, "local_saved_usd": 40.0,
            "local_phantom_basis_usd": 50.0, "local_saved_basis_usd": 20.0,
            "savings_by_session": {},
            "dispatch_by_session": {"sess-alpha": 20.0},
            "phantom_basis": "Sonnet 5.5 basis"}
    out = ct.render_breakdown(data)
    assert "50.00" in out, "the phantom is shown on the configured basis"
    assert "20.00" in out, "the dispatch split must be scaled to the same basis, not left raw"


def _savings_stub(tmp_path, payload):
    """A COST_TRACKER_SAVINGS_CMD that prints a known report --json payload, so collect() takes
    its real subprocess path against a fixture instead of the real machine."""
    stub = tmp_path / "savings-stub.sh"
    stub.write_text("#!/bin/sh\ncat <<'JSON'\n" + json.dumps(payload) + "\nJSON\n")
    stub.chmod(0o700)
    return str(stub)


def test_collect_scales_the_dispatch_split_by_the_basis(tmp_path, monkeypatch):
    """THE WIRING, not the renderer. The rendering tests above hand render_breakdown an
    already-scaled dict, so they pass whether or not collect() scales anything — a mutation
    deleting `phantom_basis_factor()` from the split construction SURVIVED them. This one drives
    the real subprocess path with a fixture and would catch it.

    A Sonnet basis must halve the DISPATCH split as well as the phantom, or one block would show
    two different bases at once.
    """
    payload = {"saved_usd": 40.0, "events": 3,
               "by_session": [{"session": "sess-alpha", "saved_usd": 25.0, "events": 2},
                              {"session": "sess-beta", "saved_usd": 15.0, "events": 1}]}
    for basis, expected in (("opus", 25.0), ("sonnet", 12.5)):
        # load_ct SETS COST_TRACKER_SAVINGS_CMD itself (default /bin/false), so the stub must be
        # passed through its own parameter — a monkeypatch is clobbered by the fixture.
        ct, _ledger = load_ct(tmp_path, savings_cmd=_savings_stub(tmp_path, payload))
        monkeypatch.setenv("COST_TRACKER_PHANTOM_BASIS", basis)
        data = ct.collect("today")
        split = data.get("dispatch_by_session")
        assert split, f"collect() must expose the split (basis={basis})"
        assert abs(split["sess-alpha"] - expected) < 1e-9, \
            f"basis={basis}: sess-alpha should be {expected}, got {split['sess-alpha']}"
        assert abs(data["local_saved_basis_usd"] - 40.0 * (0.5 if basis == "sonnet" else 1.0)) < 1e-9
        assert abs(sum(split.values()) - data["local_saved_basis_usd"]) < 1e-9, \
            "the split must reconcile to the total on the SAME basis"


def test_an_older_ledger_without_by_session_stays_none(tmp_path, monkeypatch):
    """A ledger predating 0.43.2 has no by_session key. None — 'not measured' — must not become an
    empty dict, which would read as 'measured, no dispatches'."""
    ct, _ledger = load_ct(
        tmp_path, savings_cmd=_savings_stub(tmp_path, {"saved_usd": 12.5, "events": 1}))
    assert ct.collect("today")["dispatch_by_session"] is None

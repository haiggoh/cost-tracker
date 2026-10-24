"""changelog-prepend.py — the barrier for a repeated, costly slip.

The failure it removes: adding a release entry needs the PREVIOUS version as an edit anchor, and
writing the new one there is a slip made four times in a single session (2026-10-24). Each time
the edit tool refused to match — the anchor was simply absent — so the tooling already protected
the outcome; what it could not do was stop the turn being wasted. The helper makes the anchor a
thing the script derives, so the step is no longer performed by hand.

These tests are the barrier's own regression suite: if the anchor-finding or the refusal weakens,
the slip becomes possible again.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(ROOT, "bin", "changelog-prepend.py")

A_CHANGELOG = """# Changelog

All notable changes to cost-tracker are documented here.

## [0.15.0] — 2026-09-01

### Added
- the previous release

## [0.14.0] — 2026-08-30
"""


def _run(tmp_path, *args, stdin=None, changelog=A_CHANGELOG):
    path = tmp_path / "CHANGELOG.md"
    if changelog is not None:
        path.write_text(changelog)
    return subprocess.run([sys.executable, HELPER] + list(args) + ["--file", str(path)],
                          input=stdin, capture_output=True, text=True), path


def test_it_finds_the_anchor_itself(tmp_path):
    """The whole point: the caller never names the previous version. Only the NEW one is given."""
    p, path = _run(tmp_path, "0.16.0", "A new release")
    assert p.returncode == 0, p.stderr
    text = path.read_text()
    assert text.index("## [0.16.0]") < text.index("## [0.15.0]"), \
        "the new entry must land ABOVE the previous one"
    assert "0.15.0" in p.stdout, "the script should report the anchor it found"
    # The heading carries the date and title, so the entry reads as a real release line
    assert "## [0.16.0] — " in text and "A new release" in text


def test_a_body_can_come_from_stdin_or_a_file(tmp_path):
    _, path = _run(tmp_path, "1.0.0", "With body", "--body", "-", stdin="### Added\n- thing\n")
    assert "### Added\n- thing" in path.read_text()

    body = tmp_path / "body.md"
    body.write_text("### Fixed\n- a bug\n")
    _, path2 = _run(tmp_path, "1.0.1", "From file", "--body", str(body))
    assert "### Fixed\n- a bug" in path2.read_text()


def test_a_version_already_at_the_top_is_refused_not_duplicated(tmp_path):
    """Make 0.15.0 the top entry first, then ask for it again — the refusal must fire."""
    _, path = _run(tmp_path, "0.15.0", "again")
    before = path.read_text()
    p, _ = _run(tmp_path, "0.15.0", "again", changelog=before)
    assert p.returncode == 1, "a duplicate version must be refused"
    assert "already the top entry" in p.stderr
    assert path.read_text() == before, "a refused edit must leave the file byte-identical"
    assert path.read_text().count("## [0.15.0]") == 1


def test_a_version_that_is_not_a_version_is_refused(tmp_path):
    p, _ = _run(tmp_path, "notaversion")
    assert p.returncode == 2
    assert "x.y.z" in p.stderr


def test_a_missing_file_is_reported_not_created(tmp_path):
    missing = tmp_path / "nope.md"
    p = subprocess.run([sys.executable, HELPER, "1.0.0", "t", "--file", str(missing)],
                       capture_output=True, text=True)
    assert p.returncode == 1
    assert "no such file" in p.stderr
    assert not missing.exists(), "a refused run must not create the file it could not read"


def test_help_prints_help_and_writes_nothing(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(A_CHANGELOG)
    before = path.read_text()
    p = subprocess.run([sys.executable, HELPER, "--help"], capture_output=True, text=True)
    assert p.returncode == 0
    # argparse prints lowercase "usage:"; the tool's own USAGE block is the --help TEXT, not what
    # the parser emits. Asserting the capital-U form would test my prose, not the behaviour.
    assert "usage:" in p.stdout and "--body" in p.stdout and "VERSION" in p.stdout
    assert path.read_text() == before, "--help must never write"


def test_dry_run_writes_nothing(tmp_path):
    p, path = _run(tmp_path, "2.0.0", "Dry", "--dry-run")
    assert p.returncode == 0
    assert path.read_text() == A_CHANGELOG, "--dry-run must not write"
    assert "## [2.0.0]" in p.stdout, "but it must still show what would happen"


def test_check_is_a_release_gate(tmp_path):
    _, path = _run(tmp_path, "3.0.0", "Gated")
    ok = subprocess.run([sys.executable, HELPER, "3.0.0", "--check", "--file", str(path)],
                        capture_output=True, text=True)
    assert ok.returncode == 0
    bad = subprocess.run([sys.executable, HELPER, "9.9.9", "--check", "--file", str(path)],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "not 9.9.9" in bad.stderr


def test_an_unreleased_block_stays_above_the_new_entry(tmp_path):
    """`## [Unreleased]` must remain the newest bucket. Inserting above it would date an
    unreleased section, and inserting below it would date the new release as unreleased."""
    body = """# Changelog

## [Unreleased]

### Added
- not yet shipped

## [0.15.0] — 2026-09-01
"""
    _, path = _run(tmp_path, "0.16.0", "Real release", changelog=body)
    text = path.read_text()
    assert text.index("## [Unreleased]") < text.index("## [0.16.0]"), \
        "Unreleased must stay above a dated release"
    assert text.index("## [0.16.0]") < text.index("## [0.15.0]")


def test_it_leaves_the_file_mode_alone(tmp_path):
    """In-place write through the same inode: a tmp-file-plus-mv would reset a 600 file to the
    umask default, which is how a secrets-shaped config becomes world-readable."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text(A_CHANGELOG)
    os.chmod(path, 0o600)
    _run(tmp_path, "4.0.0", "Mode", changelog=A_CHANGELOG)
    mode = oct(path.stat().st_mode & 0o777)
    assert mode == "0o600", f"mode must survive the rewrite, got {mode}"


def test_a_file_with_no_numbered_heading_is_refused_not_guessed(tmp_path):
    """No `## [x.y.z]` means no anchor. Guessing one (inserting at line 1) would be the same
    class of error this tool exists to remove."""
    p, _ = _run(tmp_path, "1.0.0", "First", changelog="# Changelog\n\nNothing yet.\n")
    assert p.returncode == 1
    assert "no numbered" in p.stderr


@pytest.mark.parametrize("version,expected", [("v1.2.3", "1.2.3"), ("V1.2.3", "1.2.3"),
                                             ("1.2.3", "1.2.3")])
def test_a_leading_v_is_normalised(tmp_path, version, expected):
    """A tag is `v0.18.0`; the changelog heading is `0.18.0`. Accepting both stops the mismatch
    being a silent no-op."""
    p, path = _run(tmp_path, version, "Tag form")
    assert p.returncode == 0, p.stderr
    assert f"## [{expected}]" in path.read_text()

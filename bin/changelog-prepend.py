#!/usr/bin/env python3
"""changelog-prepend.py — insert a release section at the top of a Keep-a-Changelog file.

WHY THIS EXISTS. Adding a release entry means writing a new `## [x.y.z]` heading above the
current one, and the ANCHOR for that edit is the previous version number. Naming it by hand goes
wrong in a specific, repeated way: you write the NEW version into the anchor, the edit finds
nothing, and you have wasted a turn. Measured 2026-10-24: four failures in one session, all the
same slip, all caught only by the edit tool refusing to match.

So this script removes the step rather than reminding about it: it reads the file, finds the top
numbered heading ITSELF, and inserts above it. The caller never names the previous version.

Safe by construction:
  - arguments are parsed BEFORE any file is opened, so `--help` cannot write;
  - the file is rewritten through the SAME inode (open "w"), so mode and ownership survive;
  - an entry whose version is already at the top is REFUSED, not silently duplicated;
  - `--dry-run` prints the result without writing.
"""
import argparse
import os
import re
import sys

USAGE = """changelog-prepend.py — insert a release section at the top of a Keep-a-Changelog file.

Usage:
  changelog-prepend.py VERSION "Title" [--body FILE|-] [--file PATH] [--dry-run] [--check]

Arguments:
  VERSION                 the new version, `x.y.z` (or `vX.Y.Z`; the leading v is normalised away)
  "Title"                 the release title, written into the `## [VERSION] — date — Title` heading

Options:
  --body FILE             markdown body for the section. `-` reads stdin. Without it the section
                          is just the heading, ready to be filled in by hand.
  --file PATH             the changelog to edit (default `CHANGELOG.md` in the cwd)
  --date YYYY-MM-DD      the date in the heading (default: today, UTC)
  --dry-run               print the resulting top of the file, write nothing
  --check                 verify VERSION is the top entry and exit; no write (for release gates)

Environment:
  None. Every input is an argument, so a run is reproducible from its own command line.

Exit status is 0 on success, 2 on a usage error, and 1 on a refused edit (already-present
version, or a file with no numbered heading to insert above)."""

HEADING_RE = re.compile(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\](.*)$", re.M)
# A prose section such as `## [Unreleased]` is not an anchor: inserting above a non-numbered
# heading would put a dated release above a bucket that must stay newest.
NUMBERED_HEADING_RE = re.compile(r"^## \[[0-9]+\.[0-9]+\.[0-9]+\]", re.M)


def normalise(version):
    v = (version or "").strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    return v


def is_version(s):
    return bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", s or ""))


def top_version(text):
    """The version of the first NUMBERED `## [x.y.z]` heading, or None.

    `## [Unreleased]` does not match and is skipped — but note it means the first numbered
    heading is NOT necessarily at the top of the file, which is exactly why the anchor must be
    found by search rather than by reading a line count.
    """
    m = HEADING_RE.search(text)
    return m.group(1) if m else None


def build_section(version, title, body, day):
    head = f"## [{version}] — {day}"
    if title:
        head += f" — {title}"
    parts = [head, ""]
    if body:
        b = body.rstrip("\n")
        if b:
            parts.extend([b, ""])
    return "\n".join(parts)


def prepend(path, version, title, body, day):
    """Insert the section and return the new text. Raises ValueError on a refused edit."""
    with open(path, "r", errors="replace") as f:
        text = f.read()
    m = HEADING_RE.search(text)
    if not m:
        raise ValueError(f"{path}: no numbered '## [x.y.z]' heading to insert above. "
                         "Add the first release entry by hand — this tool only inserts above "
                         "an existing one.")
    current = m.group(1)
    if current == version:
        raise ValueError(f"{path}: {version} is already the top entry. Refusing to duplicate it — "
                         "if you meant to rewrite it, edit that section in place.")
    section = build_section(version, title, body, day)
    # Insert IMMEDIATELY above the numbered heading, wherever it is: an `## [Unreleased]` block
    # above it must stay above.
    return text[:m.start()] + section + text[m.start():], current


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="changelog-prepend.py",
        description="Insert a release section at the top of a Keep-a-Changelog file.",
        epilog="The anchor is read from the file, never passed in — see --help.")
    ap.add_argument("version", nargs="?", help="the new version, x.y.z")
    ap.add_argument("title", nargs="?", default="", help="the release title")
    ap.add_argument("--body", metavar="FILE", default=None,
                    help="markdown body for the section; '-' reads stdin")
    ap.add_argument("--file", metavar="PATH", default="CHANGELOG.md",
                    help="the changelog to edit (default CHANGELOG.md)")
    ap.add_argument("--date", metavar="YYYY-MM-DD", dest="day", default=None,
                    help="the date in the heading (default: today, UTC)")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="print the result, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="verify VERSION is the top entry and exit; writes nothing")
    args = ap.parse_args(argv)

    # --help and an unknown flag exit inside parse_args above, BEFORE anything is opened.
    if args.version is None:
        ap.error("a VERSION is required (or use --help)")
    version = normalise(args.version)
    if not is_version(version):
        ap.error(f"VERSION must look like x.y.z, got {args.version!r}")
    path = args.file
    if not os.path.isfile(path):
        print(f"changelog-prepend.py: {path}: no such file", file=sys.stderr)
        return 1
    try:
        with open(path, "r", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"changelog-prepend.py: {path}: {e}", file=sys.stderr)
        return 1

    if args.check:
        got = top_version(text)
        if got == version:
            print(f"{path}: {version} is the top entry")
            return 0
        print(f"{path}: top entry is {got or '<none>'}, not {version}", file=sys.stderr)
        return 1

    body = ""
    if args.body:
        if args.body == "-":
            body = sys.stdin.read()
        else:
            try:
                with open(args.body, "r", errors="replace") as f:
                    body = f.read()
            except OSError as e:
                print(f"changelog-prepend.py: --body {args.body}: {e}", file=sys.stderr)
                return 1
    day = args.day
    if day is None:
        import datetime
        day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    try:
        new_text, previous = prepend(path, version, args.title, body, day)
    except ValueError as e:
        print(f"changelog-prepend.py: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        sys.stdout.write(new_text[:2000])
        return 0

    # Same inode: a tmp-file-plus-mv would reset the mode (and on a repo file, the ownership
    # story) of a file that may be 600.
    try:
        with open(path, "w") as f:
            f.write(new_text)
    except OSError as e:
        print(f"changelog-prepend.py: {path}: {e}", file=sys.stderr)
        return 1
    print(f"{path}: inserted [{version}] above [{previous}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

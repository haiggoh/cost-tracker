#!/usr/bin/env python3
"""Build the portable package for cost-tracker (COORDINATION C1/C2, Option B).

Host package (repo root) unchanged: bin/ stays on the PATH in Code. This script generates
the committed ``portable/`` deterministically — never hand-edit portable/; ``--check`` fails
on drift.

Portable layout: no top-level bin/ (blocks Chat/Cowork installs). The CLI and its sibling
modules (loaded from the CLI's own dir via SourceFileLoader) move bin/ -> scripts/, so they
resolve each other inside scripts/ unchanged. The skill/command docs reference the bare
PATH commands (`cost-tracker`, `budget-tally`); those mentions are rewritten to the
scripts/ invocation — inside fenced sh blocks and in backticked mentions. Host-only files
(the statusline wrapper, the capture shim, the shim resolver, changelog tooling, install/)
stay out of the package.

Usage:
  build-portable.py            build portable/ in place (temp build + swap)
  build-portable.py --check    rebuild in a temp dir (stamped with the committed
                               source_commit/source_repo) and diff; exit 1 on drift
  build-portable.py --zip [--out DIR]   emit dist/<name>-portable.zip (portable/ untouched)
  build-portable.py --commit SHA  record this source_commit

Env: none. Stdlib only. Requires git only when --commit is not given.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

SCHEMA = "haiggoh-portable-build/1"
TRANSFORM_VERSION = "1"
BUILDER = "scripts/build-portable.py"
BUILDER_VERSION = "1.0.0"
FALLBACK_SOURCE_REPO = "haiggoh/cost-tracker"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
IGNORE_DIRS = {"__pycache__", ".pytest_cache", ".git"}
IGNORE_SUFFIXES = (".pyc", ".pyo")

CT = "python3 '$CLAUDE_PLUGIN_ROOT/scripts/cost-tracker'"
BT = "python3 '$CLAUDE_PLUGIN_ROOT/scripts/budget-tally.py'"

COPY_FILES = [
    ".claude-plugin/plugin.json",
    "commands/cost-today.md",
    "hooks/hooks.json",
    "hooks/session-end-reconcile.py",
    "scripts/budget-tally.py",
    "scripts/cost-tracker",
    "scripts/cost_connection.py",
    "scripts/cost_tracker_menu.py",
    "scripts/proxy_prices.py",
    "scripts/shared_costs.py",
    "scripts/shared_snapshot.py",
    "skills/cost-tracker/SKILL.md",
    "docs/history-and-incidents.md",
]
HOST_TO_PORTABLE = {f"scripts/{n}": f"bin/{n}" for n in
                     ("cost-tracker", "cost_connection.py", "budget-tally.py",
                      "proxy_prices.py", "cost_tracker_menu.py", "shared_costs.py",
                      "shared_snapshot.py")}
# (old, new) literal pairs; "fence:" prefix = apply only inside ``` fenced blocks;
# "re:" prefix (after optional "fence:") = regex with a replacement CALLABLE
TRANSFORMS = {
    "skills/cost-tracker/SKILL.md": [
        ("fence:re:" + r"(?m)^(cost-tracker|budget-tally)(\s)",
         lambda m: (CT if m.group(1) == "cost-tracker" else BT) + m.group(2)),
        ("`cost-tracker ", "`" + CT + " "),
    ],
    "commands/cost-today.md": [
        ("fence:re:" + r"(?m)^(cost-tracker|budget-tally)(\s)",
         lambda m: (CT if m.group(1) == "cost-tracker" else BT) + m.group(2)),
        ("`cost-tracker ", "`" + CT + " "),
    ],
}

COMPONENTS = [
    {"path": ".claude-plugin/plugin.json", "kind": "manifest",
     "adaptation_class": "mechanical", "evidence_level": "source-inspected",
     "surfaces": {"chat": True, "cowork": True, "code": True},
     "notes": "same name+version as the host manifest; an installed marketplace plugin wins "
              "over a synced copy in Code, so no double-run"},
    {"path": "skills/cost-tracker/SKILL.md", "kind": "skill",
     "adaptation_class": "mechanical", "evidence_level": "source-inspected",
     "surfaces": {"chat": True, "cowork": True, "code": True},
     "notes": "PATH-command references (cost-tracker/budget-tally) rewritten to the scripts/ "
              "invocation, in fenced sh blocks and backticked mentions"},
    {"path": "docs/history-and-incidents.md", "kind": "data",
     "adaptation_class": "native-unchanged", "evidence_level": "source-inspected",
     "surfaces": {"chat": True, "cowork": True, "code": True},
     "notes": "narrative moved out of SKILL.md for the word budget; bundled so the SKILL.md "
              "links resolve inside the portable package"},
    {"path": "commands/cost-today.md", "kind": "command",
     "adaptation_class": "mechanical", "evidence_level": "source-inspected",
     "surfaces": {"chat": True, "cowork": True, "code": True},
     "notes": "slash command; same command-reference rewrite as the skill"},
    {"path": "hooks/hooks.json", "kind": "hooks",
     "adaptation_class": "native-unchanged", "evidence_level": "source-inspected",
     "surfaces": {"chat": False, "cowork": True, "code": True},
     "notes": "SessionEnd reconcile hook; hooks stay in hooks/"},
    {"path": "hooks/session-end-reconcile.py", "kind": "hook-script",
     "adaptation_class": "native-unchanged", "evidence_level": "source-inspected",
     "surfaces": {"chat": False, "cowork": True, "code": True},
     "notes": "reads the ledger stores (user-level data, correct per the waypoints pilot); portable as-is"},
    {"path": "scripts/cost-tracker", "kind": "script",
     "adaptation_class": "mechanical", "evidence_level": "source-inspected",
     "surfaces": {"chat": False, "cowork": True, "code": True},
     "notes": "CLI moved bin/ -> scripts/ per C1; loads its sibling modules from its own dir "
              "(scripts/ in portable) — verbatim copy works"},
    {"path": "scripts/", "kind": "library",
     "adaptation_class": "native-unchanged", "evidence_level": "source-inspected",
     "surfaces": {"chat": False, "cowork": True, "code": True},
     "notes": "cost_connection.py, budget-tally.py, proxy_prices.py, cost_tracker_menu.py — "
              "the CLI's SourceFileLoader closure, sibling-resolved inside scripts/"},
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git(args, cwd):
    try:
        return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                              check=True).stdout.strip()
    except FileNotFoundError:
        sys.exit("build-portable.py: git binary not found — pass --commit <sha> explicitly")
    except subprocess.CalledProcessError as e:
        sys.exit(f"build-portable.py: git {' '.join(args)} failed: {e.stderr.strip()}")


def source_identity(root, commit_override):
    commit = commit_override or git(["rev-parse", "HEAD"], root)
    remote = ""
    try:
        remote = git(["remote", "get-url", "origin"], root)
    except SystemExit:
        remote = ""
    m = re.match(r"(?:git@[^:]+:|https://[^/]+/)([^/]+/[^/]+?)(?:\.git)?$", remote or "")
    return (m.group(1) if m else FALLBACK_SOURCE_REPO), commit


def _apply_rules(text, rules):
    for old, new in rules:
        fence = old.startswith("fence:")
        if fence:
            old = old[len("fence:"):]
        if old.startswith("re:"):
            pat = old[3:]
            if fence:
                def _sub(m, pat=pat, new=new):
                    return re.sub(pat, new, m.group(0))
                text = re.sub(r"```[^\n]*\n.*?```", _sub, text, flags=re.S)
            else:
                text = re.sub(pat, new, text)
        else:
            if fence:
                def _sub2(m, old=old, new=new):
                    return m.group(0).replace(old, new)
                text = re.sub(r"```[^\n]*\n.*?```", _sub2, text, flags=re.S)
            else:
                assert old in text, f"transform anchor missing: {old[:60]!r}"
                text = text.replace(old, new)
    return text


def build_tree(root, dest):
    with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    for rel in COPY_FILES:
        host_rel = HOST_TO_PORTABLE.get(rel, rel)
        src = os.path.join(root, host_rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if rel in TRANSFORMS:
            with open(src, encoding="utf-8") as i:
                text = i.read()
            text = _apply_rules(text, TRANSFORMS[rel])
            with open(dst, "w", encoding="utf-8") as o:
                o.write(text)
        else:
            with open(src, "rb") as i, open(dst, "wb") as o:
                shutil.copyfileobj(i, o)
        os.chmod(dst, 0o755 if rel.endswith((".sh", ".py")) or "/" not in rel and rel.startswith("scripts/") and "." not in rel else 0o644)
    return manifest


def write_build_json(dest, root, commit_override, repo_override=None):
    source_repo, commit = source_identity(root, commit_override)
    if repo_override:
        source_repo = repo_override
    files = {}
    for dirpath, dirnames, filenames in os.walk(dest):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in sorted(filenames):
            if fn.endswith(IGNORE_SUFFIXES):
                continue
            p = os.path.join(dirpath, fn)
            files[os.path.relpath(p, dest).replace(os.sep, "/")] = sha256_file(p)
    doc = {
        "schema": SCHEMA, "source_repo": source_repo, "source_commit": commit,
        "builder": BUILDER, "builder_version": BUILDER_VERSION,
        "transform_version": TRANSFORM_VERSION, "files": files, "components": COMPONENTS,
    }
    with open(os.path.join(dest, "PORTABLE-BUILD.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def walk_files(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in sorted(filenames):
            if fn.endswith(IGNORE_SUFFIXES):
                continue
            p = os.path.join(dirpath, fn)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out


def diff_trees(a, b):
    fa, fb = walk_files(a), walk_files(b)
    out = []
    for rel in sorted(set(fa) | set(fb)):
        pa, pb = fa.get(rel), fb.get(rel)
        if pa is None or pb is None or sha256_file(pa) != sha256_file(pb) \
                or (os.lstat(pa).st_mode & 0o777) != (os.lstat(pb).st_mode & 0o777):
            out.append(rel)
    return out


def write_zip(build_dir, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    zpath = os.path.join(out_dir, f"{name}-portable.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(walk_files(build_dir)):
            p = os.path.join(build_dir, rel)
            zi = zipfile.ZipInfo(rel, date_time=ZIP_EPOCH)
            zi.external_attr = ((0o755 if rel.endswith((".sh", ".py")) else 0o644) & 0xFFFF) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as f:
                z.writestr(zi, f.read())
    return zpath


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build-portable.py",
                                 description="Generate the committed portable/ package (COORDINATION C1/C2). Never hand-edit portable/.")
    ap.add_argument("--check", action="store_true",
                    help="rebuild in a temp dir stamped with the COMMITTED source_commit/source_repo and diff; exit 1 on drift")
    ap.add_argument("--zip", action="store_true", help="also emit the upload zip from a temp build (portable/ untouched)")
    ap.add_argument("--out", metavar="DIR", default=None, help="zip output dir (default dist/)")
    ap.add_argument("--commit", metavar="SHA", default=None, help="record this source_commit")
    args = ap.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    portable = os.path.join(root, "portable")
    check_commit = args.commit
    check_repo = None
    if args.check and check_commit is None and os.path.isfile(os.path.join(portable, "PORTABLE-BUILD.json")):
        with open(os.path.join(portable, "PORTABLE-BUILD.json"), encoding="utf-8") as f:
            committed = json.load(f)
        check_commit = committed.get("source_commit")
        check_repo = committed.get("source_repo")

    with tempfile.TemporaryDirectory() as tmp:
        fresh = os.path.join(tmp, "portable")
        os.makedirs(fresh)
        build_tree(root, fresh)
        write_build_json(fresh, root, check_commit or args.commit, check_repo)
        if args.check:
            if not os.path.isdir(portable):
                print("✗ portable/ does not exist — run scripts/build-portable.py first")
                return 1
            drift = diff_trees(fresh, portable)
            if drift:
                print("✗ portable/ drifted from a fresh build:")
                for rel in drift:
                    print("  ", rel)
                print("  fix: run scripts/build-portable.py and commit the result")
                return 1
            print("✓ portable/ matches a fresh build")
            return 0
        if args.zip:
            with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
                name = json.load(f)["name"]
            zpath = write_zip(fresh, args.out or os.path.join(root, "dist"), name)
            print(f"✓ wrote {zpath} (from a temp build; portable/ untouched)")
            return 0
        if os.path.isdir(portable):
            shutil.rmtree(portable)
        try:
            os.rename(fresh, portable)
        except OSError:
            shutil.copytree(fresh, portable)
            shutil.rmtree(fresh)
    print(f"✓ built portable/ ({len(walk_files(portable))} files) + PORTABLE-BUILD.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Register (or remove) the fake-SAMPIC custom pages in an experiment's ODB.

    python -m install.register_pages            # install / refresh
    python -m install.register_pages --list     # show what would be written
    python -m install.register_pages --check    # validate, touch nothing
    python -m install.register_pages --remove   # take them out again

Keys are written ONE AT A TIME BY FULL PATH. Never `odb_set("/Custom", {...})`:
odb_set defaults to remove_unspecified_keys=True, so handing it a dict for the
whole subtree would delete every custom page belonging to anyone else in the
experiment. That is an unrecoverable thing to do to somebody's setup and the
call looks perfectly reasonable.

By the same logic --remove only deletes keys that point into THIS checkout, so
it cannot take out a page that merely shares a name.
"""

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from install import manifest
else:
    from . import manifest

CUSTOM = "/Custom"


def _client(name="fake-sampic-pages"):
    import midas.client
    return midas.client.MidasClient(name)


def do_list(base, prefix) -> int:
    for key, path, entry in manifest.resolved(base, prefix):
        flag = "menu" if entry.in_menu else "asset"
        print(f"  {CUSTOM}/{key}")
        print(f"      {flag:5s}  {path}")
        print(f"      {entry.description}")
    return 0


def do_check(base) -> int:
    problems = manifest.check_all(base)
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        return 1
    print(f"manifest OK: {len(manifest.ENTRIES)} entries")
    return 0


def do_install(base, prefix, replace) -> int:
    problems = manifest.check_all(base)
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}", file=sys.stderr)
        return 1

    client = _client()
    written = skipped = 0
    for key, path, entry in manifest.resolved(base, prefix):
        odb_path = f"{CUSTOM}/{key}"
        existing = None
        if client.odb_exists(odb_path):
            try:
                existing = str(client.odb_get(odb_path))
            except Exception:
                existing = None
        if existing and not replace:
            same_tree = os.path.abspath(existing).startswith(manifest.pages_dir(base))
            if not same_tree:
                # Somebody else owns this key. Overwriting it would silently
                # replace their page with ours.
                print(f"  [SKIP] {odb_path} points outside this checkout:\n"
                      f"         {existing}\n"
                      f"         pass --replace to take it over", file=sys.stderr)
                skipped += 1
                continue
        client.odb_set(odb_path, path)
        written += 1
        print(f"  {odb_path} -> {path}")

    client.disconnect()
    print(f"\n{written} key(s) written" + (f", {skipped} skipped" if skipped else ""))
    if written:
        print("Open mhttpd and the pages appear in the side menu.")
        print("Note: an edited page may be cached for 24 h -- bump the ?v= in its HTML.")
    return 1 if skipped else 0


def do_remove(base, prefix) -> int:
    client = _client()
    root = manifest.pages_dir(base)
    removed = 0
    for key, _path, _entry in manifest.resolved(base, prefix):
        odb_path = f"{CUSTOM}/{key}"
        if not client.odb_exists(odb_path):
            continue
        try:
            value = str(client.odb_get(odb_path))
        except Exception:
            value = ""
        if not os.path.abspath(value).startswith(root):
            print(f"  [SKIP] {odb_path} does not point into {root}", file=sys.stderr)
            continue
        client.odb_delete(odb_path)
        removed += 1
        print(f"  removed {odb_path}")
    client.disconnect()
    print(f"\n{removed} key(s) removed")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="show what would be written")
    g.add_argument("--check", action="store_true", help="validate the manifest only")
    g.add_argument("--remove", action="store_true", help="remove our keys")
    p.add_argument("--replace", action="store_true",
                   help="overwrite keys that point outside this checkout")
    p.add_argument("--prefix", default="",
                   help="prefix for menu page names, to coexist with another install")
    p.add_argument("--pages-dir", default=None, help="override the pages/ directory")
    args = p.parse_args(argv)

    if args.list:
        return do_list(args.pages_dir, args.prefix)
    if args.check:
        return do_check(args.pages_dir)
    if args.remove:
        return do_remove(args.pages_dir, args.prefix)
    return do_install(args.pages_dir, args.prefix, args.replace)


if __name__ == "__main__":
    sys.exit(main())

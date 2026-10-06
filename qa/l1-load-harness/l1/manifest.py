#!/usr/bin/env python3
"""Deterministic evidence manifest (sha256sum format, paths sorted bytewise, MANIFEST.sha256 itself excluded).
  manifest.py write DIR      -> DIR/MANIFEST.sha256 ; verify with: (cd DIR && sha256sum -c MANIFEST.sha256)
  manifest.py verify DIR     -> exit 0 iff every listed file matches and no unlisted file exists"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import sha256_file  # noqa: E402

NAME = "MANIFEST.sha256"


def build(d):
    files = []
    for root, _, names in os.walk(d):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), d)
            if rel != NAME:
                files.append(rel)
    files.sort(key=lambda s: s.encode())
    return "".join(f"{sha256_file(os.path.join(d, f))}  {f}\n" for f in files)


def write(d):
    text = build(d)
    with open(os.path.join(d, NAME), "w") as f:
        f.write(text)
    return text


def verify(d):
    want = open(os.path.join(d, NAME)).read()
    return want == build(d)


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if len(a) != 2 or a[0] not in ("write", "verify"):
        print(__doc__)
        return 2
    if a[0] == "write":
        print(f"{len(write(a[1]).splitlines())} files")
        return 0
    ok = verify(a[1])
    print("MANIFEST OK" if ok else "MANIFEST MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

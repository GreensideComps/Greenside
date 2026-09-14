"""Extract the real signup modal from the Liquid snippet into a standalone page
so it can be driven in a browser exactly as shipped.

Output goes to the system temp dir so running tests never dirties the working tree.
"""
import pathlib
import re
import tempfile

SRC = pathlib.Path(__file__).resolve().parent.parent / "snippets" / "greenside-signup-modal.liquid"
OUT = pathlib.Path(tempfile.gettempdir()) / "greenside-modal-harness.html"


def build():
    raw = SRC.read_text()

    # Drop only the {% doc %} block; everything after it is plain HTML/CSS/JS.
    body = re.sub(r"\{%\s*doc\s*%\}.*?\{%\s*enddoc\s*%\}", "", raw, flags=re.S).strip()

    if "{%" in body or "{{" in body:
        raise SystemExit(
            "Liquid found in the modal body. The harness can only serve plain HTML, so these "
            "tests would no longer exercise the shipped code. Render it another way, or move "
            "the Liquid out of the modal."
        )
    if "<script>" not in body or "<dialog" not in body:
        raise SystemExit("Modal extraction failed: expected a <dialog> and a <script> block.")

    OUT.write_text(
        "<!doctype html>\n<html><head><meta charset='utf-8'>"
        "<title>Greenside modal harness</title></head>\n<body>\n"
        "<button data-greenside-signup-trigger>GET MY DOUBLE ENTRIES</button>\n"
        + body
        + "\n</body></html>\n"
    )
    return OUT, len(body)


if __name__ == "__main__":
    path, size = build()
    print(f"harness written: {path} ({size} chars of real modal)")

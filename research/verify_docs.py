"""Provenance for the fund PDFs in research/docs/funds.

Every one of these files came from a rolling URL: the same URL serves a
different edition later, so a re-download does not reproduce the snapshot and
nothing in the filename records which edition is on disk. The manifest pins
each file by SHA-256 so a swapped or re-fetched document fails loudly instead
of silently changing the parsed portfolio.

    python3 verify_docs.py            # check every file against the manifest
    python3 verify_docs.py --rebuild  # re-pin after a deliberate refresh
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
FUNDS = DOCS / "funds"
SOURCES = DOCS / "sources.json"
MANIFEST = DOCS / "manifest.json"

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


class ProvenanceError(Exception):
    """A file on disk is not the edition the manifest pinned."""


def sha256(path: Path) -> str:
    """Hash a file in chunks.

    Args:
        path: File to hash.

    Returns:
        Lowercase hex digest.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def declared() -> dict[str, dict]:
    """Read the per-file provenance sources.json declares.

    Returns:
        Filename -> {source_url, retrieved, as_of, url_stability, description}.
        as_of is ISO where sources.json states a date, else None.
    """
    src = json.loads(SOURCES.read_text())
    out: dict[str, dict] = {}

    fs = src["factsheets"]
    for name, url in fs["files"].items():
        out[name] = {
            "source_url": url,
            "retrieved": fs["retrieved"],
            "as_of": iso(fs["as_of"]),
            "url_stability": "rolling",
            "description": f"factsheet, {fs['as_of']}",
        }

    st = src["statutory_reports"]
    for name, desc in st["files"].items():
        out[name] = {
            "source_url": st["url_pattern"],
            "retrieved": st["retrieved"],
            "as_of": iso(desc),
            "url_stability": "rolling",
            "description": desc,
        }
    return out


def iso(text: str) -> str | None:
    """Pull a DD.MM.YYYY date out of text and return it as ISO.

    Args:
        text: String that may contain a German-format date.

    Returns:
        YYYY-MM-DD, or None if no date is present.
    """
    m = DATE_RE.search(text)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def build() -> dict:
    """Hash every declared file and assemble the manifest.

    Returns:
        The manifest document.

    Raises:
        ProvenanceError: A file sources.json declares is missing from disk.
    """
    files = {}
    for name, meta in sorted(declared().items()):
        path = FUNDS / name
        if not path.exists():
            raise ProvenanceError(f"{name}: declared in sources.json but not in {FUNDS}")
        files[name] = {**meta, "bytes": path.stat().st_size, "sha256": sha256(path)}
    return {
        "note": "Pins each fund PDF to the exact edition it was retrieved as. "
                "Every source_url here is rolling and will serve a different "
                "document later, so these files cannot be re-fetched. Rebuild "
                "only when deliberately refreshing a snapshot.",
        "files": files,
    }


def check(names: list[str] | None = None) -> list[str]:
    """Verify files on disk against the manifest.

    Args:
        names: Filenames to check. None checks every pinned file.

    Returns:
        The names verified.

    Raises:
        ProvenanceError: The manifest is missing, a file is unpinned, absent,
            or its hash does not match the pinned edition. A full check also
            fails on any PDF in FUNDS that carries no provenance at all.
    """
    if not MANIFEST.exists():
        raise ProvenanceError(f"{MANIFEST} missing — run verify_docs.py --rebuild")
    pinned = json.loads(MANIFEST.read_text())["files"]
    targets = sorted(pinned) if names is None else names

    bad = []
    if names is None:
        for path in sorted(FUNDS.glob("*.pdf")):
            if path.name not in pinned:
                bad.append(f"{path.name}: on disk with no provenance — "
                           f"declare it in sources.json, then rebuild")
    for name in targets:
        entry = pinned.get(name)
        if entry is None:
            bad.append(f"{name}: not pinned in the manifest")
            continue
        path = FUNDS / name
        if not path.exists():
            bad.append(f"{name}: pinned but missing from {FUNDS}")
            continue
        got = sha256(path)
        if got != entry["sha256"]:
            bad.append(
                f"{name}: content changed — pinned {entry['sha256'][:12]} "
                f"({entry['description']}), on disk {got[:12]}. The source URL is "
                f"rolling, so this is a different edition, not a repaired download.")
    if bad:
        raise ProvenanceError("provenance check failed:\n  " + "\n  ".join(bad))
    return targets


def main() -> None:
    if "--rebuild" in sys.argv:
        m = build()
        MANIFEST.write_text(json.dumps(m, indent=2) + "\n")
        print(f"pinned {len(m['files'])} files -> {MANIFEST}")
        return
    names = check()
    print(f"OK {len(names)} files match their pinned editions")


if __name__ == "__main__":
    main()

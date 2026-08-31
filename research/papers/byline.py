"""Nesting-aware byline extraction for Anthropic's research channels.

Anthropic renders author lists in three different markups across two sites, so
byline parsing is separated from the harvest driver and tested independently.
Regex over nested ``<div>``s silently returns nothing on two of the three forms;
this module walks the tag stream instead.

Forms handled:
  1. ``div.section-authors``  - alignment blog and Circuits Updates. Names with
     superscript keys, a floated date, and a footnote legend mapping keys to
     affiliations. May appear several times per page (one per sub-study).
  2. ``p.authors`` + ``p.affiliations`` - older alignment-blog articles.
  3. ``span.author`` inside ``div.d-byline`` - transformer-circuits papers,
     where ``*`` marks a core contributor.
"""

from __future__ import annotations

import html as html_mod
import re
from html.parser import HTMLParser

DATE_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+(\d{1,2}),\s*(\d{4})$"
)
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "January February March April May June July August September "
        "October November December".split()
    )
}


VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "col"}


class _ClassCollector(HTMLParser):
    """Collect the inner HTML of every element carrying a given CSS class."""

    def __init__(self, wanted: str) -> None:
        super().__init__(convert_charrefs=False)
        self.wanted = wanted
        self.out: list[str] = []
        self._stack: list[str | None] = []
        self._buf: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        classes = dict(attrs).get("class", "") or ""
        opening = self.get_starttag_text() or ""
        if self._depth:
            self._buf.append(opening)
        if tag in VOID:
            return  # no matching end tag; pushing would corrupt the stack
        if self.wanted in classes.split():
            self._depth += 1
            self._stack.append(tag)
        elif self._depth:
            self._stack.append(None)

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        opened = self._stack.pop() if self._stack else None
        if opened is not None and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self.out.append("".join(self._buf))
                self._buf = []
                return
        self._buf.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs) -> None:
        if self._depth:
            self._buf.append(self.get_starttag_text() or "")

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._buf.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._depth:
            self._buf.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._depth:
            self._buf.append(f"&#{name};")


def elements_by_class(page_html: str, css_class: str) -> list[str]:
    """Return the inner HTML of every element with ``css_class``.

    Args:
        page_html: Raw HTML of a page.
        css_class: Exact class token to match.

    Returns:
        Inner HTML of each match, in document order.
    """
    p = _ClassCollector(css_class)
    p.feed(page_html)
    return p.out


def _lines(fragment: str) -> list[str]:
    """Flatten a byline fragment to text lines, keeping superscripts as ``^{x}``."""
    t = fragment.replace("\n", " ")
    t = re.sub(
        r"(?is)<sup[^>]*>(.*?)</sup>",
        lambda m: "^{" + re.sub(r"<[^>]+>", "", m.group(1)).replace(",", "|").strip() + "}",
        t,
    )
    t = re.sub(r"(?i)<h[1-6][^>]*>.*?</h[1-6]>", " ", t, flags=re.S)
    for uni, digit in {"\u00b9": "1", "\u00b2": "2", "\u00b3": "3"}.items():
        t = t.replace(uni, "^{" + digit + "}")
    t = re.sub(r"(?i)<(div|p|br)\b[^>]*>", "\n", t)
    t = re.sub(r"(?i)</(div|p)>", "\n", t)
    t = html_mod.unescape(re.sub(r"<[^>]+>", "", t))
    t = t.replace("\xa0", " ")
    return [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n") if ln.strip()]


def _legend(line: str) -> dict[str, str]:
    """Parse an affiliation legend line into {key: affiliation}."""
    out: dict[str, str] = {}
    for key, label in re.findall(r"\^\{(\w+)\}\s*([^;^]+)", line):
        label = re.split(r"\.\s+[A-Z]", label)[0]
        out[key] = label.strip(" ;,.")
    return out


def parse_generic_byline(fragment: str, legend: dict[str, str] | None = None) -> dict:
    """Parse a ``section-authors`` or ``p.authors`` fragment.

    Args:
        fragment: Inner HTML of the byline element.
        legend: Affiliation map from a sibling element, when not inline.

    Returns:
        Dict with ``authors`` (name, marks, affiliations, role) and ``date``
        (ISO string or None).
    """
    legend = dict(legend or {})
    lines = _lines(fragment)
    name_lines: list[str] = []
    iso_date = None

    for ln in lines:
        stripped = ln.strip()
        m = DATE_RE.match(stripped)
        if m:
            iso_date = f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
            continue
        if stripped.startswith("^{"):
            legend.update(_legend(stripped))
            continue
        if re.match(r"(?i)^(correspondence|work done|\*?\s*equal|authors|affiliations)\b", stripped):
            continue
        if re.search(r"[A-Za-z]", stripped):
            # A trailing floated date can land on the names line.
            dm = re.search(
                r"(January|February|March|April|May|June|July|August|September"
                r"|October|November|December)\s+(\d{1,2}),\s*(\d{4})$",
                stripped,
            )
            if dm:
                iso_date = f"{dm.group(3)}-{MONTHS[dm.group(1)]:02d}-{int(dm.group(2)):02d}"
                stripped = stripped[: dm.start()].strip()
            if stripped:
                name_lines.append(stripped)

    if not name_lines:
        return {"authors": [], "date": iso_date}

    role = "author"
    authors: list[dict] = []
    for chunk in re.split(r"[;,]", ", ".join(name_lines)):
        chunk = chunk.strip()
        if not chunk:
            continue
        if re.match(r"(?i)^edited by\b", chunk):
            role = "editor"
            chunk = re.sub(r"(?i)^edited by\s*", "", chunk).strip()
        first_letter = re.search(r"[A-Za-z]", chunk)
        cut = first_letter.start() if first_letter else len(chunk)
        lead_marks = [
            m for grp in re.findall(r"\^\{([^}]*)\}", chunk[:cut]) for m in grp.split("|")
        ]
        marks = [
            m for grp in re.findall(r"\^\{([^}]*)\}", chunk[cut:]) for m in grp.split("|")
        ]
        leading = bool(lead_marks)
        name = re.sub(r"\^\{[^}]*\}", "", chunk).strip(" .,")
        if leading and authors:
            authors[-1]["marks"].extend(m for m in lead_marks if m)
            authors[-1]["affiliations"] = [
                legend[k] for k in authors[-1]["marks"] if k in legend
            ] or authors[-1]["affiliations"]
        if not name or not re.search(r"[A-Za-z]", name):
            continue
        affs = [legend.get(k, f"?{k}") for k in marks if k.isalnum() and not k.isalpha()]
        authors.append(
            {
                "name": name,
                "marks": [k for k in marks if k.isalpha() or not k.isdigit()],
                "affiliations": affs or ["Anthropic"],
                "role": role,
            }
        )
    return {"authors": authors, "date": iso_date}


def parse_legend(page_html: str) -> dict:
    """Interpret what the byline's symbol footnote actually means.

    The symbol ``*`` is overloaded across Anthropic's own channels: "Core
    contributor", "Core Research Contributor", "Equal contribution, author order
    alphabetical", and on the alignment blog the Anthropic Fellows Program.
    Treating them as one signal would invent seniority that is not there, and an
    alphabetical byline means author position carries no information at all.

    Args:
        page_html: Raw HTML of one article.

    Returns:
        Dict with ``star_means`` (core | equal | fellows | other | None) and
        ``order_meaningful`` (False when the byline is declared alphabetical).
    """
    info = elements_by_class(page_html, "info") + elements_by_class(page_html, "author-note")
    text = " ".join(ln for frag in info for ln in _lines(frag))
    if not text:
        text = " ".join(
            ln
            for frag in elements_by_class(page_html, "section-authors")
            for ln in _lines(frag)
            if ln.lstrip().startswith("^{") or "Fellows" in ln
        )
    if "*" not in text:
        return {"star_means": None, "order_meaningful": True, "legend_text": text[:300]}
    star = None
    if re.search(r"core\b.*contributor", text, re.I):
        star = "core"
    elif re.search(r"equal contribution", text, re.I):
        star = "equal"
    elif re.search(r"fellows program", text, re.I):
        star = "fellows"
    elif "*" in text:
        star = "other"
    return {
        "star_means": star,
        "order_meaningful": not re.search(r"alphabetical", text, re.I),
        "legend_text": text[:300],
    }


def parse_page(page_html: str) -> dict:
    """Parse whichever byline form a page uses.

    Args:
        page_html: Raw HTML of one article.

    Returns:
        Dict with ``authors`` (ordered, deduplicated), ``date``, ``star_means``
        and ``order_meaningful``. ``authors`` is empty when a page carries no
        machine-readable byline, which is itself a result worth recording.
    """
    meta = parse_legend(page_html)

    iso = None
    pub = elements_by_class(page_html, "published")
    if pub:
        for ln in _lines(pub[0]):
            m = DATE_RE.match(ln)
            if m:
                iso = f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"

    affil = elements_by_class(page_html, "affiliations")
    default_aff = "Anthropic"
    legend: dict[str, str] = {}
    for frag in affil:
        lines = [ln for ln in _lines(frag) if ln]
        for ln in lines:
            legend.update(_legend(ln))
        if lines and not lines[-1].lstrip().startswith("^{"):
            default_aff = lines[-1]

    authors: list[dict] = []
    spans = elements_by_class(page_html, "author")
    if spans:
        for span in spans:
            lines = _lines(span)
            if not lines:
                continue
            marks = [m for grp in re.findall(r"\^\{([^}]*)\}", lines[0]) for m in grp.split("|")]
            name = re.sub(r"\^\{[^}]*\}", "", lines[0]).strip(" ,;")
            if name:
                authors.append(
                    {
                        "name": name,
                        "marks": [c for m in marks for c in m],
                        "affiliations": [default_aff],
                        "role": "author",
                    }
                )
    else:
        for frag in elements_by_class(page_html, "section-authors") or elements_by_class(
            page_html, "authors"
        ):
            parsed = parse_generic_byline(frag, legend)
            authors.extend(parsed["authors"])
            iso = iso or parsed["date"]

    return _finalise(authors, iso, meta)


SYMBOLS = "*\u2020\u2021\u00a7\u00b6"


def _finalise(authors: list[dict], iso: str | None, meta: dict) -> dict:
    """Deduplicate by name, keep first-appearance order, annotate positions."""
    for a in authors:
        # Some pages write the marker as plain text rather than <sup>.
        trailing = a["name"][len(a["name"].rstrip(SYMBOLS)):]
        if trailing:
            a["name"] = a["name"].rstrip(SYMBOLS).strip()
            a["marks"].extend(trailing)
    seen: dict[str, dict] = {}
    for a in authors:
        if a["name"] not in seen:
            seen[a["name"]] = a
        else:
            seen[a["name"]]["marks"].extend(a["marks"])
    ordered = list(seen.values())
    for i, a in enumerate(ordered):
        a["position"] = i + 1
        a["n_authors"] = len(ordered)
        a["is_core"] = ("*" in a["marks"]) if meta["star_means"] == "core" else None
        # A fellowship is declared two independent ways: as the meaning of "*"
        # in the footnote, or as the author's numbered affiliation. Reading only
        # the symbol marks a Fellow as staff.
        by_symbol = meta["star_means"] == "fellows" and "*" in a["marks"]
        by_affiliation = any("Fellows" in x for x in a["affiliations"])
        a["is_fellow"] = bool(by_symbol or by_affiliation)
        # A fellowship is not employment, so it must not satisfy is_anthropic.
        a["affiliations"] = list(dict.fromkeys(a["affiliations"]))
        a["marks"] = list(dict.fromkeys(a["marks"]))
        a["is_anthropic"] = any(
            x == "Anthropic" or (x.startswith("Anthropic") and "Fellows" not in x)
            for x in a["affiliations"]
        )
    return {
        "authors": ordered,
        "date": iso,
        "star_means": meta["star_means"],
        "order_meaningful": meta["order_meaningful"],
    }

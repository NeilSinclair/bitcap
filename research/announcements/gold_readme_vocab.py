"""Render the gold README's vocabulary section from config.

The section was hand-pasted and drifted: it still listed `open_weights_release`
after that mechanism was retired, carried the pre-narrowing definition of
`inference_cost_down`, and had never heard of the practice axis. A reviewer
labelling against it would have been labelling against a vocabulary the
pipeline no longer uses.

`tests/test_config.py` asserts the README matches what this renders, so the two
cannot drift again silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config"
README = Path(__file__).parent / "test" / "README.md"
HEADING = "## Vocabulary"


def one_line(text: str) -> str:
    """Collapse a YAML block scalar to a single line.

    Args:
        text: Multi-line description.

    Returns:
        Whitespace-collapsed single line.
    """
    return " ".join(text.split())


def render() -> str:
    """Render the vocabulary section from the config files.

    Returns:
        Markdown for everything from the `## Vocabulary` heading onwards.
    """
    prompt = (ROOT / "prompts" / "announcement_scoring" / "v6.md").read_text()
    events = re.findall(r"^`([a-z_]+)` — ", prompt, re.M)

    mech = yaml.safe_load((CONFIG / "mechanisms.yaml").read_text())["mechanisms"]
    cats = yaml.safe_load((CONFIG / "categories.yaml").read_text())["categories"]
    prac_cfg = yaml.safe_load((CONFIG / "practices.yaml").read_text())
    prac = prac_cfg["practices"]
    cap = prac_cfg.get("max_dimensions")

    out = [HEADING, "", "Generated from `config/` by `gold_readme_vocab.py`. Do not",
           "hand-edit: a test asserts it matches the config the pipeline uses.", "",
           "### Event types (choose exactly one)", ""]
    out += [f"- `{e}`" for e in events]

    out += ["", "### Mechanisms — the investment axis", ""]
    out += [f"- `{m['id']}` — {m['label']}. {one_line(m['description'])}" for m in mech]

    out += ["", "### Categories", ""]
    out += [
        f"- `{c['id']}` — {c['label']}. {one_line(c['definition'])}"
        for c in cats if c.get("lab_signal_routable", True)
    ]

    out += ["", "### Practices — the AI-team axis", "",
            "Tagged independently of the mechanisms. An article can carry no",
            "transmission to an investor and still be the most useful thing an",
            "engineer reads this week, and the reverse is equally valid.", "",
            "Each practice tag takes `action` (`adopt` / `investigate` / `watch`),",
            "`impact`, `confidence`, a `reason` and a verbatim `quote`.", ""]
    for p in prac:
        out.append(f"- `{p['id']}` — {p['label']}. {one_line(p['description'])}")
        for name, meaning in (p.get("dimensions") or {}).items():
            out.append(f"    - `{name}` — {one_line(meaning)}")
    if cap:
        out += ["", f"`model_capability` requires between 1 and {cap} dimensions. "
                    "Naming most of", "the list says only \"this is a big launch\" "
                    "and cannot be ranked."]
    return "\n".join(out) + "\n"


def main() -> None:
    """Rewrite the README's vocabulary section in place."""
    text = README.read_text()
    head = text.split(HEADING)[0]
    README.write_text(head + render())
    print(f"rewrote {README.name} vocabulary section")


if __name__ == "__main__":
    main()

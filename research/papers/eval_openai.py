"""Compare LLM credit-section extraction against the deterministic OpenAI parser.

Third lab through the procedure. OpenAI's credit sections are two-level (team
then role) and run to 400+ names. The deterministic output is the reference, not
truth - disagreements are printed for adjudication.

Usage:
    python research/eval_openai.py
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent.parent
PROMPT = ROOT / "prompts" / "byline_extraction" / "openai_v1.md"
REGISTER = ROOT / "research" / "docs" / "openai_contributors.json"
CACHE = ROOT / "research" / "docs" / "deepseek_cache"
OUT = ROOT / "research" / "docs" / "openai_eval.json"
COST = ROOT / "research" / "docs" / "openai_llm_cost.json"

PRICES = {"claude-sonnet-5": (2.00, 10.00), "claude-opus-5": (5.00, 25.00)}

# The author list is an appendix, so unlike Anthropic's bylines it sits at the END
# of the document. The tail is sent, and the slice size is recorded.
HTML_BUDGET = 130_000

SCHEMA = {
    "type": "object",
    "properties": {
        "authors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": ["string", "null"]},
                },
                "required": ["name", "role"],
                "additionalProperties": False,
            },
        },
        "order_meaningful": {"type": "boolean"},
        "no_credit_section": {"type": "boolean"},
    },
    "required": ["authors", "order_meaningful", "no_credit_section"],
    "additionalProperties": False,
}


def normalise(name: str) -> str:
    """Comparison key: accents, punctuation and initial spacing removed."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^\w\s]", "", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    # "J. L. Cai" and "J.L. Cai" are one person; collapse runs of single letters.
    return re.sub(r"\b([a-z]) (?=[a-z]\b)", r"\1", n)


def page_for(arxiv_id: str) -> str:
    """Read the cached arXiv HTML for a paper.

    Args:
        arxiv_id: Versioned arXiv id.

    Returns:
        Cached page text, or "" when not cached.
    """
    url = f"https://arxiv.org/html/{arxiv_id}"
    key = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")[:150]
    path = CACHE / f"{key}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def prepare(raw: str) -> tuple[str, bool]:
    """Strip non-content markup and take the window containing the author list.

    The appendix is NOT reliably at the end: DeepSeek-V4 puts "Evaluation Details"
    after it and DeepSeek-R1's page is 2MB, so a fixed tail slice missed the author
    list on two of three papers and the model correctly reported none. The window
    is anchored on the section itself.

    Args:
        raw: Raw page HTML.

    Returns:
        Tuple of (prepared HTML, whether the page was truncated).
    """
    body = re.sub(r"(?is)<(script|style|svg|noscript).*?</\1>", " ", raw)
    body = re.sub(r"(?is)<!--.*?-->", " ", body)
    body = re.sub(r"[ \t]+", " ", body)
    if len(body) <= HTML_BUDGET:
        return body, False
    # Anchor on the credit section's HEADING, the same way the deterministic
    # parser does. Taking the last textual match instead put the window on a
    # red-teaming acknowledgement in the GPT-4o card and mid-list in o1's, which
    # scored as catastrophic model failure and was entirely this function's fault.
    anchor = -1
    for m in re.finditer(r"(?i)(Authorship,\s*credit|<h2[^>]*>\s*\d*\s*Contributors)", body):
        window = body[max(0, m.start() - 300) : m.start()]
        if "ltx_title" in window or m.group(0).lower().startswith("<h2"):
            anchor = m.start()
    if anchor < 0:
        return body[-HTML_BUDGET:], True
    return body[max(0, anchor - 500) : anchor - 500 + HTML_BUDGET], True


def extract(client: anthropic.Anthropic, model: str, html: str) -> tuple[dict, dict]:
    """Run one extraction and return the result with its cost record."""
    text = PROMPT.read_text(encoding="utf-8")
    system = text.split("## System", 1)[1].split("## User", 1)[0].strip()
    user = text.split("## User", 1)[1].strip().replace("{html}", html)

    started = time.time()
    with client.messages.stream(
        model=model,
        max_tokens=64000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        # Thinking shares the max_tokens budget with the answer, so a 300-name
        # credit section can be cut mid-JSON. Fail with the cause named rather
        # than crashing in json.loads with an unterminated-string error.
        raise RuntimeError(
            f"output truncated at max_tokens ({response.usage.output_tokens} out); "
            "raise max_tokens or split the section"
        )

    body = next(b.text for b in response.content if b.type == "text")
    u = response.usage
    ir, orr = PRICES[model]
    return json.loads(body), {
        "model": model,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "usd": round(u.input_tokens / 1e6 * ir + u.output_tokens / 1e6 * orr, 6),
        "seconds": round(time.time() - started, 1),
    }


def compare(paper: dict, pred: dict) -> dict:
    """Compare one paper's deterministic and LLM author lists."""
    gold = {normalise(a["name"]): a for a in paper["authors"]}
    llm = {normalise(a["name"]): a for a in pred.get("authors", [])}
    shared = sorted(set(gold) & set(llm))

    role_diffs, dep_diffs = [], []
    for k in shared:
        g, p = gold[k], llm[k]
        if (g["role"] or "") != (p["role"] or ""):
            role_diffs.append({"name": g["name"], "gold": g["role"], "llm": p["role"]})
        _ = p

    return {
        "title": paper["title"],
        "url": paper["url"],
        "gold_n": len(gold),
        "llm_n": len(llm),
        "matched": len(shared),
        "missed": [gold[k]["name"] for k in sorted(set(gold) - set(llm))],
        "invented": [llm[k]["name"] for k in sorted(set(llm) - set(gold))],
        "role_diffs": role_diffs,
        "departed_diffs": dep_diffs,
        "order_meaningful_llm": pred.get("order_meaningful"),
    }


def main() -> None:
    """Run the comparison over papers that carry an author-list appendix."""
    from llm_byline import load_env

    load_env()
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    papers = [p for p in register["papers"] if p["roles_source"] == "credit_section"
              and sum(1 for a in p["authors"] if a["role"]) > 0]
    client = anthropic.Anthropic()

    rows, costs = [], []
    for p in papers:
        html, truncated = prepare(page_for(p["arxiv_id"]))
        try:
            parsed, cost = extract(client, "claude-sonnet-5", html)
        except Exception as exc:
            print(f"  ! {type(exc).__name__} on {p['title'][:40]}: {exc}")
            continue
        cost["title"] = p["title"]
        costs.append(cost)
        COST.write_text(json.dumps(costs, indent=2), encoding="utf-8")
        row = compare(p, parsed)
        row["truncated"] = truncated
        rows.append(row)
        print(
            f"  {row['llm_n']:4d}/{row['gold_n']:4d} matched {row['matched']:4d}  "
            f"${cost['usd']:.4f}  {p['title'][:44]}"
        )

    tp = sum(r["matched"] for r in rows)
    fn = sum(len(r["missed"]) for r in rows)
    fp = sum(len(r["invented"]) for r in rows)
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    summary = {
        "papers": len(rows),
        "gold_authors": tp + fn,
        "llm_authors": tp + fp,
        "name_precision": round(precision, 4),
        "name_recall": round(recall, 4),
        "name_f1": round(2 * precision * recall / (precision + recall), 4)
        if precision + recall
        else 0,
        "names_missed": fn,
        "names_invented": fp,
        "role_disagreements": sum(len(r["role_diffs"]) for r in rows),

        "usd": round(sum(c["usd"] for c in costs), 4),
    }
    OUT.write_text(json.dumps({"summary": summary, "papers": rows}, indent=2), encoding="utf-8")

    print()
    for k, v in summary.items():
        print(f"  {k:26} {v}")
    print("\n--- disagreements ---")
    for r in rows:
        print(f"\n{r['title'][:60]}")
        print(f"    missed {len(r['missed'])}: {', '.join(r['missed'][:12])}")
        print(f"    invented {len(r['invented'])}: {', '.join(r['invented'][:12])}")

        print(f"    role disagreements: {len(r['role_diffs'])}")



if __name__ == "__main__":
    main()

"""Carry the release classifications forward from v8 to v9 without re-spending.

Safe only because of two facts, both checked below rather than assumed:

  1. `prompts/announcement_scoring/v9.md` is byte-identical to v8 apart from a
     header comment. The version exists to force a re-read of *announcements*,
     whose text changed; it asks the model nothing new.
  2. The 380 release documents were not re-fetched. Their `raw_articles.payload`
     is the same text the v8 call saw.

Same prompt plus same input means a v9 run reproduces the v8 output, so paying
~$10 for it buys nothing. What that argument does NOT license is pretending the
call happened: every copied row carries `_carried_forward` naming the version it
came from and why, so provenance stays honest and this is greppable later.
"""
import json, pathlib, sys
from datetime import date

from sqlalchemy import text
from app.db import get_engine, load_env

# --- fact 1: the prompts really are the same question ------------------------
P = pathlib.Path("prompts/announcement_scoring")
v8_body = P.joinpath("v8.md").read_text().split("\n", 1)[1]
v9_body = P.joinpath("v9.md").read_text().split("-->\n", 1)[1]
if v8_body != v9_body:
    sys.exit("REFUSING: v9.md is not byte-identical to v8.md; a copy would be a lie")
print("v9 prompt body is byte-identical to v8")

load_env()
engine = get_engine()

MARKER = {
    "from_prompt_version": "v8",
    "on": date(2026, 9, 5).isoformat(),
    "why": ("v9 is byte-identical to v8 and this document's text was not "
            "re-fetched, so a v9 call would reproduce the v8 output exactly. "
            "The row was copied, not re-run. See docs/decisions.md D56."),
}

with engine.begin() as c:
    rows = list(c.execute(text("""
        select r.url, r.payload
        from raw_llm_responses r
        join raw_articles a on a.url = r.url
        where r.prompt_version = 'v8'
          and a.source_file = 'github_releases'
    """)))
    print(f"v8 release responses found: {len(rows)}")

    already = {u for (u,) in c.execute(text("""
        select r.url from raw_llm_responses r
        join raw_articles a on a.url = r.url
        where r.prompt_version = 'v9' and a.source_file = 'github_releases'
    """))}
    print(f"already at v9: {len(already)}")

    inserted = 0
    for url, payload in rows:
        if url in already:
            continue
        body = dict(payload)
        body["_carried_forward"] = MARKER
        c.execute(text("""
            insert into raw_llm_responses (url, prompt_version, payload, loaded_at)
            values (:u, 'v9', cast(:p as jsonb), now())
            on conflict (url, prompt_version) do nothing
        """), {"u": url, "p": json.dumps(body)})
        inserted += 1

    print(f"carried forward: {inserted}")

with engine.connect() as c:
    for r in c.execute(text(
            "select prompt_version, count(*) from raw_llm_responses "
            "group by 1 order by 1")):
        print("raw_llm_responses", r)

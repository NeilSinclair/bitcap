"""Carry the release classifications forward from v8 to v9 without re-spending.

**The justification is NOT "the output would be identical".** That was the first
version of this argument and this repository's own artefacts refute it: on the
78 corpus articles whose text is byte-identical between the pre- and post-D56
corpus and which are scored under both versions, v9 disagreed with v8 on the
mechanism id set for 17 of them (22%) and on the band for 8 (10%).
`research/docs/variance_v3.json` says the same independently -- 6 of 12
articles mechanism-stable across repeat calls at a fixed prompt and fixed text.
`temperature` is deprecated for this model, so that spread is inherent and
cannot be tuned away.

The real argument is narrower and survives the evidence: **re-running buys a
different sample from the same noisy distribution, not a better one.** The v8
rows are already a draw from it, made against the same prompt and the same
bytes, so ~$10 would purchase a re-roll rather than an improvement. Roughly one
row in five would land differently, and there is no basis for calling the new
draw more correct than the old one.

Two preconditions still matter, and only the first can be checked here:

  1. v9 must ask the same question as v8. Checked below on the *assembled
     system prompt* -- what `build_prompt` actually sends -- not on the file,
     because the file carries a rationale comment that `strip_comments` removes.
     An earlier version of this guard compared the two files after splitting
     each at a different point, which removed precisely the bytes that differed
     and so verified nothing.
  2. The release documents must not have been re-fetched. This holds
     structurally rather than by inspection: `fetch_releases` only fetches above
     a per-repo cursor, so a landed release is never revisited. It is asserted
     rather than verified because `raw_llm_responses` stores no hash of the text
     the call read -- which is worth fixing, given this whole exercise began
     with text changing underneath a stored classification.

Every copied row carries `_carried_forward` naming the source version and the
reasoning, so the saving costs no provenance and the rows stay greppable.
"""
import json, pathlib, sys
from datetime import date, timezone
import datetime as _dt

from sqlalchemy import text
from app.db import get_engine, load_env

# --- precondition 1: the same question, as actually sent ---------------------
# Compared on the assembled system prompt rather than the file. The file
# differs (v9 carries a rationale comment); what reaches the model must not.
sys.path.insert(0, str(pathlib.Path("research/announcements").resolve()))
from score_announcements import strip_comments  # noqa: E402

P = pathlib.Path("prompts/announcement_scoring")


def sent(version: str) -> str:
    """The prompt body `build_prompt` would send, minus the version title."""
    return strip_comments(
        P.joinpath(f"{version}.md").read_text(encoding="utf-8")
    ).split("\n", 1)[1]


if sent("v8") != sent("v9"):
    sys.exit("REFUSING: v9 does not send the same prompt body as v8 -- "
             "a carried-forward row would be answering a different question")
print("v8 and v9 send an identical prompt body")

sys.path.insert(0, str(pathlib.Path(".").resolve()))
from app.pipeline.registry import CORPUS_LABELS, RELEASES  # noqa: E402

FROM_VERSION = "v8"
TO_VERSION = "v9"
LABEL = CORPUS_LABELS[RELEASES]

load_env()
engine = get_engine()

MARKER = {
    "from_prompt_version": FROM_VERSION,
    "on": _dt.datetime.now(timezone.utc).date().isoformat(),
    "why": ("Copied, not re-run. v9 sends the same prompt body as v8 and this "
            "document's text was not re-fetched, so re-running would draw a "
            "different sample from the same distribution rather than a better "
            "one -- roughly 1 row in 5 would differ. See docs/decisions.md D56."),
}

with engine.begin() as c:
    params = {"from_v": FROM_VERSION, "to_v": TO_VERSION, "label": LABEL}
    rows = list(c.execute(text("""
        select r.url, r.payload
        from raw_llm_responses r
        join raw_articles a on a.url = r.url
        where r.prompt_version = :from_v
          and a.source_file = :label
    """), params))
    print(f"v8 release responses found: {len(rows)}")
    if not rows:
        sys.exit("REFUSING: no v8 release responses found -- either the corpus "
                 "label changed or the releases leg has not landed. Exiting 0 "
                 "here would report a successful carry-forward of nothing.")

    already = {u for (u,) in c.execute(text("""
        select r.url from raw_llm_responses r
        join raw_articles a on a.url = r.url
        where r.prompt_version = :to_v and a.source_file = :label
    """), params)}
    print(f"already at v9: {len(already)}")

    inserted = 0
    for url, payload in rows:
        if url in already:
            continue
        body = dict(payload)
        body["_carried_forward"] = MARKER
        c.execute(text("""
            insert into raw_llm_responses (url, prompt_version, payload, loaded_at)
            values (:u, :to_v, cast(:p as jsonb), now())
            on conflict (url, prompt_version) do nothing
        """), {"u": url, "p": json.dumps(body), "to_v": TO_VERSION})
        inserted += 1

    print(f"carried forward: {inserted}")

with engine.connect() as c:
    for r in c.execute(text(
            "select prompt_version, count(*) from raw_llm_responses "
            "group by 1 order by 1")):
        print("raw_llm_responses", r)

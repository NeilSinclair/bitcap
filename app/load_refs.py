"""Mirror config/*.yaml into the reference tables.

YAML remains the source of truth ("config, not code"); the mirror exists so the
join and the future app can query it. The load is wipe-and-reload inside one
transaction, and refuses to run when config/validate.py reports errors — a
mirror of an invalid config would launder the invalidity into clean tables.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import models as m

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config"

sys.path.insert(0, str(CONFIG))


def validate_config() -> None:
    """Run the repo's config validator and refuse the load on any error.

    Raises:
        RuntimeError: If config/validate.py reports errors.
    """
    import validate

    if validate.main() != 0:
        raise RuntimeError("config validation failed; refusing to mirror an invalid config")


def load_refs(session: Session, config_dir: Path = CONFIG, check: bool = True) -> dict:
    """Wipe and reload every reference table from the YAML config.

    Args:
        session: Open session; this function flushes, the caller commits.
        config_dir: Directory holding the YAML files (overridable for tests).
        check: Run config/validate.py first. Tests with synthetic configs
            disable this; the real load never should.

    Returns:
        Row counts per table, for the run record.
    """
    if check:
        validate_config()

    sources = yaml.safe_load((config_dir / "sources.yaml").read_text())
    mechanisms = yaml.safe_load((config_dir / "mechanisms.yaml").read_text())
    categories = yaml.safe_load((config_dir / "categories.yaml").read_text())
    practices = yaml.safe_load((config_dir / "practices.yaml").read_text())
    holdings = yaml.safe_load((config_dir / "holdings.yaml").read_text())
    companies = yaml.safe_load((config_dir / "companies.yaml").read_text())

    # Children before parents; the whole reload is one transaction. The entire
    # clean layer references the refs (tags -> vocab ids, articles -> labs,
    # connections -> holdings) and is derived from raw, so a ref reload wipes
    # it and the caller rebuilds it (cli.cmd_load chains transform + connect).
    # Raw tables are untouched: they are the history.
    for table in (m.Connection, m.ArticleMechanism, m.ArticleCategory,
                  # ArticleGroup and ArticleLink are derived silver like the
                  # rest of this list, and both carry a plain foreign key to
                  # `articles` with no cascade -- so omitting them here does not
                  # leave stale rows, it makes `DELETE FROM articles` raise and
                  # takes `bitcap-db load` down entirely.
                  m.ArticleGroup, m.ArticleLink,
                  m.ArticlePractice, m.Classification, m.Article,
                  m.HoldingCategory, m.HoldingMechanism, m.HoldingLabExposure,
                  m.Holding, m.RefLab, m.RefMechanism, m.RefCategory,
                  m.RefPractice):
        session.execute(delete(table))

    session.add_all(
        m.RefLab(id=lab["id"], label=lab["label"], config_version=sources["version"])
        for lab in sources["labs"]
    )
    session.add_all(
        m.RefMechanism(id=x["id"], label=x["label"], description=x["description"],
                       polarity_note=x["polarity_note"],
                       config_version=mechanisms["version"])
        for x in mechanisms["mechanisms"]
    )
    session.add_all(
        m.RefCategory(id=x["id"], label=x["label"], definition=x["definition"],
                      boundary=x["boundary"],
                      lab_signal_routable=x["lab_signal_routable"],
                      config_version=categories["version"])
        for x in categories["categories"]
    )
    session.add_all(
        m.RefPractice(id=x["id"], label=x["label"], description=x["description"],
                      dimensions=x.get("dimensions") or {},
                      config_version=practices["version"])
        for x in practices["practices"]
    )

    # Parents must reach the database before children: the models declare FKs
    # without ORM relationships, so the unit of work cannot infer the order —
    # Postgres enforces it, sqlite (in tests) does not.
    session.flush()

    by_isin = {c["isin"]: c for c in companies["companies"]}
    tracked = {lab["id"] for lab in sources["labs"]}
    holding_isins = set()
    counts = {"holding_categories": 0, "holding_mechanisms": 0,
              "holding_lab_exposure": 0, "edges_skipped": 0}

    for h in holdings["holdings"]:
        company = by_isin.get(h["isin"], {})
        holding_isins.add(h["isin"])
        # Name and ticker come from companies.yaml, which carries the legal name
        # and the full ticker set; holdings.yaml carries the custodian's mangled
        # statement string and has 7 tickers missing. The custodian string is
        # kept alongside so a row still reconciles to the fund document.
        session.add(m.Holding(
            isin=h["isin"], name=company.get("name") or h["name"],
            custodian_name=h["name"],
            aliases=company.get("aliases") or [],
            ticker=company.get("ticker") or h.get("ticker"),
            ticker_verified=h.get("ticker_verified", False),
            weight_pct=h["weight_pct"],
            ai_role=company.get("ai_role", "none"),
            holdings_version=holdings["version"],
            companies_version=companies["version"],
        ))
    session.flush()

    for h in holdings["holdings"]:
        for cid in h.get("categories", []):
            session.add(m.HoldingCategory(isin=h["isin"], category_id=cid))
            counts["holding_categories"] += 1

    # Edge tables come from companies.yaml. An entry held only by another fund
    # has no holdings row to attach to; skip it audibly rather than silently.
    for c in companies["companies"]:
        if c["isin"] not in holding_isins:
            n = len(c.get("mechanisms", [])) + len(c.get("lab_exposure", []))
            if n:
                counts["edges_skipped"] += n
                print(f"load_refs: {c['name']}: {n} edge(s) skipped — not a "
                      f"Technology Leaders holding", file=sys.stderr)
            continue
        for e in c.get("mechanisms", []):
            session.add(m.HoldingMechanism(
                isin=c["isin"], mechanism_id=e["id"], sign=e["sign"],
                magnitude=e["magnitude"], confidence=e["confidence"],
                why=e["why"], source=e.get("source")))
            counts["holding_mechanisms"] += 1
        for e in c.get("lab_exposure", []):
            session.add(m.HoldingLabExposure(
                isin=c["isin"], lab=e["lab"], kind=e["kind"], sign=e["sign"],
                magnitude=e["magnitude"], confidence=e["confidence"],
                why=e["why"], source=e.get("source"),
                is_dormant=e["lab"] not in tracked))
            counts["holding_lab_exposure"] += 1

    # Flush, don't commit: the caller wraps refs + raw + transform + connect in
    # one transaction so a later failure rolls the wipe above back with it.
    session.flush()
    counts.update({
        "ref_labs": len(sources["labs"]),
        "ref_mechanisms": len(mechanisms["mechanisms"]),
        "ref_categories": len(categories["categories"]),
        "ref_practices": len(practices["practices"]),
        "holdings": len(holdings["holdings"]),
    })
    return counts

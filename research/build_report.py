"""Build the interactive BIT Capital research overview.

Reads the parsed datasets in research/docs and writes a single self-contained
HTML file with the data inlined, so it opens straight from the filesystem.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
OUT = HERE / "bit_capital_overview.html"

TEMPLATE = (HERE / "report_template.html").read_text()


def main() -> None:
    payload = {
        "funds": json.loads((DOCS / "bit_capital_funds.json").read_text()),
        "gtl": json.loads((DOCS / "gtl_portfolio_2025-12-31.json").read_text()),
        "analysis": json.loads((DOCS / "analysis.json").read_text()),
        "firm": json.loads((DOCS / "firm_profile.json").read_text()),
        "compare": json.loads((DOCS / "date_comparison.json").read_text()),
        "views": json.loads((DOCS / "portfolio_views.json").read_text()),
        "history": json.loads((DOCS / "position_history.json").read_text()),
    }
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload))
    OUT.write_text(html)
    size = OUT.stat().st_size / 1024
    print(f"-> {OUT} ({size:.0f} KB)")


if __name__ == "__main__":
    main()

"""What the pipeline ingests, assembled from config rather than hardcoded.

One :class:`Source` per (leg, thing-that-can-fail-independently): a lab for
announcements and papers, a GitHub org for github. That granularity is the
point — `fetch_announcements.collect()` loops over every lab in one call with no
error handling between them, so driving it at leg granularity would let one
Cloudflare-blocked lab take the other six down with it. The orchestrator calls
the per-lab discovery method instead, which the module already exposes.

Stages exist because the legs are not independent (docs/handover.md §7):
Mistral's papers harvester reads the announcements corpus, so it cannot run in
the same stage as the fetch that produces it. Sources inside a stage are
mutually independent and a failure in one says nothing about the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG = Path(__file__).parent.parent.parent / "config"

ANNOUNCEMENTS, PAPERS, GITHUB = "announcements", "papers", "github"
LEGS = (ANNOUNCEMENTS, PAPERS, GITHUB)

# Stage order. Announcements first because Mistral's papers harvester sources
# its candidate titles from the announcements corpus; GitHub is independent of
# both and runs last only because it is the slowest and least urgent.
STAGES = {ANNOUNCEMENTS: 1, PAPERS: 2, GITHUB: 3}


@dataclass(frozen=True)
class Source:
    """One independently-failing ingestion source.

    Attributes:
        leg: Which of :data:`LEGS` this belongs to.
        id: Stable identifier, unique within the leg — a lab id for
            announcements and papers, a GitHub org login for github (a lab can
            own more than one org, so the lab id would not be unique).
        label: Human name, for logs and the ops view.
        stage: Sources in a lower stage run to completion first.
        enabled: False records a source deliberately not covered, which is not
            the same thing as one that is broken.
        config: The leg-specific entry from the YAML, passed through to the
            adapter. Kept as a dict rather than flattened into fields because
            each leg needs genuinely different keys.
    """

    leg: str
    id: str
    label: str
    stage: int
    enabled: bool
    config: dict = field(default_factory=dict, compare=False)

    @property
    def key(self) -> tuple[str, str]:
        """The (leg, id) pair used as the source_state primary key."""
        return (self.leg, self.id)

    def __str__(self) -> str:
        return f"{self.leg}/{self.id}"


def _load(config_dir: Path, name: str) -> dict:
    return yaml.safe_load((config_dir / name).read_text(encoding="utf-8"))


def announcement_sources(config_dir: Path = CONFIG) -> list[Source]:
    """One source per lab in sources.yaml."""
    config = _load(config_dir, "sources.yaml")
    window = config["window_months"]
    return [
        Source(
            leg=ANNOUNCEMENTS,
            id=lab["id"],
            label=lab["label"],
            stage=STAGES[ANNOUNCEMENTS],
            enabled=True,
            config={**lab, "window_months": window},
        )
        for lab in config["labs"]
    ]


def paper_sources(config_dir: Path = CONFIG) -> list[Source]:
    """One source per lab in papers_sources.yaml, including disabled ones.

    Disabled labs are returned rather than filtered out so the register can be
    enumerated honestly — "xAI publishes no findable papers" is a finding, and
    silently omitting it would make it look like an oversight.
    """
    config = _load(config_dir, "papers_sources.yaml")
    return [
        Source(
            leg=PAPERS,
            id=lab["lab"],
            label=lab["lab"],
            stage=STAGES[PAPERS],
            enabled=bool(lab.get("enabled", True)),
            config=lab,
        )
        for lab in config["labs"]
    ]


def github_sources(config_dir: Path = CONFIG) -> list[Source]:
    """One source per GitHub org.

    Keyed on the org login, not the lab: Meta AI owns two orgs
    (`facebookresearch` and `meta-llama`), and they fail independently.
    """
    config = _load(config_dir, "github_sources.yaml")
    return [
        Source(
            leg=GITHUB,
            id=org,
            label=f"{entry['lab']} ({org})",
            stage=STAGES[GITHUB],
            enabled=True,
            config={**entry, "org": org},
        )
        for org, entry in config["orgs"].items()
    ]


LOADERS = {
    ANNOUNCEMENTS: announcement_sources,
    PAPERS: paper_sources,
    GITHUB: github_sources,
}


def load_sources(
    legs: tuple[str, ...] = LEGS, config_dir: Path = CONFIG
) -> list[Source]:
    """Build the registry for the named legs, in stage order.

    Args:
        legs: Which legs to include. A firing that only runs announcements
            passes just that.
        config_dir: Directory holding the three YAML registers.

    Returns:
        Every source, sorted by stage then leg then id, so a run's order is
        deterministic and its log is diffable between firings.

    Raises:
        ValueError: On an unknown leg name, rather than silently ingesting
            nothing.
    """
    unknown = set(legs) - set(LEGS)
    if unknown:
        raise ValueError(f"unknown leg(s): {sorted(unknown)}")
    sources = [s for leg in legs for s in LOADERS[leg](config_dir)]
    return sorted(sources, key=lambda s: (s.stage, s.leg, s.id))

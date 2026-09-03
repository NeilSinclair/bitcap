"""The review agent's dependencies must actually exist.

The agent definition points at files by path: the shared review standard, the
project contract, the config validator. Nothing checks those paths, and the
failure is silent -- an agent told to read a file that has been renamed reads
nothing, then reviews without the standard it was built to apply and returns a
confident, weaker review. That is exactly the degradation the contract asks each
module to guard against, so it is guarded here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
AGENT = ROOT / ".claude" / "agents" / "bitcap-reviewer.md"


def frontmatter(path: Path) -> tuple[dict, str]:
    """Split a `---` delimited YAML frontmatter block off an agent definition.

    Args:
        path: The agent markdown file.

    Returns:
        (fields, body). Parsed with a flat key: value split rather than YAML,
        because the description is a long unquoted line containing colons.
    """
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert m, f"{path.name} has no frontmatter block"
    fields = dict(
        line.split(":", 1) for line in m.group(1).splitlines() if ":" in line
    )
    return {k.strip(): v.strip() for k, v in fields.items()}, m.group(2)


class TestReviewAgent:
    def test_the_definition_exists(self):
        assert AGENT.exists(), "the review agent definition is gone"

    def test_frontmatter_declares_what_the_runtime_needs(self):
        fields, _ = frontmatter(AGENT)
        assert fields["name"] == "bitcap-reviewer", "name must match the filename"
        assert len(fields["description"]) > 60, "description is what routes work here"
        assert "ReportFindings" in fields["tools"], "the agent reports through this tool"

    @pytest.mark.parametrize("path", [
        ".claude/skills/code-reviewer/SKILL.md",  # the severity ladder and tone rules
        ".claude/CLAUDE.md",                      # the contract in §3
        "config/validate.py",                     # the config-not-code check
        "config/scoring.yaml",                    # the scoring-determinism hazard
        "app/scoring.py",
    ])
    def test_every_referenced_path_resolves(self, path):
        """A path the agent is told to read must exist, or the check silently lapses."""
        _, body = frontmatter(AGENT)
        assert path in body, f"{path} is no longer referenced -- update this test too"
        assert (ROOT / path).exists(), f"the agent points at a missing {path}"

    def test_it_cannot_edit_the_code_it_reviews(self):
        """This one is enforced, not merely stated: the tools list omits both.

        A reviewer that patches its own findings stops being an independent
        check, so the guarantee has to come from the grant rather than the prose.
        """
        fields, _ = frontmatter(AGENT)
        granted = {t.strip() for t in fields["tools"].split(",")}
        assert not granted & {"Write", "Edit", "NotebookEdit"}

    def test_the_unenforceable_rule_is_declared_as_unenforceable(self):
        """The DB prohibition is prose — Bash is unrestricted and stays that way.

        A project-level deny on `bitcap-db` would block the README's documented
        workflow for everyone. The definition must therefore say plainly that
        this rule is not enforced, so nobody reads it as a guarantee.
        """
        _, body = frontmatter(AGENT)
        assert "bitcap-db" in body and "never run them" in body
        assert "not** enforced" in body, "the limit must be stated, not implied"

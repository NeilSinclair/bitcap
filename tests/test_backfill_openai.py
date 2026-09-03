"""Tests for the Internet Archive backfill of OpenAI article text.

The silent failures this exists to catch: an archived snapshot that is really a
redirect stub or an error page being written over a good RSS summary, gzip bytes
being stored as mojibake and then quoted by the model, and HTML entities
surviving into the corpus — which already happened once and made verbatim quote
checking report false hallucinations.

Network is never touched here. Every test drives the pure functions.
"""

from __future__ import annotations

import gzip
import io
import json
import sys
import zlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "announcements"))

from backfill_openai import clean, decompress, recover, slug_of  # noqa: E402


class TestSlugOf:
    """URL normalisation, which is the join key against the CDX index."""

    def test_strips_scheme_host_and_trailing_slash(self):
        assert slug_of("https://openai.com/index/gpt-5-6/") == "index/gpt-5-6"

    def test_www_and_http_normalise_to_the_same_key(self):
        assert slug_of("http://www.openai.com/index/a") == slug_of(
            "https://openai.com/index/a"
        )

    def test_case_is_folded(self):
        """CDX returns urlkeys lowercased; a case mismatch would silently miss."""
        assert slug_of("https://openai.com/index/GPT-5-6") == "index/gpt-5-6"


class TestDecompress:
    """Snapshots fetched with `id_` carry the original Content-Encoding."""

    def test_gzip_body_is_decoded(self):
        raw = b"<html>hello</html>"
        packed = io.BytesIO()
        with gzip.GzipFile(fileobj=packed, mode="wb") as fh:
            fh.write(raw)
        assert decompress(packed.getvalue(), "gzip") == raw

    def test_gzip_detected_from_magic_bytes_without_a_header(self):
        raw = b"<html>hello</html>"
        packed = io.BytesIO()
        with gzip.GzipFile(fileobj=packed, mode="wb") as fh:
            fh.write(raw)
        assert decompress(packed.getvalue(), "") == raw

    def test_a_gzip_header_on_plain_bytes_does_not_corrupt_them(self):
        """This happened: the header said gzip and the body was plain HTML.

        Trusting the header raised OSError and lost the page; trusting only the
        magic bytes missed genuinely compressed ones. Both are checked, and
        neither is trusted alone.
        """
        assert decompress(b"<!doctype html>", "gzip") == b"<!doctype html>"

    def test_deflate_is_decoded_in_either_framing(self):
        raw = b"<p>x</p>"
        assert decompress(zlib.compress(raw), "deflate") == raw
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        headerless = compressor.compress(raw) + compressor.flush()
        assert decompress(headerless, "deflate") == raw

    def test_uncompressed_body_passes_through(self):
        assert decompress(b"plain", "") == b"plain"


class TestClean:
    def test_script_and_style_are_removed(self):
        out = clean("<p>keep</p><script>var x=1</script><style>a{color:red}</style>")
        assert "keep" in out and "var x" not in out and "color:red" not in out

    def test_comments_are_removed(self):
        assert "secret" not in clean("<p>keep</p><!-- secret -->")

    def test_entities_are_decoded(self):
        """The corpus was found holding literal `&amp;`, which made a verbatim
        quote check report ten false hallucinations."""
        assert clean("<p>compute &amp; memory</p>") == "compute & memory"

    def test_double_encoded_entities_are_decoded(self):
        assert clean("<p>it&amp;#x27;s</p>") == "it's"

    def test_whitespace_is_collapsed(self):
        assert clean("<p>a</p>\n\n   <p>b</p>") == "a b"


class TestRecover:
    """The guard that stops a bad snapshot overwriting a good summary."""

    @staticmethod
    def _article(text="a summary of some length"):
        return {"url": "https://openai.com/index/x", "text": text}

    def test_a_snapshot_shorter_than_what_we_hold_is_rejected(self, tmp_path, monkeypatch):
        """A redirect stub or error page must never replace the RSS summary."""
        import backfill_openai as bf

        monkeypatch.setattr(bf, "CACHE", tmp_path / "cache")
        monkeypatch.setattr(bf, "get", lambda url, timeout=60: b"<html>404</html>")
        assert bf.recover(self._article("x" * 400), "20260101000000") is None

    def test_a_longer_snapshot_is_accepted_and_cached(self, tmp_path, monkeypatch):
        import backfill_openai as bf

        cache = tmp_path / "cache"
        monkeypatch.setattr(bf, "CACHE", cache)
        body = b"<html><body>" + b"the full article text " * 40 + b"</body></html>"
        calls = []

        def fake_get(url, timeout=60):
            calls.append(url)
            return body

        monkeypatch.setattr(bf, "get", fake_get)
        article = self._article()
        text, snapshot = bf.recover(article, "20260101000000")
        assert "the full article text" in text
        assert snapshot.startswith("http://web.archive.org/web/20260101000000id_/")
        assert len(calls) == 1

        # Second call must be served from disk, not the network.
        again = bf.recover(article, "20260101000000")
        assert again == (text, snapshot)
        assert len(calls) == 1, "cache was not used"

    def test_the_snapshot_url_uses_the_id_modifier(self, tmp_path, monkeypatch):
        """Without `id_` the archive returns its own rewritten page, complete
        with an injected toolbar, which would end up quoted as source text."""
        import backfill_openai as bf

        monkeypatch.setattr(bf, "CACHE", tmp_path / "cache")
        monkeypatch.setattr(bf, "get", lambda url, timeout=60: b"<p>" + b"y" * 500 + b"</p>")
        _, snapshot = bf.recover(self._article(), "20260101000000")
        assert "id_/" in snapshot


class TestSourcesConfig:
    def test_openai_declares_the_wayback_backfill(self):
        cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        openai = next(lab for lab in cfg["labs"] if lab["id"] == "openai")
        assert openai["backfill"] == "wayback"
        assert openai["method"] == "rss", "discovery stays on RSS; only text is backfilled"

    def test_the_prompt_documents_the_archived_text_source(self):
        """A text_source the prompt has never heard of gets no confidence rule."""
        prompt = (ROOT / "prompts" / "announcement_scoring" / "v6.md").read_text()
        assert "full_text_archived" in prompt

    def test_recovered_articles_carry_a_resolvable_snapshot(self):
        """Provenance: an archived score must be traceable to the exact capture."""
        articles = json.loads(
            (ROOT / "research" / "docs" / "announcements.json").read_text()
        )
        archived = [a for a in articles if a.get("text_source") == "full_text_archived"]
        if not archived:
            pytest.skip("backfill has not been run yet")
        for a in archived:
            assert a.get("archive_snapshot", "").startswith("http://web.archive.org/web/")
            assert a["url"] in a["archive_snapshot"]

"""Tests for the fund-PDF provenance manifest.

The failure this guards against is silent: a rolling URL re-fetched into the
same filename parses cleanly and produces a different portfolio. Every test
here asserts the check fails loudly instead.
"""
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))
import verify_docs as vd


class Harness(unittest.TestCase):
    """Builds a throwaway docs tree so tests never touch the real PDFs."""

    def setUp(self):
        self.tmp = Path(__file__).parent / "_tmp_docs"
        shutil.rmtree(self.tmp, ignore_errors=True)
        (self.tmp / "funds").mkdir(parents=True)
        (self.tmp / "funds" / "jb_X.pdf").write_bytes(b"annual edition one")
        (self.tmp / "funds" / "Fact_X.pdf").write_bytes(b"factsheet edition one")
        (self.tmp / "sources.json").write_text(json.dumps({
            "factsheets": {
                "retrieved": "2026-08-24",
                "as_of": "31.07.2026",
                "files": {"Fact_X.pdf": "https://example.test/x_ultimo.pdf"},
            },
            "statutory_reports": {
                "retrieved": "2026-08-25",
                "url_pattern": "https://example.test/api/<ISIN>/{jb|hjb}",
                "files": {"jb_X.pdf": "DE000X annual, 31.12.2025"},
            },
        }))
        self.patches = [
            mock.patch.object(vd, "DOCS", self.tmp),
            mock.patch.object(vd, "FUNDS", self.tmp / "funds"),
            mock.patch.object(vd, "SOURCES", self.tmp / "sources.json"),
            mock.patch.object(vd, "MANIFEST", self.tmp / "manifest.json"),
        ]
        for p in self.patches:
            p.start()
        (self.tmp / "manifest.json").write_text(json.dumps(vd.build()))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestCheck(Harness):
    def test_unchanged_files_pass(self):
        self.assertEqual(vd.check(), ["Fact_X.pdf", "jb_X.pdf"])

    def test_rewritten_file_fails(self):
        """The core case: same URL, same filename, different edition."""
        (self.tmp / "funds" / "jb_X.pdf").write_bytes(b"annual edition two")
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.check()
        self.assertIn("content changed", str(e.exception))
        self.assertIn("jb_X.pdf", str(e.exception))

    def test_single_byte_change_fails(self):
        (self.tmp / "funds" / "jb_X.pdf").write_bytes(b"annual edition onE")
        with self.assertRaises(vd.ProvenanceError):
            vd.check()

    def test_deleted_file_fails(self):
        (self.tmp / "funds" / "jb_X.pdf").unlink()
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.check()
        self.assertIn("missing", str(e.exception))

    def test_undeclared_pdf_fails_a_full_check(self):
        (self.tmp / "funds" / "mystery.pdf").write_bytes(b"where did this come from")
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.check()
        self.assertIn("no provenance", str(e.exception))

    def test_undeclared_pdf_does_not_fail_a_targeted_check(self):
        """A parser asks about its own files, not the whole directory."""
        (self.tmp / "funds" / "mystery.pdf").write_bytes(b"unrelated")
        self.assertEqual(vd.check(["jb_X.pdf"]), ["jb_X.pdf"])

    def test_targeted_check_of_an_unpinned_name_fails(self):
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.check(["not_pinned.pdf"])
        self.assertIn("not pinned", str(e.exception))

    def test_missing_manifest_fails(self):
        (self.tmp / "manifest.json").unlink()
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.check()
        self.assertIn("--rebuild", str(e.exception))

    def test_all_failures_reported_together(self):
        (self.tmp / "funds" / "jb_X.pdf").write_bytes(b"changed")
        (self.tmp / "funds" / "Fact_X.pdf").unlink()
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.check()
        self.assertIn("jb_X.pdf", str(e.exception))
        self.assertIn("Fact_X.pdf", str(e.exception))


class TestBuild(Harness):
    def test_records_hash_size_and_provenance(self):
        entry = vd.build()["files"]["jb_X.pdf"]
        self.assertEqual(entry["bytes"], len(b"annual edition one"))
        self.assertEqual(len(entry["sha256"]), 64)
        self.assertEqual(entry["url_stability"], "rolling")
        self.assertEqual(entry["retrieved"], "2026-08-25")

    def test_as_of_comes_from_the_description_not_the_filename(self):
        files = vd.build()["files"]
        self.assertEqual(files["jb_X.pdf"]["as_of"], "2025-12-31")
        self.assertEqual(files["Fact_X.pdf"]["as_of"], "2026-07-31")

    def test_declared_but_absent_file_fails(self):
        (self.tmp / "funds" / "Fact_X.pdf").unlink()
        with self.assertRaises(vd.ProvenanceError) as e:
            vd.build()
        self.assertIn("not in", str(e.exception))

    def test_rebuild_after_a_deliberate_refresh_repins(self):
        (self.tmp / "funds" / "jb_X.pdf").write_bytes(b"annual edition two")
        (self.tmp / "manifest.json").write_text(json.dumps(vd.build()))
        self.assertEqual(vd.check(), ["Fact_X.pdf", "jb_X.pdf"])


class TestIso(unittest.TestCase):
    def test_parses_german_dates(self):
        self.assertEqual(vd.iso("DE000X annual, 31.12.2025"), "2025-12-31")

    def test_returns_none_without_a_date(self):
        self.assertIsNone(vd.iso("no date here"))


class TestRealManifest(unittest.TestCase):
    """The committed manifest must match the committed PDFs."""

    def test_repo_pdfs_match_their_pinned_editions(self):
        self.assertEqual(len(vd.check()), 19)

    def test_manifest_matches_a_fresh_build(self):
        on_disk = json.loads(vd.MANIFEST.read_text())["files"]
        self.assertEqual(on_disk, vd.build()["files"])


if __name__ == "__main__":
    unittest.main()

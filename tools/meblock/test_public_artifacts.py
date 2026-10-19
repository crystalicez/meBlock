"""Guard that keeps private diagnostics out of the public meBlock Git index."""

import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest

from check_public_artifacts import blocked_content, blocked_path, main, verify_tracked_files


class PublicArtifactGuardTests(unittest.TestCase):
    def test_denies_private_handoff_and_raw_reports(self):
        bad = (
            "HANDOFF-NEXT-SESSION.md",
            "memory-reports/session-1.json",
            "private/notes.txt",
            "profiles/test/profile.ini",
            ".audit/working.tsv",
            "docs/memory-report.json.gz",
            "results/archive.heapsnapshot",
            "tmp/memory.json.gz",
            "settings.sqlite",
            "dist/build/private-memory/synthetic-meblock.xpi",
            "dist/build/meblock-comparison/session/report.json",
            "sample.cpuprofile",
        )
        for path in bad:
            with self.subTest(path=path):
                self.assertIsNotNone(blocked_path(path))

    def test_permits_public_source_and_docs(self):
        good = (
            ".gitignore",
            ".github/workflows/meblock-regression.yml",
            "docs/meblock-memory.md",
            "tools/meblock/analyze_memory.py",
            "src/js/background.js",
        )
        for path in good:
            with self.subTest(path=path):
                self.assertIsNone(blocked_path(path))

    def test_rejects_token_and_private_key_without_echoing_value(self):
        self.assertEqual(
            blocked_content("docs/meblock-memory.md", b"key=" + b"ghp_" + b"X" * 32),
            "service-token",
        )
        self.assertEqual(
            blocked_content(
                "tools/meblock/example.py",
                b"-----BEGIN " + b"PRIVATE KEY-----" + b"\n" + b"test",
            ),
            "private-key",
        )

    def test_identifying_home_path_and_email_are_rejected(self):
        fake_home = (
            b"C:" + bytes([92]) + b"Users" + bytes([92])
            + b"SampleName" + bytes([92]) + b"prefs.js"
        )
        self.assertEqual(
            blocked_content("docs/meblock-memory.md", fake_home),
            "private-home-directory",
        )
        private_email = b"person" + b"@" + b"private.invalid"
        self.assertEqual(
            blocked_content("docs/meblock-memory.md", private_email),
            "email-address",
        )
        self.assertIsNone(
            blocked_content("docs/meblock-memory.md", b"meBlock@example.invalid")
        )

    def test_git_index_guard_rejects_force_added_private_artifacts_without_printing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            safe = root / "docs" / "meblock-memory.md"
            safe.parent.mkdir()
            safe.write_text("Safe documentation", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "--", "docs/meblock-memory.md"],
                           check=True, capture_output=True)
            self.assertEqual(verify_tracked_files(str(root)), [])
            raw = root / "dist" / "build" / "private-memory" / "report.json.gz"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"private sample")
            handoff = root / "HANDOFF-NEXT-SESSION.md"
            handoff.write_text("local-only", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "--", "dist/build/private-memory/report.json.gz",
                            "HANDOFF-NEXT-SESSION.md"], check=True, capture_output=True)
            problems = verify_tracked_files(str(root))
            self.assertEqual(len(problems), 2)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--repository", str(root)]), 1)
            self.assertIn("DENIED: 2", output.getvalue())
            self.assertNotIn("report.json.gz", output.getvalue())
            self.assertNotIn("HANDOFF-NEXT-SESSION", output.getvalue())

    def test_scoped_content_checker_does_not_scan_upstream(self):
        self.assertIsNone(
            blocked_content("src/js/background.js", b"ghp_" + b"X" * 32)
        )


if __name__ == "__main__":
    unittest.main()

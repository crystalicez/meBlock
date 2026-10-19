"""Guard that keeps private diagnostics out of the public meBlock Git index."""

import unittest

from check_public_artifacts import blocked_content, blocked_path


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

    def test_scoped_content_checker_does_not_scan_upstream(self):
        self.assertIsNone(
            blocked_content("src/js/background.js", b"ghp_" + b"X" * 32)
        )


if __name__ == "__main__":
    unittest.main()

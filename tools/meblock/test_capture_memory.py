"""Offline tests for disposable XPI memory workloads; no Firefox profile access."""

from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from capture_memory import BUILT_IN_LIST, extension_process_explicit_mib, synthetic_xpi


class CaptureMemoryTests(unittest.TestCase):
    def test_disposable_package_preserves_source_and_injects_only_fixture_rules(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "original.xpi"
            disposable = Path(root) / "disposable.xpi"
            with ZipFile(original, "w") as archive:
                archive.writestr(BUILT_IN_LIST, "||fixed.example.invalid^\n")
                archive.writestr("manifest.json", '{"version":"1"}')
            synthetic_xpi(original, disposable, 3)
            with ZipFile(original) as archive:
                self.assertEqual(archive.read(BUILT_IN_LIST), b"||fixed.example.invalid^\n")
            with ZipFile(disposable) as archive:
                self.assertEqual(
                    archive.read(BUILT_IN_LIST),
                    b"||fixed.example.invalid^\n\n"
                    b"||mb-memory-0000000.example.invalid^\n"
                    b"||mb-memory-0000001.example.invalid^\n"
                    b"||mb-memory-0000002.example.invalid^\n",
                )
                self.assertEqual(archive.read("manifest.json"), b'{"version":"1"}')

    def test_missing_bundled_list_fails(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "original.xpi"
            with ZipFile(original, "w") as archive:
                archive.writestr("manifest.json", "{}")
            with self.assertRaisesRegex(ValueError, "bundled"):
                synthetic_xpi(original, Path(root) / "copy.xpi", 1)

    def test_extension_process_attribution_is_explicitly_not_addon_only(self):
        data = [
            {"process": "Extensions (pid 123)", "path": "explicit", "units": 0, "amount": 20 * 1024 * 1024},
            {"process": "Extensions (pid 123)", "path": "explicit/js", "units": 0, "amount": 12 * 1024 * 1024},
            {"process": "Extensions (pid 123)", "path": "explicit/native", "units": 0, "amount": 8 * 1024 * 1024},
            {"process": "Main Process", "path": "explicit", "units": 0, "amount": 900 * 1024 * 1024},
        ]
        self.assertEqual(extension_process_explicit_mib(data), 20.0)
        self.assertIsNone(extension_process_explicit_mib([]))

    def test_negative_synthetic_rule_count_fails(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            synthetic_xpi(Path("missing"), Path("unused"), 0)


if __name__ == "__main__":
    unittest.main()

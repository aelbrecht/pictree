import os
import tempfile
import unittest
from pathlib import Path

import dedupe


class DeduplicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.source_dir = Path(self.temp_dir.name)

    def create_candidate(self, name: str, contents: bytes) -> Path:
        candidate = self.source_dir / name
        candidate.write_bytes(contents)
        os.utime(candidate, (1000, 1000))
        return candidate

    def test_keeps_different_files_with_matching_metadata(self) -> None:
        first = self.create_candidate('20240101-000000-a.jpg', b'AAAA')
        second = self.create_candidate('20240101-000000-b.jpg', b'BBBB')

        result = dedupe.deduplicate(str(self.source_dir), verbosity=0)

        self.assertEqual(result, (2, 0))
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_deletes_only_byte_identical_duplicates(self) -> None:
        original = self.create_candidate('20240101-000000-a.jpg', b'AAAA')
        duplicate = self.create_candidate('20240101-000000-long-name.jpg', b'AAAA')

        result = dedupe.deduplicate(str(self.source_dir), verbosity=0)

        self.assertEqual(result[1], 1)
        self.assertTrue(original.exists())
        self.assertFalse(duplicate.exists())

    def test_dry_run_does_not_delete_duplicates(self) -> None:
        original = self.create_candidate('20240101-000000-a.jpg', b'AAAA')
        duplicate = self.create_candidate('20240101-000000-long-name.jpg', b'AAAA')

        result = dedupe.deduplicate(str(self.source_dir), dry_run=True, verbosity=0)

        self.assertEqual(result[1], 1)
        self.assertTrue(original.exists())
        self.assertTrue(duplicate.exists())

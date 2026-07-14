import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from util import hashes


class HashTests(unittest.TestCase):
    def test_calculates_sha1(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir, 'photo.jpg')
            file_path.write_bytes(b'abc')

            result = hashes.calculate_file_hash(str(file_path))

        self.assertEqual(result, 'a9993e364706816aba3e25717850c26c9cd0d89d')

    def test_copies_and_hashes_in_one_source_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')

            digest = hashes.copy_file_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'image contents')
            self.assertEqual(digest, '1082f00eac56e0d0c5ea09091bc936dc2714f904')

    def test_sidecar_records_hash_and_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image')
            destination.write_bytes(b'image')
            os.utime(source, (1000, 1000))
            digest = hashes.calculate_file_hash(str(source))

            hashes.write_hash_sidecar(str(destination), digest, source_path=str(source))
            record = hashes.read_hash_sidecar_record(str(destination))

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.digest, digest)
            self.assertTrue(record.matches_file(destination.stat()))
            self.assertTrue(record.matches_source(str(source), source.stat()))

    def test_reuses_existing_sidecar_without_reading_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir, 'photo.jpg')
            file_path.write_bytes(b'image')
            hashes.write_hash_sidecar(str(file_path), 'a' * 40, source_path=str(file_path))

            with patch('util.hashes.calculate_file_hash') as calculate_hash:
                result = hashes.get_or_create_file_hash(str(file_path))

        self.assertEqual(result, 'a' * 40)
        calculate_hash.assert_not_called()

    def test_recalculates_a_stale_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir, 'photo.jpg')
            file_path.write_bytes(b'old')
            hashes.write_hash_sidecar(str(file_path), 'a' * 40, source_path=str(file_path))
            file_path.write_bytes(b'new contents')

            with patch('util.hashes.calculate_file_hash', return_value='b' * 40) as calculate_hash:
                result = hashes.get_or_create_file_hash(str(file_path))

        self.assertEqual(result, 'b' * 40)
        calculate_hash.assert_called_once_with(str(file_path))


if __name__ == '__main__':
    unittest.main()

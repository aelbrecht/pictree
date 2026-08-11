import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
            os.utime(source, (1000, 1000))

            result = hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'image contents')
            self.assertEqual(result.digest, '1082f00eac56e0d0c5ea09091bc936dc2714f904')
            self.assertEqual(destination.stat().st_mtime_ns, source.stat().st_mtime_ns)
            self.assertEqual(result.source_stat.st_size, source.stat().st_size)
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])
            self.assertTrue(Path(str(destination) + hashes.HASH_SUFFIX).is_file())

    def test_atomic_copy_cleans_up_after_keyboard_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            digest = MagicMock()
            digest.update.side_effect = KeyboardInterrupt

            with (
                patch('util.hashes.new_hasher', return_value=digest),
                self.assertRaises(KeyboardInterrupt),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_cleans_up_when_metadata_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')

            with (
                patch('util.hashes.copystat', side_effect=OSError('network error')),
                self.assertRaises(OSError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_cleans_up_when_rename_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            real_replace = hashes.os.replace

            def fail_media_rename(source_path: str, destination_path: str) -> None:
                if destination_path == str(destination):
                    raise OSError('network error')
                real_replace(source_path, destination_path)

            with (
                patch('util.hashes.os.replace', side_effect=fail_media_rename),
                self.assertRaises(OSError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])
            self.assertTrue(Path(str(destination) + hashes.HASH_SUFFIX).is_file())

            hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'image contents')
            record = hashes.read_hash_sidecar_record(str(destination))
            self.assertIsNotNone(record)
            assert record is not None
            self.assertTrue(record.matches_file(destination.stat()))

    def test_atomic_copy_never_overwrites_nonempty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'new contents')
            destination.write_bytes(b'existing archive contents')

            with self.assertRaises(hashes.DestinationChangedError):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'existing archive contents')
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_replaces_legacy_zero_byte_destination_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            destination.touch()

            hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'image contents')

    def test_atomic_copy_leaves_zero_byte_destination_unchanged_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            destination.touch()

            with (
                patch('util.hashes.copystat', side_effect=OSError('network error')),
                self.assertRaises(OSError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_bytes(), b'')
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_rejects_source_changes_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            real_copystat = hashes.copystat

            def mutate_source(source_path: str, destination_path: str) -> None:
                real_copystat(source_path, destination_path)
                Path(source_path).write_bytes(b'changed while copying')

            with (
                patch('util.hashes.copystat', side_effect=mutate_source),
                self.assertRaises(hashes.FileChangedError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_rejects_source_changes_during_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            digest = MagicMock()

            def mutate_source(_chunk: bytes) -> None:
                source.write_bytes(b'changed while the source was open')

            digest.update.side_effect = mutate_source
            with (
                patch('util.hashes.new_hasher', return_value=digest),
                self.assertRaises(hashes.FileChangedError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_refuses_destination_created_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            real_copystat = hashes.copystat

            def create_destination(source_path: str, temp_path: str) -> None:
                real_copystat(source_path, temp_path)
                destination.write_bytes(b'created by another process')

            with (
                patch('util.hashes.copystat', side_effect=create_destination),
                self.assertRaises(hashes.DestinationChangedError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'created by another process')
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_rejects_post_rename_size_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            source.write_bytes(b'image contents')
            real_replace = hashes.os.replace

            def replace_then_truncate(source_path: str, destination_path: str) -> None:
                real_replace(source_path, destination_path)
                if destination_path == str(destination):
                    destination.write_bytes(b'x')

            with (
                patch('util.hashes.os.replace', side_effect=replace_then_truncate),
                self.assertRaises(hashes.DestinationChangedError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'x')
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])

    def test_atomic_copy_rejects_post_rename_same_size_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            replacement = Path(temp_dir, 'replacement.jpg')
            source.write_bytes(b'AAAA')
            real_replace = hashes.os.replace

            def replace_then_swap_file(source_path: str, destination_path: str) -> None:
                real_replace(source_path, destination_path)
                if destination_path == str(destination):
                    replacement.write_bytes(b'BBBB')
                    hashes.copystat(str(source), str(replacement))
                    real_replace(replacement, destination_path)

            with (
                patch('util.hashes.os.replace', side_effect=replace_then_swap_file),
                self.assertRaises(hashes.DestinationChangedError),
            ):
                hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'BBBB')
            self.assertEqual(list(Path(temp_dir).glob('.pictree-copy-*.tmp')), [])
            record = hashes.read_hash_sidecar_record(str(destination))
            self.assertIsNotNone(record)
            assert record is not None
            self.assertTrue(record.prepared)
            self.assertFalse(record.matches_file(destination.stat()))

    def test_atomic_copy_verifies_content_when_rename_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, 'source.jpg')
            destination = Path(temp_dir, 'destination.jpg')
            replacement = Path(temp_dir, 'replacement.jpg')
            source.write_bytes(b'AAAA')
            real_replace = hashes.os.replace

            def replace_then_swap_file(source_path: str, destination_path: str) -> None:
                real_replace(source_path, destination_path)
                if destination_path == str(destination):
                    replacement.write_bytes(b'AAAA')
                    real_replace(replacement, destination_path)

            with (
                patch('util.hashes.os.replace', side_effect=replace_then_swap_file),
                patch(
                    'util.hashes.calculate_file_hash',
                    wraps=hashes.calculate_file_hash,
                ) as calculate_hash,
            ):
                result = hashes.copy_file_atomically_with_hash(str(source), str(destination))

            self.assertEqual(destination.read_bytes(), b'AAAA')
            calculate_hash.assert_called_once()
            self.assertEqual(calculate_hash.call_args.args, (str(destination),))
            record = hashes.read_hash_sidecar_record(str(destination))
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.digest, result.digest)
            self.assertTrue(record.matches_file(destination.stat()))

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

    def test_sidecar_read_propagates_io_errors(self) -> None:
        with (
            patch('builtins.open', side_effect=PermissionError('sidecar unavailable')),
            self.assertRaises(PermissionError),
        ):
            hashes.read_hash_sidecar_record('/archive/photo.jpg')

    def test_recalculates_a_stale_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir, 'photo.jpg')
            file_path.write_bytes(b'old')
            hashes.write_hash_sidecar(str(file_path), 'a' * 40, source_path=str(file_path))
            file_path.write_bytes(b'new contents')

            with patch('util.hashes.calculate_file_hash', return_value='b' * 40) as calculate_hash:
                result = hashes.get_or_create_file_hash(str(file_path))

        self.assertEqual(result, 'b' * 40)
        calculate_hash.assert_called_once()
        self.assertEqual(calculate_hash.call_args.args, (str(file_path),))
        self.assertEqual(
            calculate_hash.call_args.kwargs['expected_stat'].st_size,
            len(b'new contents'),
        )


if __name__ == '__main__':
    unittest.main()

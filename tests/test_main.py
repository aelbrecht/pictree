import contextlib
import io
import os
import tempfile
import unittest
from builtins import open as builtin_open
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import main
from util import hashes
from util.locking import DestinationLockedError, destination_lock


class SyncMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.src_dir = Path(self.temp_dir.name, 'source')
        self.dst_dir = Path(self.temp_dir.name, 'destination')
        self.src_dir.mkdir()
        self.dst_dir.mkdir()

    def test_copies_media_to_exif_date_directory_and_records_source(self) -> None:
        source = self.src_dir / 'holiday.jpg'
        source.write_bytes(b'image contents')
        modified_timestamp = datetime(2025, 1, 2, 12, 30).timestamp()
        os.utime(source, (modified_timestamp, modified_timestamp))

        with patch('main.get_exif_modify_time', return_value=datetime(2024, 7, 8, 9, 10, 11)):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '7' / '8' / '20240708-091011-holiday.jpg'
        self.assertEqual(result, (1, 0))
        self.assertEqual(destination.read_bytes(), b'image contents')
        self.assertEqual(destination.stat().st_mtime, source.stat().st_mtime)
        self.assertTrue(Path(str(destination) + '.sha1').is_file())
        self.assertFalse(Path(str(source) + '.sha1').exists())
        self.assertEqual(
            (destination.parent / '.filesource').read_text(encoding='utf-8'),
            f'{source},{destination}\n',
        )

    def test_uses_file_modification_time_when_exif_date_is_missing(self) -> None:
        source = self.src_dir / 'clip.mp4'
        source.write_bytes(b'video contents')
        modified = datetime(2023, 3, 4, 5, 6, 7)
        os.utime(source, (modified.timestamp(), modified.timestamp()))

        with patch('main.get_exif_modify_time', return_value=None):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2023' / '3' / '4' / '20230304-050607-clip.mp4'
        self.assertEqual(result, (1, 0))
        self.assertTrue(destination.is_file())

    def test_skips_an_identical_file_on_a_second_sync(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'same image')
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            first_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)
            with (
                patch('main.calculate_file_hash') as calculate_hash,
                patch('main.copy_file_atomically_with_hash') as copy_with_hash,
            ):
                second_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(first_result, (1, 0))
        self.assertEqual(second_result, (0, 1))
        calculate_hash.assert_not_called()
        copy_with_hash.assert_not_called()
        self.assertEqual(len(list((self.dst_dir / '2024' / '2' / '3').glob('*.jpg'))), 1)

    def test_seeds_missing_sidecar_from_source_without_reading_destination(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image')
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        sidecar = Path(str(destination) + '.sha1')
        sidecar.unlink()

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', return_value='a' * 40) as calculate_hash,
        ):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(result, (0, 1))
        calculate_hash.assert_called_once()
        self.assertEqual(calculate_hash.call_args.args, (str(source),))
        self.assertEqual(
            calculate_hash.call_args.kwargs['expected_stat'].st_size,
            source.stat().st_size,
        )
        self.assertTrue(sidecar.read_text(encoding='utf-8').startswith('a' * 40))

    def test_valid_sidecar_tolerates_rounded_destination_timestamp(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image')
        os.utime(source, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        rounded_mtime_ns = source.stat().st_mtime_ns // 1_000_000_000 * 1_000_000_000
        os.utime(destination, ns=(rounded_mtime_ns, rounded_mtime_ns))
        hashes.write_hash_sidecar(
            str(destination),
            hashes.calculate_file_hash(str(source)),
            source_path=str(source),
            source_stat=source.stat(),
        )

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash') as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(result, (0, 1))
        calculate_hash.assert_not_called()
        copy_file.assert_not_called()
        self.assertEqual(len(list(destination.parent.glob('*.jpg'))), 1)

    def test_verifies_legacy_smb_timestamp_mismatch_once_without_copying(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        destination = destination_dir / '20240203-040506-photo.jpg'
        destination.write_bytes(b'image')
        os.utime(destination, (2000, 2000))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            first_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(first_result, (0, 1))
        self.assertEqual(
            [call.args[0] for call in calculate_hash.call_args_list],
            [str(source), str(destination)],
        )
        copy_file.assert_not_called()
        record = hashes.read_hash_sidecar_record(str(destination))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.matches_file(destination.stat()))
        self.assertTrue(record.matches_source(str(source), source.stat()))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash') as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            second_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(second_result, (0, 1))
        calculate_hash.assert_not_called()
        copy_file.assert_not_called()

    def test_legacy_smb_timestamp_mismatch_with_different_content_versions(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'AAAA')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        destination = destination_dir / '20240203-040506-photo.jpg'
        destination.write_bytes(b'BBBB')
        os.utime(destination, (2000, 2000))

        with patch('main.get_exif_modify_time', return_value=captured_at):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        archived_files = list(destination_dir.glob('*.jpg'))
        self.assertEqual(result, (1, 0))
        self.assertEqual(destination.read_bytes(), b'BBBB')
        self.assertEqual({path.read_bytes() for path in archived_files}, {b'AAAA', b'BBBB'})
        destination_record = hashes.read_hash_sidecar_record(str(destination))
        self.assertIsNotNone(destination_record)
        assert destination_record is not None
        self.assertEqual(
            destination_record.digest,
            hashes.calculate_file_hash(str(destination)),
        )
        self.assertIsNone(destination_record.source_path)

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            retry_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(retry_result, (0, 1))
        self.assertEqual(
            [call.args[0] for call in calculate_hash.call_args_list],
            [str(source)],
        )
        copy_file.assert_not_called()

    def test_stale_saved_size_never_falls_back_to_legacy_metadata_trust(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'OLD')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        destination = destination_dir / '20240203-040506-photo.jpg'
        destination.write_bytes(b'OLD')
        os.utime(destination, (1000, 1000))
        hashes.write_hash_sidecar(
            str(destination),
            hashes.calculate_file_hash(str(source)),
            source_path=str(source),
            source_stat=source.stat(),
        )

        source.write_bytes(b'NEWS')
        destination.write_bytes(b'EVIL')
        os.utime(source, (2000, 2000))
        os.utime(destination, (2000, 2000))

        with patch('main.get_exif_modify_time', return_value=captured_at):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        archived_files = list(destination_dir.glob('*.jpg'))
        self.assertEqual(result, (1, 0))
        self.assertEqual(destination.read_bytes(), b'EVIL')
        self.assertEqual({path.read_bytes() for path in archived_files}, {b'NEWS', b'EVIL'})
        destination_record = hashes.read_hash_sidecar_record(str(destination))
        self.assertIsNotNone(destination_record)
        assert destination_record is not None
        self.assertEqual(
            destination_record.digest,
            hashes.calculate_file_hash(str(destination)),
        )
        self.assertIsNone(destination_record.source_path)

    def test_invalid_sidecar_never_falls_back_to_legacy_metadata_trust(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'AAAA')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        destination = destination_dir / '20240203-040506-photo.jpg'
        destination.write_bytes(b'BBBB')
        os.utime(destination, (1000, 1000))
        Path(str(destination) + hashes.HASH_SUFFIX).write_text(
            'invalid receipt\n',
            encoding='utf-8',
        )

        with patch('main.get_exif_modify_time', return_value=captured_at):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        archived_files = list(destination_dir.glob('*.jpg'))
        self.assertEqual(result, (1, 0))
        self.assertEqual(destination.read_bytes(), b'BBBB')
        self.assertEqual({path.read_bytes() for path in archived_files}, {b'AAAA', b'BBBB'})
        destination_record = hashes.read_hash_sidecar_record(str(destination))
        self.assertIsNotNone(destination_record)
        assert destination_record is not None
        self.assertEqual(
            destination_record.digest,
            hashes.calculate_file_hash(str(destination)),
        )
        self.assertIsNone(destination_record.source_path)

    def test_matching_metadata_prepared_receipt_is_never_trusted(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'AAAA')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        destination = destination_dir / '20240203-040506-photo.jpg'
        destination.write_bytes(b'BBBB')
        os.utime(destination, (1000, 1000))
        hashes._write_hash_sidecar_for_stat(
            str(destination),
            str(destination),
            hashes.calculate_file_hash(str(source)),
            destination.stat(),
            source_path=str(source),
            source_stat=source.stat(),
            prepared=True,
        )

        with patch('main.get_exif_modify_time', return_value=captured_at):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        archived_files = list(destination_dir.glob('*.jpg'))
        self.assertEqual(result, (1, 0))
        self.assertEqual(destination.read_bytes(), b'BBBB')
        self.assertEqual({path.read_bytes() for path in archived_files}, {b'AAAA', b'BBBB'})
        destination_record = hashes.read_hash_sidecar_record(str(destination))
        self.assertIsNotNone(destination_record)
        assert destination_record is not None
        self.assertFalse(destination_record.prepared)
        self.assertEqual(
            destination_record.digest,
            hashes.calculate_file_hash(str(destination)),
        )

    def test_legacy_canonical_match_does_not_touch_stale_version(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        canonical = destination_dir / '20240203-040506-photo.jpg'
        canonical.write_bytes(b'image')
        os.utime(canonical, (2000, 2000))
        version = destination_dir / '20240203-040506-photo-v1234.jpg'
        version.write_bytes(b'image')
        hashes.write_hash_sidecar(
            str(version),
            hashes.calculate_file_hash(str(source)),
            source_path=str(source),
            source_stat=source.stat(),
        )
        os.utime(version, (3000, 3000))
        version_sidecar = Path(str(version) + hashes.HASH_SUFFIX)
        version_before = (
            version.read_bytes(),
            version.stat().st_mtime_ns,
            version_sidecar.read_bytes(),
            version_sidecar.stat().st_mtime_ns,
        )

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(result, (0, 1))
        self.assertEqual(
            [call.args[0] for call in calculate_hash.call_args_list],
            [str(source), str(canonical)],
        )
        copy_file.assert_not_called()
        canonical_record = hashes.read_hash_sidecar_record(str(canonical))
        self.assertIsNotNone(canonical_record)
        version_after = (
            version.read_bytes(),
            version.stat().st_mtime_ns,
            version_sidecar.read_bytes(),
            version_sidecar.stat().st_mtime_ns,
        )
        self.assertEqual(version_after, version_before)

    def test_matching_legacy_version_avoids_copy_when_canonical_differs(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'AAAA')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination_dir = self.dst_dir / '2024' / '2' / '3'
        destination_dir.mkdir(parents=True)
        canonical = destination_dir / '20240203-040506-photo.jpg'
        canonical.write_bytes(b'BBBB')
        os.utime(canonical, (2000, 2000))
        version = destination_dir / '20240203-040506-photo-v1234.jpg'
        version.write_bytes(b'AAAA')
        os.utime(version, (3000, 3000))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            first_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(first_result, (0, 1))
        self.assertEqual(
            [call.args[0] for call in calculate_hash.call_args_list],
            [str(source), str(canonical), str(version)],
        )
        copy_file.assert_not_called()
        canonical_record = hashes.read_hash_sidecar_record(str(canonical))
        version_record = hashes.read_hash_sidecar_record(str(version))
        self.assertIsNotNone(canonical_record)
        self.assertIsNotNone(version_record)
        assert canonical_record is not None
        assert version_record is not None
        self.assertIsNone(canonical_record.source_path)
        self.assertTrue(version_record.matches_source(str(source), source.stat()))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            second_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(second_result, (0, 1))
        self.assertEqual(
            [call.args[0] for call in calculate_hash.call_args_list],
            [str(source)],
        )
        copy_file.assert_not_called()

    def test_matching_hash_refreshes_sidecar_after_source_path_changes(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image')
        os.utime(source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        moved_source_dir = Path(self.temp_dir.name, 'moved-source')
        moved_source_dir.mkdir()
        moved_source = moved_source_dir / 'photo.jpg'
        moved_source.write_bytes(b'image')
        os.utime(moved_source, (2000, 2000))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
        ):
            result = main.sync_media(str(moved_source_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        record = hashes.read_hash_sidecar_record(str(destination))
        self.assertEqual(result, (0, 1))
        calculate_hash.assert_called_once()
        self.assertEqual(len(list(destination.parent.glob('*.jpg'))), 1)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.matches_source(str(moved_source), moved_source.stat()))

    def test_source_change_during_date_read_stops_before_writing_archive(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'original image')

        def mutate_during_exif(_path: str) -> datetime:
            source.write_bytes(b'changed image contents')
            return datetime(2024, 2, 3, 4, 5, 6)

        with (
            patch('main.get_exif_modify_time', side_effect=mutate_during_exif),
            self.assertRaises(hashes.FileChangedError),
        ):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(
            [path for path in self.dst_dir.iterdir() if not path.name.startswith('.pictree-')],
            [],
        )

    def test_sidecar_failure_does_not_publish_media_or_source_log(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image contents')
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch(
                'util.hashes._write_hash_sidecar_for_stat',
                side_effect=OSError('network error'),
            ),
            self.assertRaises(OSError),
        ):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        self.assertFalse(destination.exists())
        self.assertFalse(Path(str(destination) + '.sha1').exists())
        self.assertFalse((destination.parent / '.filesource').exists())
        self.assertEqual(list(destination.parent.glob('.pictree-copy-*.tmp')), [])

        with patch('main.get_exif_modify_time', return_value=captured_at):
            retry_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(retry_result, (1, 0))
        self.assertTrue(Path(str(destination) + '.sha1').exists())
        self.assertEqual(len(list(destination.parent.glob('*.jpg'))), 1)

    def test_log_failure_after_rounded_copy_retries_without_duplicate(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image contents')
        os.utime(source, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        real_copystat = hashes.copystat

        def round_destination_timestamp(source_path: str, destination_path: str) -> None:
            real_copystat(source_path, destination_path)
            rounded = os.stat(destination_path).st_mtime_ns // 1_000_000_000 * 1_000_000_000
            os.utime(destination_path, ns=(rounded, rounded))

        def fail_source_log(file, *args, **kwargs):
            if os.fspath(file).endswith(os.sep + '.filesource'):
                raise OSError('network error')
            return builtin_open(file, *args, **kwargs)

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('util.hashes.copystat', side_effect=round_destination_timestamp),
            patch('builtins.open', side_effect=fail_source_log),
            self.assertRaises(OSError),
        ):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        self.assertEqual(destination.read_bytes(), b'image contents')
        self.assertTrue(Path(str(destination) + '.sha1').is_file())
        self.assertNotEqual(destination.stat().st_mtime_ns, source.stat().st_mtime_ns)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            retry_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(retry_result, (0, 1))
        self.assertEqual(len(list(destination.parent.glob('*.jpg'))), 1)

    def test_smb_mtime_change_during_final_rename_is_recorded(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image contents')
        os.utime(source, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        real_replace = hashes.os.replace

        def replace_and_normalize_mtime(source_path: str, destination_path: str) -> None:
            real_replace(source_path, destination_path)
            if destination_path == str(destination):
                normalized_mtime_ns = 1_800_000_000_000_000_000
                os.utime(destination_path, ns=(normalized_mtime_ns, normalized_mtime_ns))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('util.hashes.os.replace', side_effect=replace_and_normalize_mtime),
            patch('util.hashes.calculate_file_hash') as destination_hash,
        ):
            first_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        record = hashes.read_hash_sidecar_record(str(destination))
        self.assertEqual(first_result, (1, 0))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.matches_file(destination.stat()))
        self.assertNotEqual(destination.stat().st_mtime_ns, source.stat().st_mtime_ns)
        destination_hash.assert_not_called()

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash') as calculate_hash,
        ):
            second_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertEqual(second_result, (0, 1))
        calculate_hash.assert_not_called()
        self.assertEqual(len(list(destination.parent.glob('*.jpg'))), 1)

    def test_interrupted_final_receipt_refresh_recovers_without_duplicate(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image contents')
        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        real_replace = hashes.os.replace
        real_write_sidecar = hashes._write_hash_sidecar_for_stat
        sidecar_write_count = 0

        def replace_and_normalize_mtime(source_path: str, destination_path: str) -> None:
            real_replace(source_path, destination_path)
            if destination_path == str(destination):
                normalized_mtime_ns = 1_800_000_000_000_000_000
                os.utime(destination_path, ns=(normalized_mtime_ns, normalized_mtime_ns))

        def interrupt_final_sidecar(*args, **kwargs) -> None:
            nonlocal sidecar_write_count
            sidecar_write_count += 1
            if sidecar_write_count == 2:
                raise KeyboardInterrupt
            real_write_sidecar(*args, **kwargs)

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('util.hashes.os.replace', side_effect=replace_and_normalize_mtime),
            patch(
                'util.hashes._write_hash_sidecar_for_stat',
                side_effect=interrupt_final_sidecar,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        stale_record = hashes.read_hash_sidecar_record(str(destination))
        self.assertTrue(destination.is_file())
        self.assertIsNotNone(stale_record)
        assert stale_record is not None
        self.assertFalse(stale_record.matches_file(destination.stat()))
        destination_inode = destination.stat().st_ino
        destination_contents = destination.read_bytes()

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
            patch('main.copy_file_atomically_with_hash') as copy_file,
        ):
            retry_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        repaired_record = hashes.read_hash_sidecar_record(str(destination))
        self.assertEqual(retry_result, (0, 1))
        calculate_hash.assert_called_once()
        self.assertEqual(calculate_hash.call_args.args, (str(destination),))
        copy_file.assert_not_called()
        self.assertEqual(destination.stat().st_ino, destination_inode)
        self.assertEqual(destination.read_bytes(), destination_contents)
        self.assertIsNotNone(repaired_record)
        assert repaired_record is not None
        self.assertTrue(repaired_record.matches_file(destination.stat()))
        self.assertEqual(len(list(destination.parent.glob('*.jpg'))), 1)

    def test_stale_receipt_never_hides_same_size_destination_change(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'AAAA')
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination = self.dst_dir / '2024' / '2' / '3' / '20240203-040506-photo.jpg'
        destination.write_bytes(b'BBBB')
        changed_mtime_ns = destination.stat().st_mtime_ns + 1_000_000_000
        os.utime(destination, ns=(changed_mtime_ns, changed_mtime_ns))

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.calculate_file_hash', wraps=main.calculate_file_hash) as calculate_hash,
        ):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        archived_files = list(destination.parent.glob('*.jpg'))
        self.assertEqual(result, (1, 0))
        self.assertEqual(destination.read_bytes(), b'BBBB')
        self.assertEqual(len(archived_files), 2)
        self.assertEqual({path.read_bytes() for path in archived_files}, {b'AAAA', b'BBBB'})
        hashed_paths = [call.args[0] for call in calculate_hash.call_args_list]
        self.assertEqual(hashed_paths.count(str(destination)), 1)
        self.assertEqual(hashed_paths.count(str(source)), 1)

    def test_sidecar_read_error_stops_without_archive_changes(self) -> None:
        source = self.src_dir / 'photo.jpg'
        source.write_bytes(b'image contents')
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        destination_dir = self.dst_dir / '2024' / '2' / '3'
        before = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in destination_dir.iterdir()
        }

        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch(
                'main.read_hash_sidecar_record',
                side_effect=PermissionError('sidecar unavailable'),
            ),
            patch('main.copy_file_atomically_with_hash') as copy_file,
            self.assertRaises(PermissionError),
        ):
            main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        after = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in destination_dir.iterdir()
        }
        self.assertEqual(after, before)
        copy_file.assert_not_called()

    def test_does_not_skip_different_content_with_matching_metadata(self) -> None:
        first_source = self.src_dir / 'first.jpg'
        first_source.write_bytes(b'AAAA')
        second_dir = Path(self.temp_dir.name, 'second-source')
        second_dir.mkdir()
        second_source = second_dir / 'second.jpg'
        second_source.write_bytes(b'BBBB')
        os.utime(first_source, (1000, 1000))
        os.utime(second_source, (1000, 1000))
        captured_at = datetime(2024, 2, 3, 4, 5, 6)

        with patch('main.get_exif_modify_time', return_value=captured_at):
            first_result = main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)
            second_result = main.sync_media(str(second_dir), str(self.dst_dir), verbosity=0)

        destination_dir = self.dst_dir / '2024' / '2' / '3'
        self.assertEqual(first_result, (1, 0))
        self.assertEqual(second_result, (1, 0))
        self.assertEqual(
            {path.read_bytes() for path in destination_dir.glob('*.jpg')},
            {b'AAAA', b'BBBB'},
        )

    def test_uses_unique_version_paths_for_multiple_conflicts(self) -> None:
        source_dirs = [self.src_dir]
        for index in range(2):
            source_dir = Path(self.temp_dir.name, f'additional-source-{index}')
            source_dir.mkdir()
            source_dirs.append(source_dir)

        for source_dir, contents in zip(source_dirs, (b'AAA', b'BBB', b'CCC')):
            source = source_dir / 'photo.jpg'
            source.write_bytes(contents)
            os.utime(source, (1000, 1000))

        captured_at = datetime(2024, 2, 3, 4, 5, 6)
        with (
            patch('main.get_exif_modify_time', return_value=captured_at),
            patch('main.time', return_value=1234),
        ):
            results = [
                main.sync_media(str(source_dir), str(self.dst_dir), verbosity=0)
                for source_dir in source_dirs
            ]

        destination_dir = self.dst_dir / '2024' / '2' / '3'
        self.assertEqual(results, [(1, 0), (1, 0), (1, 0)])
        self.assertEqual(
            {path.name for path in destination_dir.glob('*.jpg')},
            {
                '20240203-040506-photo.jpg',
                '20240203-040506-photo-v1234.jpg',
                '20240203-040506-photo-v1234-2.jpg',
            },
        )
        self.assertEqual(
            {path.read_bytes() for path in destination_dir.glob('*.jpg')},
            {b'AAA', b'BBB', b'CCC'},
        )

    def test_dry_run_does_not_create_destination_directories(self) -> None:
        (self.src_dir / 'photo.jpg').write_bytes(b'image')

        with patch('main.get_exif_modify_time', return_value=datetime(2024, 2, 3, 4, 5, 6)):
            result = main.sync_media(
                str(self.src_dir),
                str(self.dst_dir),
                dry_run=True,
                verbosity=0,
            )

        self.assertEqual(result, (1, 0))
        self.assertEqual(list(self.dst_dir.iterdir()), [])

    def test_refuses_to_start_while_destination_is_locked(self) -> None:
        (self.src_dir / 'photo.jpg').write_bytes(b'image')

        with destination_lock(str(self.dst_dir)):
            with self.assertRaises(DestinationLockedError):
                main.sync_media(str(self.src_dir), str(self.dst_dir), verbosity=0)

        self.assertFalse(any(path.name == '2024' for path in self.dst_dir.iterdir()))

    def test_rejects_destination_inside_source_tree(self) -> None:
        nested_destination = self.src_dir / 'archive'
        nested_destination.mkdir()
        (self.src_dir / 'photo.jpg').write_bytes(b'image')

        with self.assertRaises(main.UnsafeSyncPathsError):
            main.sync_media(str(self.src_dir), str(nested_destination), verbosity=0)

        self.assertEqual(list(nested_destination.iterdir()), [])

    def test_rejects_source_inside_destination_tree(self) -> None:
        nested_source = self.dst_dir / 'imports'
        nested_source.mkdir()
        (nested_source / 'photo.jpg').write_bytes(b'image')

        with self.assertRaises(main.UnsafeSyncPathsError):
            main.sync_media(str(nested_source), str(self.dst_dir), verbosity=0)

        self.assertFalse((self.dst_dir / '.pictree-sync.lock').exists())

    def test_overlap_check_uses_filesystem_identity_for_ancestors(self) -> None:
        identities = {
            '/real/source': (1, 10),
            '/alias/source/archive': (1, 20),
            '/alias/source': (1, 10),
        }

        with (
            patch('main.os.path.realpath', side_effect=lambda path: path),
            patch('main._directory_identity', side_effect=identities.__getitem__),
        ):
            result = main._directory_tree_contains(
                '/real/source',
                '/alias/source/archive',
            )

        self.assertTrue(result)

    def test_rejects_case_only_alias_on_case_insensitive_filesystem(self) -> None:
        case_alias = self.src_dir.with_name(self.src_dir.name.upper())
        if not case_alias.is_dir() or not os.path.samefile(self.src_dir, case_alias):
            self.skipTest('filesystem is case-sensitive')

        with self.assertRaises(main.UnsafeSyncPathsError):
            main.sync_media(str(self.src_dir), str(case_alias), verbosity=0)

    def test_skips_media_before_year_limit_and_ignores_unsupported_files(self) -> None:
        (self.src_dir / 'old.jpg').write_bytes(b'old image')
        (self.src_dir / 'notes.txt').write_text('not media', encoding='utf-8')
        (self.src_dir / '.hidden.jpg').write_bytes(b'hidden image')

        with patch('main.get_exif_modify_time', return_value=datetime(1999, 1, 1)):
            result = main.sync_media(
                str(self.src_dir),
                str(self.dst_dir),
                year_limit=2000,
                verbosity=0,
            )

        self.assertEqual(result, (0, 1))
        self.assertEqual(
            [path for path in self.dst_dir.iterdir() if not path.name.startswith('.pictree-')],
            [],
        )


class MainTests(unittest.TestCase):
    def test_rejects_missing_destination_directory(self) -> None:
        with tempfile.TemporaryDirectory() as source:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main.main([source, str(Path(source, 'missing'))])

        self.assertEqual(result, 1)
        self.assertIn('destination directory does not exist', output.getvalue())

    def test_rejects_non_numeric_year(self) -> None:
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as destination:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main.main([source, destination, 'nope'])

        self.assertEqual(result, 1)
        self.assertIn('lower limit must be a year', output.getvalue())


if __name__ == '__main__':
    unittest.main()

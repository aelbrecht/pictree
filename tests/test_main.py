import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import main


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
                patch('main.copy_file_with_hash') as copy_with_hash,
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
        calculate_hash.assert_called_once_with(str(source))
        self.assertTrue(sidecar.read_text(encoding='utf-8').startswith('a' * 40))

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

    def test_skips_media_before_year_limit_and_ignores_unsupported_files(self) -> None:
        (self.src_dir / 'old.jpg').write_bytes(b'old image')
        (self.src_dir / 'notes.txt').write_text('not media', encoding='utf-8')
        (self.src_dir / '.hidden.jpg').write_bytes(b'hidden image')

        with patch('main.get_exif_modify_time', return_value=datetime(1999, 1, 1)):
            result = main.sync_media(str(self.src_dir), str(self.dst_dir), year_limit=2000, verbosity=0)

        self.assertEqual(result, (0, 1))
        self.assertEqual(list(self.dst_dir.iterdir()), [])


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

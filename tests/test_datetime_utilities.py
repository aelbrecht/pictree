import contextlib
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from util import file_dates, fix_exif_datetime, modify_datetime


class FileDatesTests(unittest.TestCase):
    def test_passes_paths_as_subprocess_arguments(self) -> None:
        timestamp = datetime(2024, 1, 2, 3, 4, 5)
        file_path = '/tmp/photo $(touch injected).jpg'

        with patch('util.file_dates.subprocess.run') as run:
            file_dates.set_file_dates(
                '/usr/bin/SetFile',
                file_path,
                timestamp,
                set_creation=True,
                set_modification=True,
            )

        self.assertEqual(
            run.call_args_list,
            [
                call(
                    ['/usr/bin/SetFile', '-d', '01/02/2024 03:04:05', file_path],
                    check=True,
                ),
                call(
                    ['/usr/bin/SetFile', '-m', '01/02/2024 03:04:05', file_path],
                    check=True,
                ),
            ],
        )


class ModifyDatetimesTests(unittest.TestCase):
    def test_uses_full_path_and_rolls_into_the_next_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nested_dir = Path(temp_dir, 'nested')
            nested_dir.mkdir()
            video = nested_dir / 'clip.mp4'
            video.write_bytes(b'video')
            created = datetime(2024, 1, 2, 22, 30)
            stat_result = SimpleNamespace(st_birthtime=created.timestamp(), st_ctime=created.timestamp())

            with (
                patch('util.modify_datetime.os.stat', return_value=stat_result),
                patch('util.modify_datetime.set_file_dates') as set_dates,
            ):
                result = modify_datetime.modify_datetimes(temp_dir, '/usr/bin/SetFile', verbosity=0)

        self.assertEqual(result, 1)
        set_dates.assert_called_once_with(
            '/usr/bin/SetFile',
            str(video),
            datetime(2024, 1, 3, 3, 30),
            set_creation=True,
            set_modification=True,
        )

    def test_main_fails_cleanly_when_setfile_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with (
                patch('util.modify_datetime.find_setfile', return_value=None),
                contextlib.redirect_stdout(output),
            ):
                result = modify_datetime.main([temp_dir])

        self.assertEqual(result, 1)
        self.assertIn('no files were changed', output.getvalue())


class FixExifDatetimesTests(unittest.TestCase):
    def test_skips_images_without_a_timestamp_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, 'photo.jpg').write_bytes(b'image')
            with patch('util.fix_exif_datetime.set_file_dates') as set_dates:
                result = fix_exif_datetime.fix_exif_datetimes(
                    temp_dir,
                    '/usr/bin/SetFile',
                    verbosity=0,
                )

        self.assertEqual(result, (0, 1))
        set_dates.assert_not_called()

    def test_handles_filesystems_without_birth_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir, '20240102-030405-photo.jpg')
            image.write_bytes(b'image')
            stat_result = SimpleNamespace(st_mtime=datetime(2025, 1, 1).timestamp())

            with (
                patch('util.fix_exif_datetime.os.stat', return_value=stat_result),
                patch('util.fix_exif_datetime.get_exif_modify_time', return_value=None),
                patch('util.fix_exif_datetime.set_file_dates') as set_dates,
            ):
                result = fix_exif_datetime.fix_exif_datetimes(
                    temp_dir,
                    '/usr/bin/SetFile',
                    verbosity=0,
                )

        self.assertEqual(result, (1, 0))
        set_dates.assert_called_once_with(
            '/usr/bin/SetFile',
            str(image),
            datetime(2024, 1, 2, 3, 4, 5),
            set_creation=True,
        )

    def test_main_fails_cleanly_when_setfile_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with (
                patch('util.fix_exif_datetime.find_setfile', return_value=None),
                contextlib.redirect_stdout(output),
            ):
                result = fix_exif_datetime.main([temp_dir])

        self.assertEqual(result, 1)
        self.assertIn('no files were changed', output.getvalue())

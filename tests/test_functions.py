import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from util.functions import get_exif_modify_time, is_media_extension


class GetExifModifyTimeTests(unittest.TestCase):
    def test_reads_original_capture_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir, 'photo.jpg')
            exif = Image.Exif()
            exif[36867] = '2024:06:07 08:09:10'
            Image.new('RGB', (1, 1)).save(image_path, exif=exif)

            result = get_exif_modify_time(str(image_path))

        self.assertEqual(result, datetime(2024, 6, 7, 8, 9, 10))

    def test_falls_back_to_digitized_date_when_original_date_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir, 'photo.jpg')
            exif = Image.Exif()
            exif[36867] = 'not a date'
            exif[36868] = '2022:03:04 05:06:07'
            Image.new('RGB', (1, 1)).save(image_path, exif=exif)

            result = get_exif_modify_time(str(image_path))

        self.assertEqual(result, datetime(2022, 3, 4, 5, 6, 7))

    def test_returns_none_for_non_image_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir, 'clip.mp4')
            media_path.write_bytes(b'not an image')

            result = get_exif_modify_time(str(media_path))

        self.assertIsNone(result)

    def test_returns_none_for_malformed_xmp(self) -> None:
        media = MagicMock()
        media.getexif.return_value = {}
        media.info = {'XML:com.adobe.xmp': '<invalid'}
        opened_image = MagicMock()
        opened_image.__enter__.return_value = media

        with patch('util.functions.Image.open', return_value=opened_image):
            result = get_exif_modify_time('photo.jpg', use_load=True)

        self.assertIsNone(result)


class IsMediaExtensionTests(unittest.TestCase):
    def test_recognizes_supported_media_extensions(self) -> None:
        self.assertTrue(is_media_extension('.jpg'))
        self.assertTrue(is_media_extension('.mp4'))
        self.assertTrue(is_media_extension('.heic'))
        self.assertFalse(is_media_extension('.txt'))
        self.assertFalse(is_media_extension('.JPG'))


if __name__ == '__main__':
    unittest.main()

import mimetypes
import sys
from datetime import datetime
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 933120000


def get_exif_modify_time(path, use_load=False):
    try:
        media = Image.open(path)
        exif_data = media.getexif()  # noqa
        if len(exif_data.keys()) == 0 and use_load:
            media.load()
            info = media.info.get('XML:com.adobe.xmp')
            if info is None:
                return None
            tree = ElementTree.fromstring(info)
            created = tree.find(".//{http://ns.adobe.com/photoshop/1.0/}DateCreated")
            if created is None:
                return None
            return datetime.strptime(created.text, '%Y-%m-%dT%H:%M:%S')
        if exif_data is None:
            return None
        if 36867 in exif_data:  # The date and time when the original image data was generated.
            try:
                return datetime.strptime(exif_data[36867], '%Y:%m:%d %H:%M:%S')
            except ValueError:
                pass
        if 36868 in exif_data:  # The date and time when the image was stored as digital data.
            try:
                return datetime.strptime(exif_data[36868], '%Y:%m:%d %H:%M:%S')
            except ValueError:
                pass
        if 306 in exif_data:  # The date and time of image creation.
            try:
                return datetime.strptime(exif_data[306], '%Y:%m:%d %H:%M:%S')
            except ValueError:
                pass
    except UnidentifiedImageError:
        return None
    except AttributeError:
        return None
    except Image.DecompressionBombError:
        print(f"image too large: {path}")
        sys.exit(1)


def get_extensions_for_type(general_type):
    for ext in mimetypes.types_map:
        if mimetypes.types_map[ext].split('/')[0] == general_type:
            yield ext


mimetypes.init()
video_ext = list(get_extensions_for_type('video')) + ['.m4v']
image_ext = list(get_extensions_for_type('image')) + ['.heic', '.cr2', '.xmp', '.aee', '.aae']
audio_ext = list(get_extensions_for_type('audio'))


def is_media_extension(ext: str) -> bool:
    return ext in image_ext or ext in video_ext or ext in audio_ext

import os
import sys
from sys import argv
from PIL import Image
import mimetypes
from datetime import datetime
from time import time
from shutil import copyfile, copystat


def get_extensions_for_type(general_type):
    for ext in mimetypes.types_map:
        if mimetypes.types_map[ext].split('/')[0] == general_type:
            yield ext


mimetypes.init()
video_ext = list(get_extensions_for_type('video')) + ['.m4v']
image_ext = list(get_extensions_for_type('image')) + ['.heic', '.cr2', '.xmp', '.aee', '.aae']
audio_ext = list(get_extensions_for_type('audio'))


def get_exif(path):
    try:
        media = Image.open(path)
        exif_data = media._getexif()
        tag = 306
        if 36867 in exif_data:
            tag = 36867
        elif 36868 in exif_data:
            tag = 36868
        return datetime.strptime(exif_data[tag], '%Y:%m:%d %H:%M:%S')
    except:
        return datetime.fromtimestamp(os.path.getmtime(path))


if __name__ == '__main__':

    # parse arguments
    if len(argv) < 3:
        print("missing arguments: python main.py ./Photos.photoslibrary/originals ./destination")
        sys.exit(1)
    root_directory = argv[1]
    dst_directory = argv[2]

    file_count = 0
    start_time = time()

    for dir_name, _, file_list in os.walk(root_directory):
        for file_name in file_list:
            if file_name[0] == '.':
                continue
            ext = '.' + str(file_name.split('.').pop()).lower()
            if ext in image_ext or ext in video_ext or ext in audio_ext:
                file_src_path = '%s/%s' % (dir_name, file_name)
                file_last_modified = get_exif(file_src_path)

                year = file_last_modified.year
                month = file_last_modified.month
                day = file_last_modified.day

                raw_file_name = file_last_modified.strftime('%Y%m%d-%H%M%S') + '-%s' % ''.join(
                    file_name.split('.')[:-1])
                file_dst_dir = '%s/%d/%d/%d' % (dst_directory, year, month, day)
                file_dst_path = '%s/%s' % (file_dst_dir, raw_file_name + ext)

                if not os.path.exists('%s/%d' % (dst_directory, year)):
                    os.mkdir('%s/%d' % (dst_directory, year))

                if not os.path.exists('%s/%d/%d' % (dst_directory, year, month)):
                    os.mkdir('%s/%d/%d' % (dst_directory, year, month))

                if not os.path.exists('%s/%d/%d/%d' % (dst_directory, year, month, day)):
                    os.mkdir('%s/%d/%d/%d' % (dst_directory, year, month, day))

                if os.path.isfile(file_dst_path):
                    src_stat = os.stat(file_src_path)
                    dst_stat = os.stat(file_dst_path)

                    if dst_stat.st_mtime == src_stat.st_mtime and dst_stat.st_size == src_stat.st_size:
                        print('%s skipped' % file_dst_path)
                        continue
                    else:
                        if dst_stat.st_size != 0:
                            suffix = '-v' + str(round(time()))
                            file_dst_path = '%s/%s' % (file_dst_dir, raw_file_name + suffix + ext)

                with open('%s/%s' % (file_dst_dir, '.filesource'), "a+") as f:
                    f.write('%s,%s\n' % (file_src_path, file_dst_path))

                print('%s' % file_dst_path)
                copyfile(file_src_path, file_dst_path)
                copystat(file_src_path, file_dst_path)
                file_count += 1
            else:
                print('%s/%s unsupported' % (dir_name, file_name))

from datetime import datetime
import os
from sys import argv
import sys
from functions import get_exif_modify_time, get_extensions_for_type
from shutil import copy2

if __name__ == '__main__':

    if len(argv) < 2:
        print("missing arguments, usage: python fix-exif-datetime.py [src]")
        sys.exit(1)

    src_dir = argv[1]
    if not os.path.exists(src_dir):
        print("source directory does not exist")
        sys.exit(1)

    for dir_name, _, file_list in os.walk(src_dir):

        for file_name in file_list:

            if file_name[0] == '.':
                continue

            file_src_path = '%s/%s' % (dir_name, file_name)

            ext = '.' + str(file_name.split('.').pop()).lower()

            if ext not in list(get_extensions_for_type('image')):
                continue

            info = os.stat(file_src_path)
            modified = datetime.fromtimestamp(info.st_mtime)
            created = datetime.fromtimestamp(info.st_birthtime)

            ts_str = file_name[:15]
            file_ts = datetime.strptime(ts_str, '%Y%m%d-%H%M%S')

            exif_ts = get_exif_modify_time(file_src_path, use_load=True)
            if exif_ts is not None:
                continue

            if modified.isoformat() == created.isoformat() and file_ts.isoformat() == created.isoformat():
                continue

            os.system('SetFile -d "{}" "{}"'.format(file_ts.strftime('%m/%d/%Y %H:%M:%S'), file_src_path))

            print(file_src_path)

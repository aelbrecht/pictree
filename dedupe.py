import os
import sys
from datetime import datetime
from sys import argv
from time import time

from util.functions import is_media_extension, get_exif_modify_time

dry_run = False
delete_list = {}

if __name__ == '__main__':

    if len(argv) < 2:
        print("missing arguments, usage: python dedupe.py [src]")
        sys.exit(1)

    src_dir = argv[1]
    if not os.path.exists(src_dir):
        print("source directory does not exist")
        sys.exit(1)

    delete_count_total = 0
    start_time = time()

    # iterate all directories
    for dir_name, _, file_list in os.walk(src_dir):

        # iterate all files in a single directory
        for file_name in file_list:

            if f"{dir_name}/{file_name}" in delete_list:
                continue

            # check if file should be excluded
            if file_name[0] == '.':
                continue

            # extract extension from file
            ext = '.' + str(file_name.split('.').pop()).lower()

            # check if file is a form of media, otherwise skip
            if not is_media_extension(ext):
                continue

            # get full src path
            file_src_path = f"{dir_name}/{file_name}"

            # extract most accurate media creation date to organize by
            last_modified = get_exif_modify_time(file_src_path)
            if last_modified is None:
                last_modified = datetime.fromtimestamp(os.path.getmtime(file_src_path))

            # get prefix
            time_prefix = last_modified.strftime('%Y%m%d-%H%M%S')

            # check if the same file does not exist under a different name
            is_duplicate = False
            fn_duplicate = None
            for candidate in os.listdir(dir_name):
                if candidate.startswith(time_prefix) and candidate != file_name:
                    src_stat = os.stat(file_src_path)
                    dst_stat = os.stat(f"{dir_name}/{candidate}")
                    if src_stat.st_size != dst_stat.st_size:
                        continue
                    if src_stat.st_mtime != dst_stat.st_mtime:
                        continue
                    is_duplicate = True
                    fn_duplicate = candidate
                    break

            if not is_duplicate:
                continue

            # print(f"{file_name} === {fn_duplicate}")

            remove_filename = file_name if len(file_name) > len(fn_duplicate) else fn_duplicate
            orig_filename = file_name if len(file_name) < len(fn_duplicate) else fn_duplicate
            remove_path = f"{dir_name}/{remove_filename}"
            delete_list[remove_path] = True

            print(f"[deleting]\t{remove_filename}", end='\r')

            if not dry_run:
                os.remove(remove_path)

            print(f"[finished]\t{remove_filename}\t(original: {orig_filename})")

            delete_count_total += 1

    ss = (time() - start_time) / 60
    print("total deleted: {} \t took {:.2f} minutes".format(delete_count_total, ss))

import os
import sys
from datetime import datetime
from shutil import copyfile, copystat
from sys import argv
from time import time

from util.functions import is_media_extension, get_exif_modify_time

if __name__ == '__main__':

    if len(argv) < 3:
        print("missing arguments, usage: python main.py [src] [dst]")
        sys.exit(1)

    src_dir = argv[1]
    if not os.path.exists(src_dir):
        print("source directory does not exist")
        sys.exit(1)

    dst_dir = argv[2]
    if not os.path.exists(src_dir):
        print("destination directory does not exist")
        sys.exit(1)

    copy_count_total = 0
    start_time = time()

    # iterate all directories
    for dir_name, _, file_list in os.walk(src_dir):

        print("{: <64} \t found {: <6}".format(dir_name, len(file_list)), end='')
        copy_count = 0
        skip_count = 0

        # iterate all files in a single directory
        for file_name in file_list:

            # check if file should be excluded
            if file_name[0] == '.':
                continue

            # extract extension from file
            ext = '.' + str(file_name.split('.').pop()).lower()

            # check if file is a form of media, otherwise skip
            if not is_media_extension(ext):
                print('%s/%s unsupported' % (dir_name, file_name))
                continue

            # get full src path
            file_src_path = '%s/%s' % (dir_name, file_name)

            # extract most accurate media creation date to organize by
            last_modified = get_exif_modify_time(file_src_path)
            if last_modified is None:
                last_modified = datetime.fromtimestamp(os.path.getmtime(file_src_path))

            # create directories
            file_dst_dir = '%s/%s' % (dst_dir, last_modified.strftime('%Y/%-m/%-d'))
            if not os.path.exists(file_dst_dir):
                os.makedirs(file_dst_dir)

            # create dst filename
            raw_file_name = last_modified.strftime('%Y%m%d-%H%M%S') + '-%s' % ''.join(file_name.split('.')[:-1])
            file_dst_path = '%s/%s' % (file_dst_dir, raw_file_name + ext)

            # check if file already exists and is the same as the one being copied over
            if os.path.isfile(file_dst_path):
                src_stat = os.stat(file_src_path)
                dst_stat = os.stat(file_dst_path)
                if dst_stat.st_mtime == src_stat.st_mtime and dst_stat.st_size == src_stat.st_size:
                    skip_count += 1
                else:
                    if dst_stat.st_size != 0:
                        suffix = '-v' + str(round(time()))
                        file_dst_path = '%s/%s' % (file_dst_dir, raw_file_name + suffix + ext)

            # keep track of original media name
            with open('%s/%s' % (file_dst_dir, '.filesource'), "a+") as f:
                f.write('%s,%s\n' % (file_src_path, file_dst_path))

            # copy original media
            copyfile(file_src_path, file_dst_path)
            copystat(file_src_path, file_dst_path)
            copy_count += 1
            copy_count_total += 1

        print(" \t copied {: <6} \t skipped {: <6}".format(copy_count, skip_count))

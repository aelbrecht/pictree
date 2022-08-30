import os
import sys
from datetime import datetime
from shutil import copyfile, copystat
from sys import argv
from time import time

from util.functions import is_media_extension, get_exif_modify_time

dry_run = False
verbosity = 2

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

    year_limit = 0
    if len(argv) > 3:
        if len(argv[3]) != 4:
            print("lower limit must be a year (e.g. 2007)")
            sys.exit(1)
        else:
            year_limit = int(argv[3])

    copy_count_total = 0
    skip_count_total = 0
    start_time = time()

    if verbosity > 0:
        print(f"{src_dir} -> {dst_dir}")

    # iterate all directories
    for dir_name, _, file_list in os.walk(src_dir):

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
                if verbosity > 0:
                    print('%s/%s unsupported' % (dir_name, file_name))
                continue

            # get full src path
            file_src_path = '%s/%s' % (dir_name, file_name)

            # extract most accurate media creation date to organize by
            last_modified = get_exif_modify_time(file_src_path)
            if last_modified is None:
                last_modified = datetime.fromtimestamp(os.path.getmtime(file_src_path))

            # check if year limit has been set
            if last_modified.year < year_limit:
                skip_count += 1
                skip_count_total += 1
                continue

            # create directories
            file_dst_dir = '%s/%s' % (dst_dir, last_modified.strftime('%Y/%-m/%-d'))
            if not os.path.exists(file_dst_dir):
                os.makedirs(file_dst_dir)

            # create dst filename
            time_prefix = last_modified.strftime('%Y%m%d-%H%M%S')
            raw_file_name = f"{time_prefix}-{''.join(file_name.split('.')[:-1])}"
            full_file_name = raw_file_name + ext
            file_dst_path = '%s/%s' % (file_dst_dir, full_file_name)

            # check if file already exists and is the same as the one being copied over
            if os.path.isfile(file_dst_path):
                src_stat = os.stat(file_src_path)
                dst_stat = os.stat(file_dst_path)
                if dst_stat.st_mtime == src_stat.st_mtime and dst_stat.st_size == src_stat.st_size:
                    skip_count += 1
                    skip_count_total += 1
                    if verbosity > 3:
                        print(f"skipped file: {full_file_name}")
                    continue
                else:
                    if dst_stat.st_size != 0:
                        suffix = '-v' + str(round(time()))
                        file_dst_path = '%s/%s' % (file_dst_dir, raw_file_name + suffix + ext)

            # check if the same file does not exist under a different name
            is_duplicate = False

            for candidate in os.listdir(file_dst_dir):
                if candidate.startswith(time_prefix):
                    src_stat = os.stat(file_src_path)
                    dst_stat = os.stat(f"{file_dst_dir}/{candidate}")
                    if src_stat.st_size != dst_stat.st_size:
                        continue
                    if src_stat.st_mtime != dst_stat.st_mtime:
                        continue
                    if verbosity > 2:
                        print(f"duplicate found: {candidate} -> {full_file_name}")
                    is_duplicate = True
                    break

            if is_duplicate:
                skip_count += 1
                skip_count_total += 1
                continue

            if verbosity > 1:
                print(f" ==> [copying]\t{full_file_name}", end='\r')

            if not dry_run:
                # keep track of original media name
                with open('%s/%s' % (file_dst_dir, '.filesource'), "a+") as f:
                    f.write('%s,%s\n' % (file_src_path, file_dst_path))

                # copy media
                copyfile(file_src_path, file_dst_path)
                copystat(file_src_path, file_dst_path)

            if verbosity > 1:
                print(f" ==> [finished]\t{full_file_name}")

            copy_count += 1
            copy_count_total += 1

        if verbosity > 0:
            print("{: <64} \t found {: <6} \t copied {: <6} \t skipped {: <6}".format(dir_name, len(file_list),
                                                                                      copy_count,
                                                                                      skip_count))

    ss = (time() - start_time) / 60
    if verbosity > 0:
        print("total copied: {} \t total skipped: {} \t took {:.2f} minutes".format(copy_count_total, skip_count_total,
                                                                                    ss))

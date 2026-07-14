import os
import sys
from datetime import datetime
from shutil import copyfile, copystat
from time import time
from typing import Sequence

from util.functions import get_exif_modify_time, is_media_extension


def sync_media(
    src_dir: str,
    dst_dir: str,
    year_limit: int = 0,
    *,
    dry_run: bool = False,
    verbosity: int = 2,
) -> tuple[int, int]:
    copy_count_total = 0
    skip_count_total = 0
    start_time = time()

    if verbosity > 0:
        print(f"{src_dir} -> {dst_dir}")

    for dir_name, _, file_list in os.walk(src_dir):
        copy_count = 0
        skip_count = 0

        for file_name in file_list:
            if file_name[0] == '.':
                continue

            ext = '.' + str(file_name.split('.').pop()).lower()
            if not is_media_extension(ext):
                if verbosity > 0:
                    print(f"{dir_name}/{file_name} unsupported")
                continue

            file_src_path = os.path.join(dir_name, file_name)
            last_modified = get_exif_modify_time(file_src_path)
            if last_modified is None:
                last_modified = datetime.fromtimestamp(os.path.getmtime(file_src_path))

            if last_modified.year < year_limit:
                skip_count += 1
                skip_count_total += 1
                continue

            file_dst_dir = os.path.join(dst_dir, last_modified.strftime('%Y/%-m/%-d'))
            os.makedirs(file_dst_dir, exist_ok=True)

            time_prefix = last_modified.strftime('%Y%m%d-%H%M%S')
            raw_file_name = f"{time_prefix}-{''.join(file_name.split('.')[:-1])}"
            full_file_name = raw_file_name + ext
            file_dst_path = os.path.join(file_dst_dir, full_file_name)

            if os.path.isfile(file_dst_path):
                src_stat = os.stat(file_src_path)
                dst_stat = os.stat(file_dst_path)
                if dst_stat.st_mtime == src_stat.st_mtime and dst_stat.st_size == src_stat.st_size:
                    skip_count += 1
                    skip_count_total += 1
                    if verbosity > 3:
                        print(f"skipped file: {full_file_name}")
                    continue
                if dst_stat.st_size != 0:
                    suffix = '-v' + str(round(time()))
                    file_dst_path = os.path.join(file_dst_dir, raw_file_name + suffix + ext)

            is_duplicate = False
            for candidate in os.listdir(file_dst_dir):
                if candidate.startswith(time_prefix):
                    src_stat = os.stat(file_src_path)
                    dst_stat = os.stat(os.path.join(file_dst_dir, candidate))
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
                source_log_path = os.path.join(file_dst_dir, '.filesource')
                with open(source_log_path, 'a+', encoding='utf-8') as source_log:
                    source_log.write(f"{file_src_path},{file_dst_path}\n")

                copyfile(file_src_path, file_dst_path)
                copystat(file_src_path, file_dst_path)

            if verbosity > 1:
                print(f" ==> [finished]\t{full_file_name}")

            copy_count += 1
            copy_count_total += 1

        if verbosity > 0:
            print(
                "{: <64} \t found {: <6} \t copied {: <6} \t skipped {: <6}".format(
                    dir_name,
                    len(file_list),
                    copy_count,
                    skip_count,
                )
            )

    elapsed_minutes = (time() - start_time) / 60
    if verbosity > 0:
        print(
            "total copied: {} \t total skipped: {} \t took {:.2f} minutes".format(
                copy_count_total,
                skip_count_total,
                elapsed_minutes,
            )
        )

    return copy_count_total, skip_count_total


def main(args: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if len(args) < 2:
        print("missing arguments, usage: python main.py [src] [dst] [year]")
        return 1

    src_dir, dst_dir = args[:2]
    if not os.path.isdir(src_dir):
        print("source directory does not exist")
        return 1
    if not os.path.isdir(dst_dir):
        print("destination directory does not exist")
        return 1

    year_limit = 0
    if len(args) > 2:
        year = args[2]
        if len(year) != 4 or not year.isdigit():
            print("lower limit must be a year (e.g. 2007)")
            return 1
        year_limit = int(year)

    sync_media(src_dir, dst_dir, year_limit)
    return 0


if __name__ == '__main__':
    sys.exit(main())

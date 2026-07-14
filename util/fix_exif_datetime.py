import os
import subprocess
import sys
from datetime import datetime
from typing import Sequence

if __package__:
    from .file_dates import find_setfile, set_file_dates
    from .functions import get_exif_modify_time, get_extensions_for_type
else:
    from file_dates import find_setfile, set_file_dates
    from functions import get_exif_modify_time, get_extensions_for_type


def parse_filename_timestamp(file_name: str) -> datetime | None:
    try:
        return datetime.strptime(file_name[:15], '%Y%m%d-%H%M%S')
    except ValueError:
        return None


def fix_exif_datetimes(src_dir: str, setfile_path: str, *, verbosity: int = 1) -> tuple[int, int]:
    image_extensions = set(get_extensions_for_type('image'))
    update_count = 0
    skip_count = 0

    for dir_name, _, file_list in os.walk(src_dir):
        for file_name in file_list:
            if file_name[0] == '.':
                continue

            ext = os.path.splitext(file_name)[1].lower()
            if ext not in image_extensions:
                continue

            file_timestamp = parse_filename_timestamp(file_name)
            if file_timestamp is None:
                skip_count += 1
                if verbosity > 0:
                    print(f"[skipped] invalid timestamp prefix: {os.path.join(dir_name, file_name)}")
                continue

            file_src_path = os.path.join(dir_name, file_name)
            info = os.stat(file_src_path)
            modified = datetime.fromtimestamp(info.st_mtime)
            birth_timestamp = getattr(info, 'st_birthtime', None)
            created = datetime.fromtimestamp(birth_timestamp) if birth_timestamp is not None else None

            if get_exif_modify_time(file_src_path, use_load=True) is not None:
                skip_count += 1
                continue

            if created is not None and modified == created and file_timestamp == created:
                skip_count += 1
                continue

            set_file_dates(setfile_path, file_src_path, file_timestamp, set_creation=True)
            update_count += 1
            if verbosity > 0:
                print(file_src_path)

    return update_count, skip_count


def main(args: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if len(args) < 1:
        print("missing arguments, usage: python util/fix-exif-datetime.py [src]")
        return 1

    src_dir = args[0]
    if not os.path.isdir(src_dir):
        print("source directory does not exist")
        return 1

    setfile_path = find_setfile()
    if setfile_path is None:
        print("SetFile is required to update creation dates; no files were changed")
        return 1

    try:
        fix_exif_datetimes(src_dir, setfile_path)
    except subprocess.CalledProcessError as error:
        print(f"SetFile failed with exit code {error.returncode}")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

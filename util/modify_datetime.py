import os
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Sequence

if __package__:
    from .file_dates import find_setfile, set_file_dates
else:
    from file_dates import find_setfile, set_file_dates


def modify_datetimes(target_dir: str, setfile_path: str, *, verbosity: int = 1) -> int:
    update_count = 0
    for dir_name, _, file_list in os.walk(target_dir, topdown=False):
        for file_name in file_list:
            if file_name[0] == '.' or os.path.splitext(file_name)[1].lower() != '.mp4':
                continue

            file_path = os.path.join(dir_name, file_name)
            info = os.stat(file_path)
            created_timestamp = getattr(info, 'st_birthtime', None)
            if created_timestamp is None:
                created_timestamp = info.st_ctime
            created = datetime.fromtimestamp(created_timestamp)
            adjusted = created + timedelta(hours=5)

            if verbosity > 0:
                print(file_path)
                print(created)
                print(adjusted)

            set_file_dates(
                setfile_path,
                file_path,
                adjusted,
                set_creation=True,
                set_modification=True,
            )
            update_count += 1

    return update_count


def main(args: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if len(args) < 1:
        print("missing arguments, usage: python util/modify-datetime.py [src]")
        return 1

    target_dir = args[0]
    if not os.path.isdir(target_dir):
        print("source directory does not exist")
        return 1

    setfile_path = find_setfile()
    if setfile_path is None:
        print("SetFile is required to update creation dates; no files were changed")
        return 1

    try:
        modify_datetimes(target_dir, setfile_path)
    except subprocess.CalledProcessError as error:
        print(f"SetFile failed with exit code {error.returncode}")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

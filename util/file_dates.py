import shutil
import subprocess
from datetime import datetime


def find_setfile() -> str | None:
    return shutil.which('SetFile')


def set_file_dates(
    setfile_path: str,
    file_path: str,
    value: datetime,
    *,
    set_creation: bool = False,
    set_modification: bool = False,
) -> None:
    formatted_value = value.strftime('%m/%d/%Y %H:%M:%S')
    if set_creation:
        subprocess.run([setfile_path, '-d', formatted_value, file_path], check=True)
    if set_modification:
        subprocess.run([setfile_path, '-m', formatted_value, file_path], check=True)

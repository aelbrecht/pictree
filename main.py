import os
import sys
from datetime import datetime
from time import time
from typing import Sequence

from util.functions import get_exif_modify_time, is_media_extension
from util.hashes import (
    DestinationChangedError,
    FileChangedError,
    HASH_SUFFIX,
    calculate_file_hash,
    copy_file_atomically_with_hash,
    get_or_create_file_hash,
    read_hash_sidecar_record,
    require_file_unchanged,
    write_hash_sidecar,
)
from util.locking import DestinationLockedError, destination_lock


class UnsafeSyncPathsError(ValueError):
    """Raised when source and destination directory trees overlap."""


def require_separate_directory_trees(source_dir: str, destination_dir: str) -> None:
    source_real_path = os.path.realpath(source_dir)
    destination_real_path = os.path.realpath(destination_dir)
    try:
        common_path = os.path.commonpath([source_real_path, destination_real_path])
    except ValueError:
        return
    if common_path in (source_real_path, destination_real_path):
        raise UnsafeSyncPathsError(
            'source and destination directory trees must not overlap: '
            f'{source_dir} -> {destination_dir}'
        )


def get_available_version_path(file_dst_dir: str, raw_file_name: str, ext: str) -> str:
    suffix = f'-v{round(time())}'
    candidate = os.path.join(file_dst_dir, raw_file_name + suffix + ext)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(file_dst_dir, f'{raw_file_name}{suffix}-{counter}{ext}')
        counter += 1
    return candidate


def destination_matches_source(
    source_path: str,
    source_stat: os.stat_result,
    destination_path: str,
    destination_stat: os.stat_result,
    source_hash: str | None,
    *,
    persist_sidecar: bool,
    trust_legacy_metadata: bool,
) -> tuple[bool, str | None]:
    if source_stat.st_size != destination_stat.st_size:
        return False, source_hash

    hash_record = read_hash_sidecar_record(destination_path)
    has_valid_sidecar = hash_record is not None and hash_record.matches_file(destination_stat)
    if has_valid_sidecar:
        assert hash_record is not None
        if hash_record.matches_source(source_path, source_stat):
            require_file_unchanged(source_path, source_stat)
            require_file_unchanged(destination_path, destination_stat)
            return True, source_hash
        if source_hash is None:
            source_hash = calculate_file_hash(source_path, expected_stat=source_stat)
        if source_hash != hash_record.digest:
            return False, source_hash
        if persist_sidecar:
            write_hash_sidecar(
                destination_path,
                source_hash,
                source_path=source_path,
                source_stat=source_stat,
                expected_file_stat=destination_stat,
            )
        else:
            require_file_unchanged(source_path, source_stat)
            require_file_unchanged(destination_path, destination_stat)
        return True, source_hash

    if destination_stat.st_mtime != source_stat.st_mtime:
        return False, source_hash

    if source_hash is None and (persist_sidecar or not trust_legacy_metadata):
        source_hash = calculate_file_hash(source_path, expected_stat=source_stat)

    if not trust_legacy_metadata:
        destination_hash = get_or_create_file_hash(destination_path, persist=persist_sidecar)
        if source_hash != destination_hash:
            return False, source_hash
        if persist_sidecar:
            assert source_hash is not None
            write_hash_sidecar(
                destination_path,
                source_hash,
                source_path=source_path,
                source_stat=source_stat,
                expected_file_stat=destination_stat,
            )
        else:
            require_file_unchanged(source_path, source_stat)
            require_file_unchanged(destination_path, destination_stat)
        return True, source_hash

    # Preserve the canonical legacy fast path: matching size and timestamp are
    # trusted when an existing archive file has no usable pictree sidecar.
    if persist_sidecar:
        assert source_hash is not None
        write_hash_sidecar(
            destination_path,
            source_hash,
            source_path=source_path,
            source_stat=source_stat,
            expected_file_stat=destination_stat,
        )
    else:
        require_file_unchanged(source_path, source_stat)
    return True, source_hash


def sync_media(
    src_dir: str,
    dst_dir: str,
    year_limit: int = 0,
    *,
    dry_run: bool = False,
    verbosity: int = 2,
) -> tuple[int, int]:
    require_separate_directory_trees(src_dir, dst_dir)
    if dry_run:
        return _sync_media_unlocked(
            src_dir,
            dst_dir,
            year_limit,
            dry_run=True,
            verbosity=verbosity,
        )
    with destination_lock(dst_dir):
        return _sync_media_unlocked(
            src_dir,
            dst_dir,
            year_limit,
            dry_run=False,
            verbosity=verbosity,
        )


def _sync_media_unlocked(
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
            src_stat = os.stat(file_src_path)
            last_modified = get_exif_modify_time(file_src_path)
            require_file_unchanged(file_src_path, src_stat)
            if last_modified is None:
                last_modified = datetime.fromtimestamp(src_stat.st_mtime)

            if last_modified.year < year_limit:
                skip_count += 1
                skip_count_total += 1
                continue

            file_dst_dir = os.path.join(dst_dir, last_modified.strftime('%Y/%-m/%-d'))
            if not dry_run:
                os.makedirs(file_dst_dir, exist_ok=True)

            time_prefix = last_modified.strftime('%Y%m%d-%H%M%S')
            raw_file_name = f"{time_prefix}-{''.join(file_name.split('.')[:-1])}"
            full_file_name = raw_file_name + ext
            file_dst_path = os.path.join(file_dst_dir, full_file_name)
            source_hash: str | None = None

            if os.path.isfile(file_dst_path):
                dst_stat = os.stat(file_dst_path)
                is_same_file, source_hash = destination_matches_source(
                    file_src_path,
                    src_stat,
                    file_dst_path,
                    dst_stat,
                    source_hash,
                    persist_sidecar=not dry_run,
                    trust_legacy_metadata=True,
                )
                if is_same_file:
                    skip_count += 1
                    skip_count_total += 1
                    if verbosity > 3:
                        print(f"skipped file: {full_file_name}")
                    continue
                if dst_stat.st_size != 0:
                    file_dst_path = get_available_version_path(file_dst_dir, raw_file_name, ext)

            is_duplicate = False
            candidates = os.listdir(file_dst_dir) if os.path.isdir(file_dst_dir) else []
            for candidate in candidates:
                if candidate.startswith(time_prefix) and not candidate.endswith(HASH_SUFFIX):
                    candidate_path = os.path.join(file_dst_dir, candidate)
                    if not os.path.isfile(candidate_path):
                        continue
                    dst_stat = os.stat(candidate_path)
                    is_same_file, source_hash = destination_matches_source(
                        file_src_path,
                        src_stat,
                        candidate_path,
                        dst_stat,
                        source_hash,
                        persist_sidecar=not dry_run,
                        trust_legacy_metadata=False,
                    )
                    if not is_same_file:
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
                copy_file_atomically_with_hash(
                    file_src_path,
                    file_dst_path,
                    expected_source_stat=src_stat,
                )

                source_log_path = os.path.join(file_dst_dir, '.filesource')
                with open(source_log_path, 'a+', encoding='utf-8') as source_log:
                    source_log.write(f"{file_src_path},{file_dst_path}\n")

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

    try:
        sync_media(src_dir, dst_dir, year_limit)
    except (
        DestinationLockedError,
        DestinationChangedError,
        FileChangedError,
        UnsafeSyncPathsError,
    ) as error:
        print(f'sync stopped safely: {error}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

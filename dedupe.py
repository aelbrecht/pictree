import os
import sys
from time import time
from typing import Sequence

from util.functions import is_media_extension
from util.hashes import HASH_SUFFIX, get_or_create_file_hash, remove_hash_sidecar


def deduplicate(src_dir: str, *, dry_run: bool = False, verbosity: int = 1) -> tuple[int, int]:
    deleted_paths: set[str] = set()
    delete_count_total = 0
    parse_count_total = 0
    start_time = time()

    for dir_name, _, file_list in os.walk(src_dir):
        for file_name in file_list:
            file_src_path = os.path.join(dir_name, file_name)
            if file_src_path in deleted_paths or file_name[0] == '.':
                continue

            ext = '.' + str(file_name.split('.').pop()).lower()
            if ext == '.aae' or not is_media_extension(ext):
                continue

            parse_count_total += 1
            if verbosity > 0 and parse_count_total % 2500 == 0:
                print(f" === checked {parse_count_total} files === ")

            time_prefix = file_name[:15]
            duplicate_name: str | None = None
            source_hash: str | None = None
            for candidate in os.listdir(dir_name):
                candidate_path = os.path.join(dir_name, candidate)
                if (
                    not candidate.startswith(time_prefix)
                    or candidate.endswith(HASH_SUFFIX)
                    or candidate == file_name
                    or candidate_path in deleted_paths
                    or not os.path.isfile(candidate_path)
                ):
                    continue

                try:
                    src_stat = os.stat(file_src_path)
                    candidate_stat = os.stat(candidate_path)
                except FileNotFoundError:
                    if verbosity > 0:
                        print(f"[error] {file_name}")
                    continue
                if src_stat.st_size != candidate_stat.st_size:
                    continue
                if src_stat.st_mtime != candidate_stat.st_mtime:
                    continue
                if source_hash is None:
                    source_hash = get_or_create_file_hash(file_src_path, persist=not dry_run)
                candidate_hash = get_or_create_file_hash(candidate_path, persist=not dry_run)
                if source_hash != candidate_hash:
                    continue

                duplicate_name = candidate
                break

            if duplicate_name is None:
                continue

            file_remove = duplicate_name
            file_keep = file_name
            if len(file_remove) < len(file_keep):
                file_remove, file_keep = file_keep, file_remove

            remove_path = os.path.join(dir_name, file_remove)
            deleted_paths.add(remove_path)

            if verbosity > 0:
                print(f"[deleting]\t{file_remove}", end='\r')
            if not dry_run:
                os.remove(remove_path)
                remove_hash_sidecar(remove_path)
            if verbosity > 0:
                print(f"[finished]\t{file_remove}\t(original: {file_keep})")

            delete_count_total += 1

    elapsed_minutes = (time() - start_time) / 60
    if verbosity > 0:
        print(
            "checked {} \t deleted: {} \t took {:.2f} minutes".format(
                parse_count_total,
                delete_count_total,
                elapsed_minutes,
            )
        )

    return parse_count_total, delete_count_total


def main(args: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if len(args) < 1:
        print("missing arguments, usage: python dedupe.py [src]")
        return 1

    src_dir = args[0]
    if not os.path.isdir(src_dir):
        print("source directory does not exist")
        return 1

    deduplicate(src_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())

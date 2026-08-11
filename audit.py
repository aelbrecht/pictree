import argparse
import json
import os
import stat
import string
import sys
from collections import Counter
from dataclasses import dataclass, field
from time import time
from typing import Sequence

from util.functions import is_media_extension
from util.hashes import (
    COPY_TEMP_PREFIX,
    COPY_TEMP_SUFFIX,
    HASH_LENGTH,
    HASH_SUFFIX,
    SIDECAR_TEMP_PREFIX,
    SIDECAR_TEMP_SUFFIX,
    FileChangedError,
    HashSidecar,
    calculate_file_hash,
)

DEFAULT_STALE_TEMP_SECONDS = 24 * 60 * 60
DEFAULT_MAX_EXAMPLES = 20
MAX_SIDECAR_LINE_LENGTH = 64 * 1024


@dataclass(frozen=True)
class AuditFinding:
    kind: str
    path: str
    detail: str
    is_scan_error: bool = False


@dataclass
class AuditReport:
    media_files: int = 0
    sidecar_files: int = 0
    valid_sidecars: int = 0
    legacy_sidecars: int = 0
    missing_sidecars: int = 0
    temporary_files: int = 0
    verified_hashes: int = 0
    finding_counts: Counter[str] = field(default_factory=Counter)
    examples: list[AuditFinding] = field(default_factory=list)
    scan_errors: int = 0
    max_examples: int = DEFAULT_MAX_EXAMPLES

    def add_finding(
        self,
        kind: str,
        path: str,
        detail: str,
        *,
        is_scan_error: bool = False,
    ) -> None:
        self.finding_counts[kind] += 1
        if is_scan_error:
            self.scan_errors += 1
        if len(self.examples) < self.max_examples:
            self.examples.append(
                AuditFinding(
                    kind=kind,
                    path=path,
                    detail=detail,
                    is_scan_error=is_scan_error,
                )
            )

    @property
    def finding_count(self) -> int:
        return sum(self.finding_counts.values())

    @property
    def exit_code(self) -> int:
        if self.scan_errors:
            return 2
        if self.finding_count:
            return 1
        return 0


@dataclass(frozen=True)
class ParsedSidecar:
    digest: str
    record: HashSidecar | None


def _is_valid_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_sidecar(sidecar_path: str) -> ParsedSidecar:
    with open(sidecar_path, encoding='utf-8') as sidecar:
        digest_line = sidecar.readline(MAX_SIDECAR_LINE_LENGTH + 1)
        metadata_line = sidecar.readline(MAX_SIDECAR_LINE_LENGTH + 1)
        trailing_content = sidecar.read(1)

    if len(digest_line) > MAX_SIDECAR_LINE_LENGTH or len(metadata_line) > MAX_SIDECAR_LINE_LENGTH:
        raise ValueError('sidecar line is unreasonably long')
    if trailing_content:
        raise ValueError('unexpected trailing sidecar content')

    parts = digest_line.split()
    digest = parts[0].lower() if parts else ''
    if len(digest) != HASH_LENGTH or any(character not in string.hexdigits for character in digest):
        raise ValueError('invalid SHA-1 digest')

    if not metadata_line:
        return ParsedSidecar(digest=digest, record=None)
    if not metadata_line.startswith('# pictree '):
        raise ValueError('unrecognized sidecar metadata')

    try:
        metadata = json.loads(metadata_line.removeprefix('# pictree '))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError('invalid pictree metadata') from error
    if not isinstance(metadata, dict):
        raise ValueError('pictree metadata must be an object')

    file_size = metadata.get('file_size')
    file_mtime_ns = metadata.get('file_mtime_ns')
    if not _is_valid_integer(file_size) or file_size < 0:
        raise ValueError('invalid file_size metadata')
    if not _is_valid_integer(file_mtime_ns):
        raise ValueError('invalid file_mtime_ns metadata')

    source_path = metadata.get('source_path')
    source_size = metadata.get('source_size')
    source_mtime_ns = metadata.get('source_mtime_ns')
    if source_path is not None and not isinstance(source_path, str):
        raise ValueError('invalid source_path metadata')
    if source_size is not None and (not _is_valid_integer(source_size) or source_size < 0):
        raise ValueError('invalid source_size metadata')
    if source_mtime_ns is not None and not _is_valid_integer(source_mtime_ns):
        raise ValueError('invalid source_mtime_ns metadata')

    return ParsedSidecar(
        digest=digest,
        record=HashSidecar(
            digest=digest,
            file_size=file_size,
            file_mtime_ns=file_mtime_ns,
            source_path=source_path,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
        ),
    )


def _media_extension(file_name: str) -> str:
    return '.' + file_name.rsplit('.', 1)[-1].lower()


def audit_archive(
    destination_dir: str,
    *,
    verify_hashes: bool = False,
    stale_temp_seconds: float = DEFAULT_STALE_TEMP_SECONDS,
    max_examples: int = DEFAULT_MAX_EXAMPLES,
) -> AuditReport:
    if stale_temp_seconds < 0:
        raise ValueError('stale_temp_seconds must not be negative')
    if max_examples < 0:
        raise ValueError('max_examples must not be negative')
    if os.path.islink(destination_dir) or not os.path.isdir(destination_dir):
        raise ValueError(f'archive destination is not a directory: {destination_dir}')

    report = AuditReport(max_examples=max_examples)
    scan_time = time()

    def record_scan_error(error: OSError) -> None:
        report.add_finding(
            'scan_error',
            error.filename or destination_dir,
            str(error),
            is_scan_error=True,
        )

    for dir_name, dir_names, file_names in os.walk(
        destination_dir,
        topdown=True,
        onerror=record_scan_error,
        followlinks=False,
    ):
        for child_name in tuple(dir_names):
            child_path = os.path.join(dir_name, child_name)
            try:
                child_stat = os.lstat(child_path)
            except OSError as error:
                record_scan_error(error)
                dir_names.remove(child_name)
                continue
            if stat.S_ISLNK(child_stat.st_mode):
                report.add_finding('symlink', child_path, 'directory symlink was not followed')
                dir_names.remove(child_name)

        for file_name in file_names:
            file_path = os.path.join(dir_name, file_name)
            try:
                file_stat = os.lstat(file_path)
            except OSError as error:
                record_scan_error(error)
                continue

            if stat.S_ISLNK(file_stat.st_mode):
                report.add_finding('symlink', file_path, 'file symlink was not inspected')
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                continue

            is_copy_temp = file_name.startswith(COPY_TEMP_PREFIX) and file_name.endswith(
                COPY_TEMP_SUFFIX
            )
            is_sidecar_temp = file_name.startswith(SIDECAR_TEMP_PREFIX) and file_name.endswith(
                SIDECAR_TEMP_SUFFIX
            )
            if is_copy_temp or is_sidecar_temp:
                report.temporary_files += 1
                if scan_time - file_stat.st_mtime >= stale_temp_seconds:
                    report.add_finding(
                        'stale_temporary_file',
                        file_path,
                        'an interrupted pictree operation may have left this hidden file',
                    )
                continue

            if file_name.endswith(HASH_SUFFIX):
                report.sidecar_files += 1
                target_path = file_path.removesuffix(HASH_SUFFIX)
                try:
                    target_stat = os.lstat(target_path)
                except FileNotFoundError:
                    report.add_finding(
                        'orphan_sidecar',
                        file_path,
                        'the referenced media file does not exist',
                    )
                    continue
                except OSError as error:
                    record_scan_error(error)
                    continue
                if not stat.S_ISREG(target_stat.st_mode):
                    report.add_finding(
                        'orphan_sidecar',
                        file_path,
                        'the referenced path is not a regular file',
                    )
                    continue

                try:
                    parsed_sidecar = _parse_sidecar(file_path)
                except OSError as error:
                    record_scan_error(error)
                    continue
                except (UnicodeError, ValueError) as error:
                    report.add_finding('invalid_sidecar', file_path, str(error))
                    continue

                if parsed_sidecar.record is None:
                    report.legacy_sidecars += 1
                elif parsed_sidecar.record.matches_file(target_stat):
                    report.valid_sidecars += 1
                else:
                    report.add_finding(
                        'stale_sidecar',
                        file_path,
                        'saved size or modification time does not match the media file',
                    )

                if verify_hashes:
                    try:
                        actual_digest = calculate_file_hash(
                            target_path,
                            expected_stat=os.stat(target_path),
                        )
                    except (OSError, FileChangedError) as error:
                        report.add_finding(
                            'verification_error',
                            target_path,
                            str(error),
                            is_scan_error=True,
                        )
                        continue
                    report.verified_hashes += 1
                    if actual_digest != parsed_sidecar.digest:
                        report.add_finding(
                            'checksum_mismatch',
                            target_path,
                            'media content does not match its SHA-1 sidecar',
                        )
                continue

            if file_name.startswith('.') or not is_media_extension(_media_extension(file_name)):
                continue

            report.media_files += 1
            if file_stat.st_size == 0:
                report.add_finding('zero_byte_media', file_path, 'media file is empty')

            sidecar_path = file_path + HASH_SUFFIX
            try:
                sidecar_stat = os.lstat(sidecar_path)
            except FileNotFoundError:
                report.missing_sidecars += 1
            except OSError as error:
                record_scan_error(error)
            else:
                if not stat.S_ISREG(sidecar_stat.st_mode):
                    report.missing_sidecars += 1

    return report


def _print_report(destination_dir: str, report: AuditReport, *, verify_hashes: bool) -> None:
    print(f'audited: {destination_dir}')
    print(f'media files: {report.media_files}')
    print(f'sidecars: {report.sidecar_files}')
    print(f'valid metadata sidecars: {report.valid_sidecars}')
    print(f'legacy media without sidecars: {report.missing_sidecars}')
    print(f'legacy digest-only sidecars: {report.legacy_sidecars}')
    print(f'temporary pictree files: {report.temporary_files}')
    if verify_hashes:
        print(f'content hashes verified: {report.verified_hashes}')
    print(f'findings: {report.finding_count}')

    for finding in report.examples:
        label = 'ERROR' if finding.is_scan_error else 'WARNING'
        print(f'[{label}] {finding.kind}: {finding.path} ({finding.detail})')
    omitted = report.finding_count - len(report.examples)
    if omitted > 0:
        print(f'... {omitted} additional findings omitted')


def main(args: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only integrity audit for a pictree archive')
    parser.add_argument('destination', help='archive destination directory')
    parser.add_argument(
        '--verify-hashes',
        action='store_true',
        help='read and hash destination media that have sidecars (potentially very slow)',
    )
    parser.add_argument(
        '--temp-older-than',
        type=float,
        default=24.0,
        metavar='HOURS',
        help='report pictree temporary files older than this many hours (default: 24)',
    )
    parsed_args = parser.parse_args(args)

    try:
        report = audit_archive(
            parsed_args.destination,
            verify_hashes=parsed_args.verify_hashes,
            stale_temp_seconds=parsed_args.temp_older_than * 60 * 60,
        )
    except ValueError as error:
        print(error)
        return 2

    _print_report(parsed_args.destination, report, verify_hashes=parsed_args.verify_hashes)
    return report.exit_code


if __name__ == '__main__':
    sys.exit(main())

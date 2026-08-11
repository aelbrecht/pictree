import hashlib
import json
import os
import stat
import string
import tempfile
from dataclasses import dataclass
from shutil import copystat

HASH_NAME = 'sha1'
HASH_SUFFIX = '.sha1'
HASH_LENGTH = 40
CHUNK_SIZE = 8 * 1024 * 1024
COPY_TEMP_PREFIX = '.pictree-copy-'
COPY_TEMP_SUFFIX = '.tmp'
SIDECAR_TEMP_PREFIX = '.pictree-sidecar-'
SIDECAR_TEMP_SUFFIX = '.tmp'


class FileChangedError(RuntimeError):
    """Raised when a file changes while pictree is reading it."""


class DestinationChangedError(RuntimeError):
    """Raised rather than overwriting a destination that changed unexpectedly."""


@dataclass(frozen=True)
class AtomicCopyResult:
    digest: str
    source_stat: os.stat_result
    destination_stat: os.stat_result


@dataclass(frozen=True)
class HashSidecar:
    digest: str
    file_size: int | None = None
    file_mtime_ns: int | None = None
    source_path: str | None = None
    source_size: int | None = None
    source_mtime_ns: int | None = None

    def matches_file(self, file_stat: os.stat_result) -> bool:
        return self.file_size == file_stat.st_size and self.file_mtime_ns == file_stat.st_mtime_ns

    def matches_source(self, source_path: str, source_stat: os.stat_result) -> bool:
        return (
            self.source_path == os.path.abspath(source_path)
            and self.source_size == source_stat.st_size
            and self.source_mtime_ns == source_stat.st_mtime_ns
        )


def hash_sidecar_path(file_path: str) -> str:
    return file_path + HASH_SUFFIX


def new_hasher():
    return hashlib.sha1(usedforsecurity=False)


def file_stats_match(expected: os.stat_result, actual: os.stat_result) -> bool:
    return (
        expected.st_dev == actual.st_dev
        and expected.st_ino == actual.st_ino
        and expected.st_size == actual.st_size
        and expected.st_mtime_ns == actual.st_mtime_ns
    )


def require_file_unchanged(file_path: str, expected_stat: os.stat_result) -> None:
    try:
        actual_stat = os.stat(file_path)
    except FileNotFoundError as error:
        raise FileChangedError(f'file disappeared while being processed: {file_path}') from error
    if not file_stats_match(expected_stat, actual_stat):
        raise FileChangedError(f'file changed while being processed: {file_path}')


def calculate_file_hash(
    file_path: str,
    *,
    expected_stat: os.stat_result | None = None,
) -> str:
    digest = new_hasher()
    with open(file_path, 'rb') as source:
        opened_stat = os.fstat(source.fileno())
        if expected_stat is not None and not file_stats_match(expected_stat, opened_stat):
            raise FileChangedError(f'file changed before hashing: {file_path}')
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
        if not file_stats_match(opened_stat, os.fstat(source.fileno())):
            raise FileChangedError(f'file changed while hashing: {file_path}')
    require_file_unchanged(file_path, opened_stat)
    return digest.hexdigest()


def _destination_stat(file_path: str) -> os.stat_result | None:
    try:
        return os.lstat(file_path)
    except FileNotFoundError:
        return None


def _require_destination_unchanged(
    destination_path: str,
    expected_stat: os.stat_result | None,
) -> None:
    actual_stat = _destination_stat(destination_path)
    if expected_stat is None:
        if actual_stat is not None:
            raise DestinationChangedError(
                f'destination appeared while copying; refusing to overwrite it: {destination_path}'
            )
        return
    if actual_stat is None or not file_stats_match(expected_stat, actual_stat):
        raise DestinationChangedError(
            f'destination changed while copying; refusing to overwrite it: {destination_path}'
        )


def copy_file_atomically_with_hash(
    source_path: str,
    destination_path: str,
    *,
    expected_source_stat: os.stat_result | None = None,
) -> AtomicCopyResult:
    initial_destination_stat = _destination_stat(destination_path)
    if initial_destination_stat is not None and (
        not stat.S_ISREG(initial_destination_stat.st_mode)
        or initial_destination_stat.st_size != 0
    ):
        raise DestinationChangedError(
            f'refusing to overwrite existing destination: {destination_path}'
        )

    digest = new_hasher()
    destination_dir = os.path.dirname(destination_path) or '.'

    with open(source_path, 'rb') as source:
        opened_source_stat = os.fstat(source.fileno())
        if expected_source_stat is not None and not file_stats_match(
            expected_source_stat,
            opened_source_stat,
        ):
            raise FileChangedError(f'source changed before copying: {source_path}')

        file_descriptor, temp_path = tempfile.mkstemp(
            dir=destination_dir,
            prefix=COPY_TEMP_PREFIX,
            suffix=COPY_TEMP_SUFFIX,
        )
        try:
            bytes_copied = 0
            with os.fdopen(file_descriptor, 'wb') as destination:
                while chunk := source.read(CHUNK_SIZE):
                    written = destination.write(chunk)
                    if written != len(chunk):
                        raise OSError(
                            f'short write while copying {source_path} to {destination_path}'
                        )
                    bytes_copied += written
                    digest.update(chunk)

            if not file_stats_match(opened_source_stat, os.fstat(source.fileno())):
                raise FileChangedError(f'source changed while copying: {source_path}')
            require_file_unchanged(source_path, opened_source_stat)

            temp_stat = os.stat(temp_path)
            if bytes_copied != opened_source_stat.st_size or temp_stat.st_size != bytes_copied:
                raise OSError(f'incomplete temporary copy for: {source_path}')

            copystat(source_path, temp_path)
            require_file_unchanged(source_path, opened_source_stat)
            committed_temp_stat = os.stat(temp_path)
            if committed_temp_stat.st_size != opened_source_stat.st_size:
                raise OSError(f'temporary copy size does not match source: {source_path}')
            _require_destination_unchanged(destination_path, initial_destination_stat)
            copied_digest = digest.hexdigest()
            _write_hash_sidecar_for_stat(
                destination_path,
                temp_path,
                copied_digest,
                committed_temp_stat,
                source_path=source_path,
                source_stat=opened_source_stat,
            )
            require_file_unchanged(source_path, opened_source_stat)
            _require_destination_unchanged(destination_path, initial_destination_stat)
            os.replace(temp_path, destination_path)
            committed_destination_stat = os.stat(destination_path)
            if (
                committed_destination_stat.st_size != committed_temp_stat.st_size
                or committed_destination_stat.st_mtime_ns != committed_temp_stat.st_mtime_ns
            ):
                raise DestinationChangedError(
                    f'destination changed immediately after copying: {destination_path}'
                )
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    return AtomicCopyResult(
        digest=copied_digest,
        source_stat=opened_source_stat,
        destination_stat=committed_destination_stat,
    )


def read_hash_sidecar_record(file_path: str) -> HashSidecar | None:
    try:
        with open(hash_sidecar_path(file_path), encoding='utf-8') as sidecar:
            lines = sidecar.readlines()
    except (OSError, UnicodeError):
        return None

    parts = lines[0].split() if lines else []
    if not parts:
        return None
    digest = parts[0].lower()
    if len(digest) != HASH_LENGTH or any(character not in string.hexdigits for character in digest):
        return None

    if len(lines) < 2 or not lines[1].startswith('# pictree '):
        return HashSidecar(digest=digest)
    try:
        metadata = json.loads(lines[1].removeprefix('# pictree '))
        return HashSidecar(
            digest=digest,
            file_size=metadata['file_size'],
            file_mtime_ns=metadata['file_mtime_ns'],
            source_path=metadata.get('source_path'),
            source_size=metadata.get('source_size'),
            source_mtime_ns=metadata.get('source_mtime_ns'),
        )
    except (KeyError, TypeError, ValueError):
        return HashSidecar(digest=digest)


def read_hash_sidecar(file_path: str) -> str | None:
    record = read_hash_sidecar_record(file_path)
    return record.digest if record is not None else None


def _write_hash_sidecar_for_stat(
    sidecar_file_path: str,
    metadata_file_path: str,
    digest: str,
    file_stat: os.stat_result,
    *,
    source_path: str | None = None,
    source_stat: os.stat_result | None = None,
) -> None:
    sidecar_path = hash_sidecar_path(sidecar_file_path)
    sidecar_dir = os.path.dirname(sidecar_path) or '.'
    file_descriptor, temp_path = tempfile.mkstemp(
        dir=sidecar_dir,
        prefix=SIDECAR_TEMP_PREFIX,
        suffix=SIDECAR_TEMP_SUFFIX,
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, 'w', encoding='utf-8') as sidecar:
            sidecar.write(f'{digest}  {os.path.basename(sidecar_file_path)}\n')
            metadata = {
                'file_size': file_stat.st_size,
                'file_mtime_ns': file_stat.st_mtime_ns,
            }
            if source_path is not None:
                if source_stat is None:
                    source_stat = os.stat(source_path)
                else:
                    require_file_unchanged(source_path, source_stat)
                metadata.update(
                    {
                        'source_path': os.path.abspath(source_path),
                        'source_size': source_stat.st_size,
                        'source_mtime_ns': source_stat.st_mtime_ns,
                    }
                )
            sidecar.write(f'# pictree {json.dumps(metadata, separators=(",", ":"))}\n')
        require_file_unchanged(metadata_file_path, file_stat)
        if source_path is not None and source_stat is not None:
            require_file_unchanged(source_path, source_stat)
        os.replace(temp_path, sidecar_path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def write_hash_sidecar(
    file_path: str,
    digest: str,
    *,
    source_path: str | None = None,
    source_stat: os.stat_result | None = None,
    expected_file_stat: os.stat_result | None = None,
) -> None:
    file_stat = os.stat(file_path)
    if expected_file_stat is not None and not file_stats_match(expected_file_stat, file_stat):
        raise FileChangedError(f'file changed before writing sidecar: {file_path}')
    _write_hash_sidecar_for_stat(
        file_path,
        file_path,
        digest,
        file_stat,
        source_path=source_path,
        source_stat=source_stat,
    )


def get_or_create_file_hash(file_path: str, *, persist: bool = True) -> str:
    file_stat = os.stat(file_path)
    record = read_hash_sidecar_record(file_path)
    if record is not None and record.matches_file(file_stat):
        return record.digest

    digest = calculate_file_hash(file_path, expected_stat=file_stat)
    if persist:
        write_hash_sidecar(
            file_path,
            digest,
            source_path=file_path,
            source_stat=file_stat,
            expected_file_stat=file_stat,
        )
    return digest


def remove_hash_sidecar(file_path: str) -> None:
    try:
        os.remove(hash_sidecar_path(file_path))
    except FileNotFoundError:
        pass

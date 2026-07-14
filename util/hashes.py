import hashlib
import json
import os
import string
import tempfile
from dataclasses import dataclass

HASH_NAME = 'sha1'
HASH_SUFFIX = '.sha1'
HASH_LENGTH = 40
CHUNK_SIZE = 8 * 1024 * 1024


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


def calculate_file_hash(file_path: str) -> str:
    digest = new_hasher()
    with open(file_path, 'rb') as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file_with_hash(source_path: str, destination_path: str) -> str:
    digest = new_hasher()
    with open(source_path, 'rb') as source, open(destination_path, 'wb') as destination:
        while chunk := source.read(CHUNK_SIZE):
            destination.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


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


def write_hash_sidecar(file_path: str, digest: str, *, source_path: str | None = None) -> None:
    sidecar_path = hash_sidecar_path(file_path)
    sidecar_dir = os.path.dirname(sidecar_path) or '.'
    file_descriptor, temp_path = tempfile.mkstemp(
        dir=sidecar_dir,
        prefix=f'.{os.path.basename(sidecar_path)}.',
        suffix='.tmp',
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, 'w', encoding='utf-8') as sidecar:
            sidecar.write(f'{digest}  {os.path.basename(file_path)}\n')
            file_stat = os.stat(file_path)
            metadata = {
                'file_size': file_stat.st_size,
                'file_mtime_ns': file_stat.st_mtime_ns,
            }
            if source_path is not None:
                source_stat = os.stat(source_path)
                metadata.update(
                    {
                        'source_path': os.path.abspath(source_path),
                        'source_size': source_stat.st_size,
                        'source_mtime_ns': source_stat.st_mtime_ns,
                    }
                )
            sidecar.write(f'# pictree {json.dumps(metadata, separators=(",", ":"))}\n')
        os.replace(temp_path, sidecar_path)
    except BaseException:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise


def get_or_create_file_hash(file_path: str, *, persist: bool = True) -> str:
    record = read_hash_sidecar_record(file_path)
    if record is not None and record.matches_file(os.stat(file_path)):
        return record.digest

    digest = calculate_file_hash(file_path)
    if persist:
        write_hash_sidecar(file_path, digest, source_path=file_path)
    return digest


def remove_hash_sidecar(file_path: str) -> None:
    try:
        os.remove(hash_sidecar_path(file_path))
    except FileNotFoundError:
        pass

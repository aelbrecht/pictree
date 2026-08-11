import fcntl
import os
from contextlib import contextmanager
from collections.abc import Iterator

LOCK_FILE_NAME = '.pictree-sync.lock'


class DestinationLockedError(RuntimeError):
    """Raised when another pictree sync already holds the destination lock."""


@contextmanager
def destination_lock(destination_dir: str) -> Iterator[None]:
    lock_path = os.path.join(destination_dir, LOCK_FILE_NAME)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW

    try:
        file_descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise DestinationLockedError(
            f'could not open destination lock; refusing to sync: {lock_path}'
        ) from error
    with os.fdopen(file_descriptor, 'rb', closefd=True) as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DestinationLockedError(
                f'another pictree sync is already using destination: {destination_dir}'
            ) from error
        except OSError as error:
            raise DestinationLockedError(
                f'destination filesystem does not provide a usable lock: {lock_path}'
            ) from error

        yield

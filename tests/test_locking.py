import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from util.locking import DestinationLockedError, destination_lock


class DestinationLockTests(unittest.TestCase):
    def test_rejects_overlapping_destination_locks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir)

            with destination_lock(str(destination)):
                with self.assertRaises(DestinationLockedError):
                    with destination_lock(str(destination)):
                        self.fail('overlapping lock was unexpectedly acquired')

    def test_releases_lock_when_sync_scope_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir)

            with self.assertRaisesRegex(RuntimeError, 'copy failed'):
                with destination_lock(str(destination)):
                    raise RuntimeError('copy failed')

            with destination_lock(str(destination)):
                pass

    def test_fails_closed_when_filesystem_locking_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch('util.locking.fcntl.flock', side_effect=OSError('not supported')),
                self.assertRaises(DestinationLockedError),
            ):
                with destination_lock(temp_dir):
                    self.fail('unsupported lock was unexpectedly ignored')


if __name__ == '__main__':
    unittest.main()

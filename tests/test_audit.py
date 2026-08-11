import os
import tempfile
import unittest
from builtins import open as builtin_open
from pathlib import Path
from time import time
from unittest.mock import patch

import audit
from util import hashes


class AuditArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.archive = Path(self.temp_dir.name)

    def create_media(self, name: str = 'photo.jpg', contents: bytes = b'image') -> Path:
        media = self.archive / name
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(contents)
        return media

    def test_default_audit_accepts_valid_sidecar_without_hashing_media(self) -> None:
        media = self.create_media()
        digest = hashes.calculate_file_hash(str(media))
        hashes.write_hash_sidecar(str(media), digest, source_path=str(media))

        opened_paths: list[str] = []

        def track_open(file, *args, **kwargs):
            opened_paths.append(os.fspath(file))
            return builtin_open(file, *args, **kwargs)

        with (
            patch('audit.calculate_file_hash') as calculate_hash,
            patch('builtins.open', side_effect=track_open),
        ):
            report = audit.audit_archive(str(self.archive))

        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.media_files, 1)
        self.assertEqual(report.valid_sidecars, 1)
        self.assertEqual(report.missing_sidecars, 0)
        calculate_hash.assert_not_called()
        self.assertEqual(opened_paths, [str(media) + hashes.HASH_SUFFIX])

    def test_missing_legacy_sidecar_is_informational(self) -> None:
        self.create_media()

        report = audit.audit_archive(str(self.archive))

        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.missing_sidecars, 1)
        self.assertEqual(report.finding_count, 0)

    def test_reports_zero_byte_orphan_invalid_and_stale_files(self) -> None:
        zero_byte = self.create_media('zero.jpg', b'')
        stale = self.create_media('stale.jpg', b'original')
        hashes.write_hash_sidecar(str(stale), 'a' * 40, source_path=str(stale))
        stale.write_bytes(b'changed contents')
        Path(str(self.archive / 'missing.jpg') + hashes.HASH_SUFFIX).write_text(
            f"{'a' * 40}  missing.jpg\n",
            encoding='utf-8',
        )
        invalid = Path(str(self.create_media('invalid.jpg')) + hashes.HASH_SUFFIX)
        invalid.write_text('not a hash\n', encoding='utf-8')

        report = audit.audit_archive(str(self.archive))

        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.finding_counts['zero_byte_media'], 1)
        self.assertEqual(report.finding_counts['orphan_sidecar'], 1)
        self.assertEqual(report.finding_counts['invalid_sidecar'], 1)
        self.assertEqual(report.finding_counts['stale_sidecar'], 1)
        self.assertTrue(zero_byte.exists())

    def test_reports_only_old_owned_temporary_files_as_stale(self) -> None:
        recent = self.archive / f'{hashes.COPY_TEMP_PREFIX}recent{hashes.COPY_TEMP_SUFFIX}'
        old = self.archive / f'{hashes.COPY_TEMP_PREFIX}old{hashes.COPY_TEMP_SUFFIX}'
        old_sidecar = (
            self.archive / f'{hashes.SIDECAR_TEMP_PREFIX}old{hashes.SIDECAR_TEMP_SUFFIX}'
        )
        unrelated = self.archive / '.other-tool.tmp'
        for path in (recent, old, old_sidecar, unrelated):
            path.write_bytes(b'partial')
        os.utime(old, (time() - 7200, time() - 7200))
        os.utime(old_sidecar, (time() - 7200, time() - 7200))

        report = audit.audit_archive(str(self.archive), stale_temp_seconds=3600)

        self.assertEqual(report.temporary_files, 3)
        self.assertEqual(report.finding_counts['stale_temporary_file'], 2)
        self.assertEqual(
            {finding.path for finding in report.examples},
            {str(old), str(old_sidecar)},
        )

    def test_rejects_unexpected_trailing_sidecar_content(self) -> None:
        media = self.create_media()
        hashes.write_hash_sidecar(str(media), hashes.calculate_file_hash(str(media)))
        sidecar = Path(str(media) + hashes.HASH_SUFFIX)
        with sidecar.open('a', encoding='utf-8') as sidecar_file:
            sidecar_file.write('unexpected\n')

        report = audit.audit_archive(str(self.archive))

        self.assertEqual(report.finding_counts['invalid_sidecar'], 1)

    def test_optional_hash_verification_reports_content_mismatch(self) -> None:
        media = self.create_media(contents=b'image')
        hashes.write_hash_sidecar(str(media), 'a' * 40, source_path=str(media))

        report = audit.audit_archive(str(self.archive), verify_hashes=True)

        self.assertEqual(report.verified_hashes, 1)
        self.assertEqual(report.finding_counts['checksum_mismatch'], 1)
        self.assertEqual(report.exit_code, 1)

    def test_unreadable_sidecar_marks_scan_incomplete(self) -> None:
        media = self.create_media()
        hashes.write_hash_sidecar(str(media), hashes.calculate_file_hash(str(media)))
        sidecar = Path(str(media) + hashes.HASH_SUFFIX)

        with patch(
            'audit._parse_sidecar',
            side_effect=PermissionError(13, 'permission denied', str(sidecar)),
        ):
            report = audit.audit_archive(str(self.archive))

        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.scan_errors, 1)
        self.assertEqual(report.finding_counts['scan_error'], 1)

    def test_symlinks_are_reported_without_being_followed(self) -> None:
        outside = Path(self.temp_dir.name).parent / f'{Path(self.temp_dir.name).name}-outside.jpg'
        outside.write_bytes(b'outside archive')
        self.addCleanup(outside.unlink)
        link = self.archive / 'linked.jpg'
        link.symlink_to(outside)

        with patch('audit.calculate_file_hash') as calculate_hash:
            report = audit.audit_archive(str(self.archive), verify_hashes=True)

        self.assertEqual(report.finding_counts['symlink'], 1)
        self.assertEqual(report.verified_hashes, 0)
        calculate_hash.assert_not_called()

    def test_audit_does_not_modify_archive_contents(self) -> None:
        media = self.create_media(contents=b'image')
        hashes.write_hash_sidecar(str(media), hashes.calculate_file_hash(str(media)))

        before = {
            path.relative_to(self.archive): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.archive.rglob('*')
            if path.is_file()
        }
        audit.audit_archive(str(self.archive))
        after = {
            path.relative_to(self.archive): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.archive.rglob('*')
            if path.is_file()
        }

        self.assertEqual(after, before)

    def test_examples_are_bounded(self) -> None:
        for index in range(5):
            self.create_media(f'empty-{index}.jpg', b'')

        report = audit.audit_archive(str(self.archive), max_examples=2)

        self.assertEqual(report.finding_count, 5)
        self.assertEqual(len(report.examples), 2)


if __name__ == '__main__':
    unittest.main()

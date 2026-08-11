# PicTree

Copies and organizes media files into directories labeled by their date of creation. Uses exif data if available. Skips
any existing pictures if they already exist.

## Usage

```
python main.py [src] [dst] [year]
```

The optional `year` argument skips media older than that year.

Source and destination must be separate directory trees; pictree rejects either one being nested inside the other to
prevent the archive from recursively copying itself.

Each copied destination file gets a `filename.ext.sha1` sidecar. The hash is calculated while the source is already being
copied, so it does not add another file read. Existing destination files without sidecars require one source-side hash to
seed the sidecar when size and timestamp still match. If a network filesystem changed a legacy destination timestamp,
Pictree verifies that source and destination once before seeding the sidecar. This migration is incremental across runs;
subsequent syncs reuse each receipt and do not reread that media over the network. The first migration run may therefore
read a large part of an existing archive, but it is safe to cancel and resume; completed receipts are retained.

Some network filesystems normalize modification times during the final rename. Pictree records the metadata exposed by
the final destination instead of treating that timestamp change as a failed copy. If an interruption leaves a prepared
receipt with the earlier timestamp, Pictree verifies only that destination once before trusting and refreshing its
receipt.

New media is first written to a hidden temporary file in the destination directory. Pictree only moves that file to its
final archive name after the complete copy, source metadata, and checksum receipt have been validated. A normal
interruption before that rename leaves the final name untouched. A forced shutdown may leave a hidden
`.pictree-copy-*.tmp` or `.pictree-sidecar-*.tmp` file, or an orphan checksum receipt; these are safely replaced or
ignored by the next sync and can be reported by the audit command below. Power-loss and server-failure durability
ultimately depend on the destination filesystem.

Pictree holds an advisory `.pictree-sync.lock` in the archive root while syncing or deduplicating. A second pictree
process fails safely instead of writing concurrently. Other programs must not modify the archive during a sync, and the
mounted destination filesystem must support advisory file locks.

When running `python dedupe.py /path/to/archive`, pass the same archive root used as the sync destination so both commands
coordinate through the same lock file.

## Read-only archive audit

```sh
python audit.py /path/to/archive
```

The default audit does not hash or modify media. It reports zero-byte files, invalid or stale sidecars, orphan sidecars,
symlinks, scan errors, and pictree temporary files older than 24 hours. Existing media without sidecars is counted as
legacy data rather than reported as damaged.

Run the audit while no sync or deduplication process is active so it does not report transient prepared files.

To read and verify the contents of destination files that already have sidecars:

```sh
python audit.py /path/to/archive --verify-hashes
```

Full verification can read a large part of the archive over the network and may take a long time. Even a clean metadata
audit cannot prove that legacy files without independently calculated destination hashes are complete.

The timestamp repair utilities under `util/` require Apple's `SetFile` command. On systems where it is unavailable,
they exit before modifying any files.

## Tests

```sh
python -m unittest discover -s tests -v
```

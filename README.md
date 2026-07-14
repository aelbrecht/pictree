# PicTree

Copies and organizes media files into directories labeled by their date of creation. Uses exif data if available. Skips
any existing pictures if they already exist.

## Usage

```
python main.py [src] [dst] [year]
```

The optional `year` argument skips media older than that year.

Each copied destination file gets a `filename.ext.sha1` sidecar. The hash is calculated while the source is already being
copied, so it does not add another file read. Existing destination files without sidecars require one source-side hash to
seed the sidecar; subsequent syncs reuse it and do not reread the media over the network.

The timestamp repair utilities under `util/` require Apple's `SetFile` command. On systems where it is unavailable,
they exit before modifying any files.

## Tests

```sh
python -m unittest discover -s tests -v
```

# PicTree

Copies and organizes media files into directories labeled by their date of creation. Uses exif data if available. Skips
any existing pictures if they already exist.

## Usage

```
python main.py [src] [dst] [year]
```

The optional `year` argument skips media older than that year.

The timestamp repair utilities under `util/` require Apple's `SetFile` command. On systems where it is unavailable,
they exit before modifying any files.

## Tests

```sh
python -m unittest discover -s tests -v
```

# PicTree

Copies and organizes media files into directories labeled by their date of creation. Uses exif data if available. Skips
any existing pictures if they already exist.

## Usage

```
python main.py [src] [dst] [year]
```

The optional `year` argument skips media older than that year.

## Tests

```sh
python -m unittest discover -s tests -v
```

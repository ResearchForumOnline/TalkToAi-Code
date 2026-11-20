"""Stage the original, checksum-pinned Kenney collection in the Windows payload."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen


SOURCE = ('https://github.com/ResearchForumOnline/TalkToAi-Code/releases/download/'
          'v8.7.0/Kenney-CC0-Game-Asset-Collection-8.7.0.zip')
DIGEST = '1dd27353c055a2db2b1ca709f5e25fdd6350532c7bca4721176cb56ce468d151'


def stage(payload, source):
    internal = payload / '_internal'
    if not internal.is_dir():
        raise ValueError('Build the Windows payload first.')
    directory = internal / 'assets'
    directory.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as work:
        archive = Path(work) / 'collection.zip'
        with (source.open('rb') if source else urlopen(SOURCE, timeout=90)) as src, archive.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        with archive.open('rb') as src:
            if hashlib.file_digest(src, 'sha256').hexdigest() != DIGEST:
                raise ValueError('Kenney collection checksum mismatch.')
        archive.replace(directory / 'kenney-cc0.zip')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    stage(args.payload, args.source)

"""Read single days out of the 16 GB GPS archive with HTTP range requests.

The October archive is non-solid, so each daily SQLite database is its own
compressed stream. We expose the remote file as a seekable file object and let
py7zr pull only the bytes it needs (the header at the end plus one stream).

    python scripts/remote7z.py --list
    python scripts/remote7z.py bus_movements_2026_10_15.db --out data/gps
"""
import argparse
import io
import sys
import time
from pathlib import Path

import requests

URL = ('https://pub-091bd9e794bb4877b10b29e6df80f065.r2.dev/2026-10/'
       'delhi-dtc-bus-gtfs-rt-2026-10.7z')
BLOCK = 16 * 1024 * 1024


class RangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file with a small block cache."""

    def __init__(self, url, block=BLOCK, cache_blocks=8):
        self.url, self.block, self.pos = url, block, 0
        self.session = requests.Session()
        self.size = int(self.session.head(url, timeout=60).headers['Content-Length'])
        self.cache, self.order, self.limit = {}, [], cache_blocks
        self.fetched = 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def _block(self, index):
        if index in self.cache:
            return self.cache[index]
        start = index * self.block
        end = min(start + self.block, self.size) - 1
        for attempt in range(8):
            try:
                r = self.session.get(self.url, headers={'Range': f'bytes={start}-{end}'}, timeout=120)
                r.raise_for_status()
                data = r.content
                if len(data) == end - start + 1:
                    break
            except requests.RequestException:
                pass
            time.sleep(2 ** attempt)
        else:
            raise IOError(f'range {start}-{end} failed')
        self.fetched += len(data)
        self.cache[index] = data
        self.order.append(index)
        if len(self.order) > self.limit:
            self.cache.pop(self.order.pop(0), None)
        return data

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.size:
            data = self._block(self.pos // self.block)
            off = self.pos % self.block
            piece = data[off:off + n]
            out += piece
            self.pos += len(piece)
            n -= len(piece)
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def main():
    import py7zr
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('names', nargs='*', help='archive members to extract')
    p.add_argument('--list', action='store_true')
    p.add_argument('--out', type=Path, default=Path('data/gps'))
    p.add_argument('--url', default=URL)
    a = p.parse_args()
    remote = RangeFile(a.url)
    with py7zr.SevenZipFile(remote, 'r') as z:
        if a.list:
            for info in z.list():
                print(info.filename, info.uncompressed, info.compressed)
            return
        a.out.mkdir(parents=True, exist_ok=True)
        for name in a.names:
            if (a.out / name).exists():
                print('skip', name)
                continue
            t = time.time()
            z.reset()
            z.extract(path=a.out, targets=[name])
            print(f'{name}: {(a.out / name).stat().st_size / 1e9:.2f} GB in {time.time() - t:.0f}s,'
                  f' downloaded {remote.fetched / 1e9:.2f} GB', file=sys.stderr)


if __name__ == '__main__':
    main()

"""Inspect official wheel ZIPs over HTTPS ranges; no full RTX cache download."""
import collections
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile


class RangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url = url; self.position = 0; self.cache = collections.OrderedDict()
        self.block = 2 * 1024 * 1024; self.downloaded = 0
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=-1'}), timeout=60) as r:
            if r.status != 206: raise OSError('HTTPS source does not support ranges')
            self.size = int(r.headers['Content-Range'].split('/')[-1]); self.etag = r.headers.get('ETag')

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.position
    def seek(self, offset, whence=0):
        self.position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        return self.position

    def read(self, size=-1):
        if size < 0: size = self.size - self.position
        size = min(size, self.size - self.position)
        result = bytearray()
        while size > 0:
            number = self.position // self.block
            if number not in self.cache:
                start = number * self.block; end = min(start + self.block, self.size) - 1
                for attempt in range(4):
                    try:
                        headers = {'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'}
                        if self.etag: headers['If-Match'] = self.etag
                        # Range is in the query too: avoid broken shared proxy caches.
                        url = self.url + ('&' if '?' in self.url else '?') + f'range={start}-{end}'
                        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as r:
                            if r.status != 206 or r.headers['Content-Range'] != f'bytes {start}-{end}/{self.size}':
                                raise OSError('Unexpected wheel byte range')
                            data = r.read()
                        if len(data) != end - start + 1: raise OSError('Short wheel byte range')
                        self.cache[number] = data; self.downloaded += len(data); break
                    except OSError:
                        if attempt == 3: raise
                        time.sleep(attempt + 1)
                while len(self.cache) > 12: self.cache.popitem(last=False)
            self.cache.move_to_end(number)
            data = self.cache[number]; offset = self.position % self.block
            take = min(size, len(data) - offset)
            result.extend(data[offset:offset+take]); self.position += take; size -= take
        return bytes(result)


if __name__ == '__main__':
    base = Path(__file__).resolve().parent
    for package in ['isaacsim-extscache-kit', 'isaacsim-extscache-kit-sdk']:
        filename = package.replace('-', '_') + '-6.2.0.0-cp312-none-manylinux_2_35_x86_64.whl'
        source = RangeFile('https://pypi.nvidia.com/' + package + '/' + filename)
        with zipfile.ZipFile(source) as archive:
            rows = [{'name': x.filename, 'size': x.file_size, 'compressed': x.compress_size} for x in archive.infolist()]
        (base / (package + '-files.json')).write_text(json.dumps(rows))
        groups = collections.Counter()
        for row in rows:
            parts = row['name'].split('/'); key = '/'.join(parts[:3])
            groups[key] += row['compressed']
        print(package, 'wheel bytes', source.size, 'metadata bytes', source.downloaded, flush=True)
        for name, size in groups.most_common(30): print(round(size/1024**2, 1), name, flush=True)

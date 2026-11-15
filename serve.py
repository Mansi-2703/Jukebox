# serve.py — Lightweight HTTP server with Range Request (206 Partial Content) support for video/audio streaming
import http.server
import os
import re
import sys

PORT = 8080
DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        if "Range" not in self.headers:
            self.range = None
            return super().send_head()

        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.range = None
            return super().send_head()

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        total_len = fs.st_size

        range_match = re.match(r"bytes=(\d+)-(\d*)", self.headers["Range"])
        if not range_match:
            f.close()
            self.range = None
            return super().send_head()

        first = int(range_match.group(1))
        last = int(range_match.group(2)) if range_match.group(2) else total_len - 1

        if first >= total_len:
            self.send_error(416, "Requested Range Not Satisfiable")
            f.close()
            return None

        last = min(last, total_len - 1)
        length = last - first + 1

        self.send_response(206)
        ctype = self.guess_type(path)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Range", f"bytes {first}-{last}/{total_len}")
        self.send_header("Content-Length", str(length))
        self.end_headers()

        f.seek(first)
        self.range = (first, length)
        return f

    def copyfile(self, source, outputfile):
        if not hasattr(self, "range") or not self.range:
            return super().copyfile(source, outputfile)

        first, length = self.range
        chunk_size = 64 * 1024
        bytes_left = length
        while bytes_left > 0:
            read_len = min(chunk_size, bytes_left)
            buf = source.read(read_len)
            if not buf:
                break
            try:
                outputfile.write(buf)
            except (ConnectionResetError, BrokenPipeError):
                break
            bytes_left -= len(buf)

if __name__ == "__main__":
    server_address = ("127.0.0.1", PORT)
    httpd = http.server.ThreadingHTTPServer(server_address, RangeHTTPRequestHandler)
    print(f"Serving {DIRECTORY} at http://127.0.0.1:{PORT} (with Range Request support)...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

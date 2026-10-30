"""RE Engine .pak reading for Dragon's Dogma 2, in pure Python (plus zstandard).

Ported from eigeen/ree-pak-rs (MIT) as of 2026-10-29, including its open PR #16: DD2's current paks use header
feature 0x68, whose bit 0x40 adds an entry-remap table between the entry table and the entry-table key. The
released ree-pak-cli 0.7.2 does not skip that table and reads garbage entries (tried 2026-10-29).

Layout: 16-byte header (KPKA, version 4.2, feature flags, file count) -> entry table (48 bytes/entry, XOR-encrypted
with a 128-byte RSA-wrapped key when feature & 0x08) -> [u32 if 0x10] [9 bytes if 0x04] [remaps if 0x40] -> key ->
chunk table if 0x20 (entries flagged OFFSET_IS_CHUNK_INDEX point into it; chunks are zstd or stored).
File names are not stored: entries are found by murmur3 hashes of the lower/upper-case UTF-16 path."""
import io
import pathlib
import struct
import zlib

from . import game

LIST = pathlib.Path("/mnt/d/DD2Modding/research/paklist/DD2_STM_Release.list")

# Entry-table key unwrap (pak.rs MODULUS/EXPONENT, little-endian).
_MOD = int.from_bytes(bytes([
    0x7D, 0x0B, 0xF8, 0xC1, 0x7C, 0x23, 0xFD, 0x3B, 0xD4, 0x75, 0x16, 0xD2, 0x33, 0x21, 0xD8, 0x10, 0x71, 0xF9, 0x7C,
    0xD1, 0x34, 0x93, 0xBA, 0x77, 0x26, 0xFC, 0xAB, 0x2C, 0xEE, 0xDA, 0xD9, 0x1C, 0x89, 0xE7, 0x29, 0x7B, 0xDD, 0x8A,
    0xAE, 0x50, 0x39, 0xB6, 0x01, 0x6D, 0x21, 0x89, 0x5D, 0xA5, 0xA1, 0x3E, 0xA2, 0xC0, 0x8C, 0x93, 0x13, 0x36, 0x65,
    0xEB, 0xE8, 0xDF, 0x06, 0x17, 0x67, 0x96, 0x06, 0x2B, 0xAC, 0x23, 0xED, 0x8C, 0xB7, 0x8B, 0x90, 0xAD, 0xEA, 0x71,
    0xC4, 0x40, 0x44, 0x9D, 0x1C, 0x7B, 0xBA, 0xC4, 0xB6, 0x2D, 0xD6, 0xD2, 0x4B, 0x62, 0xD6, 0x26, 0xFC, 0x74, 0x20,
    0x07, 0xEC, 0xE3, 0x59, 0x9A, 0xE6, 0xAF, 0xB9, 0xA8, 0x35, 0x8B, 0xE0, 0xE8, 0xD3, 0xCD, 0x45, 0x65, 0xB0, 0x91,
    0xC4, 0x95, 0x1B, 0xF3, 0x23, 0x1E, 0xC6, 0x71, 0xCF, 0x3E, 0x35, 0x2D, 0x6B, 0xE3, 0x00]), "little")
_EXP = 0x010001

# Per-resource encryption (resource.rs).
_RMOD = int.from_bytes(bytes([
    0x13, 0xD7, 0x9C, 0x89, 0x88, 0x91, 0x48, 0x10, 0xD7, 0xAA, 0x78, 0xAE, 0xF8, 0x59, 0xDF, 0x7D, 0x3C, 0x43, 0xA0,
    0xD0, 0xBB, 0x36, 0x77, 0xB5, 0xF0, 0x5C, 0x02, 0xAF, 0x65, 0xD8, 0x77, 0x03, 0x00]), "little")
_REXP = int.from_bytes(bytes([
    0xC0, 0xC2, 0x77, 0x1F, 0x5B, 0x34, 0x6A, 0x01, 0xC7, 0xD4, 0xD7, 0x85, 0x2E, 0x42, 0x2B, 0x3B, 0x16, 0x3A, 0x17,
    0x13, 0x16, 0xEA, 0x83, 0x30, 0x30, 0xDF, 0x3F, 0xF4, 0x25, 0x93, 0x20, 0x01, 0x00]), "little")

F_EXTRA_DATA, F_ENCRYPTED, F_EXTRA_U32, F_CHUNKS, F_REMAP = 0x04, 0x08, 0x10, 0x20, 0x40
CHUNK_INDEX = 1 << 24


def murmur3(data: bytes, seed: int = 0xFFFFFFFF) -> int:
    c1, c2, m = 0xCC9E2D51, 0x1B873593, 0xFFFFFFFF
    h = seed
    n = len(data) // 4 * 4
    for i in range(0, n, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & m
        k = ((k << 15) | (k >> 17)) & m
        k = (k * c2) & m
        h ^= k
        h = ((h << 13) | (h >> 19)) & m
        h = (h * 5 + 0xE6546B64) & m
    tail = data[n:]
    if tail:
        k = int.from_bytes(tail, "little")
        k = (k * c1) & m
        k = ((k << 15) | (k >> 17)) & m
        k = (k * c2) & m
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & m
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & m
    h ^= h >> 16
    return h


def path_hash(path: str) -> tuple[int, int]:
    """(lower, upper) hashes of a pak path, e.g. natives/stm/gui/ui01/common/job/uvs_job_01.uvs.8."""
    return (murmur3(path.lower().encode("utf-16-le")), murmur3(path.upper().encode("utf-16-le")))


def _unwrap_key(raw: bytes) -> bytes:
    return pow(int.from_bytes(raw, "little"), _EXP, _MOD).to_bytes(129, "little")


def _decrypt_table(data: bytes, raw_key: bytes) -> bytes:
    key = _unwrap_key(raw_key)
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ ((i + key[i % 32] * key[i % 29]) & 0xFF)
    return bytes(out)


def _decrypt_resource(data: bytes) -> bytes:
    size = struct.unpack_from("<Q", data, 0)[0]
    out = bytearray()
    for off in range(8, len(data) - 127, 128):
        key = int.from_bytes(data[off:off + 64], "little")
        val = int.from_bytes(data[off + 64:off + 128], "little")
        out += (val // pow(key, _REXP, _RMOD) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
    return bytes(out[:size])


class Pak:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        with open(self.path, "rb") as f:
            magic, major, minor, self.feature, count, _ = struct.unpack("<4sBBHII", f.read(16))
            if magic != b"KPKA" or (major, minor) not in ((4, 0), (4, 1), (4, 2)):
                raise ValueError(f"{self.path.name}: not a v4 RE pak ({magic!r} {major}.{minor})")
            table = f.read(48 * count)
            if self.feature & F_EXTRA_U32:
                f.read(4)
            if self.feature & F_EXTRA_DATA:
                f.read(9)
            if self.feature & F_REMAP:
                remaps = struct.unpack("<Q", f.read(8))[0]
                f.seek(16 * remaps, io.SEEK_CUR)
            if self.feature & F_ENCRYPTED:
                table = _decrypt_table(table, f.read(128))
            self.chunk_size, self.chunks = 0, []
            if self.feature & F_CHUNKS:
                self.chunk_size, n = struct.unpack("<II", f.read(8))
                raw = struct.unpack(f"<{2 * n}I", f.read(8 * n))
                high, prev = 0, raw[0] if n else 0
                for i in range(n):
                    low, meta = raw[2 * i], raw[2 * i + 1]
                    if low < prev:
                        high += 1 << 32
                    self.chunks.append((high | low, meta >> 10))
                    prev = low
        self.entries = {}
        for i in range(count):
            lo, up, off, csize, usize, attr, _ = struct.unpack_from("<IIQQQqQ", table, 48 * i)
            self.entries[(lo, up)] = (off, csize, usize, attr & 0xFFFFFFFFFFFFFFFF)

    def has(self, path: str) -> bool:
        return path_hash(path) in self.entries

    def read(self, path: str) -> bytes | None:
        e = self.entries.get(path_hash(path))
        if e is None:
            return None
        off, csize, usize, attr = e
        with open(self.path, "rb") as f:
            if attr & CHUNK_INDEX:
                import zstandard
                out, idx = bytearray(), off
                dz = zstandard.ZstdDecompressor()
                while len(out) < usize:
                    start, clen = self.chunks[idx]
                    f.seek(start)
                    blob = f.read(clen)
                    out += blob if clen == self.chunk_size else dz.decompress(blob, max_output_size=self.chunk_size)
                    idx += 1
                return bytes(out[:usize])
            f.seek(off)
            data = f.read(csize)
        if (attr >> 16) & 0xFF:
            data = _decrypt_resource(data)
        comp = attr & 0xF
        if comp == 1:
            return zlib.decompress(data, -15)
        if comp == 2:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(data, max_output_size=usize)
        return data


def game_paks() -> list[pathlib.Path]:
    """Base pak then patches in load order (later ones override earlier ones)."""
    def order(p):
        s = p.name.split("patch_")
        return int(s[1].split(".")[0]) if len(s) > 1 else -1
    return sorted(game.GAME.glob("re_chunk_000.pak*"), key=order)


_OPEN: dict = {}


def find(path: str) -> tuple[pathlib.Path, bytes] | None:
    """Newest copy of a game file across all paks: (pak, bytes), or None."""
    for p in reversed(game_paks()):
        if p not in _OPEN:
            _OPEN[p] = Pak(p)
        if _OPEN[p].has(path):
            return p, _OPEN[p].read(path)
    return None


def search_list(pattern: str, limit: int = 100) -> list[str]:
    import re
    rx = re.compile(pattern, re.I)
    out = []
    with open(LIST, encoding="utf-8", errors="replace") as f:
        for line in f:
            if rx.search(line):
                out.append(line.strip())
                if len(out) >= limit:
                    break
    return out


def write_pak(files: dict[str, bytes], out: pathlib.Path) -> pathlib.Path:
    """Plain pak (version 4.0, no feature flags: no encryption, no compression) holding `files` by path.
    Mod paks loaded as re_chunk_000.pak.patch_NNN.pak override the same paths in earlier paks."""
    items = sorted(files.items())
    data_off = 16 + 48 * len(items)
    table, blobs, off = bytearray(), bytearray(), data_off
    for path, blob in items:
        lo, up = path_hash(path)
        table += struct.pack("<IIQQQqQ", lo, up, off, len(blob), len(blob), 0, 0)
        blobs += blob
        off += len(blob)
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(struct.pack("<4sBBHII", b"KPKA", 4, 0, 0, len(items), 0) + table + blobs)
    return out

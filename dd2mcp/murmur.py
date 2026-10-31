"""MurmurHash3 x86_32. RSZ type hashes are murmur3(utf-8 type name, seed 0xFFFFFFFF) (verified 2026-10-31 on
app.SetHumanState = 0x1f460767)."""


def murmur3(data: bytes, seed: int = 0xFFFFFFFF) -> int:
    c1, c2, m = 0xCC9E2D51, 0x1B873593, 0xFFFFFFFF
    h, n = seed & m, len(data)
    for i in range(0, n - n % 4, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & m
        k = ((k << 15) | (k >> 17)) & m
        h ^= (k * c2) & m
        h = ((h << 13) | (h >> 19)) & m
        h = (h * 5 + 0xE6546B64) & m
    tail = data[n - n % 4:]
    if tail:
        k = int.from_bytes(tail, "little")
        k = (k * c1) & m
        k = ((k << 15) | (k >> 17)) & m
        h ^= (k * c2) & m
    h ^= n
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & m
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & m
    return h ^ (h >> 16)


def type_hash(name: str) -> int:
    return murmur3(name.encode("utf-8"))

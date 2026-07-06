"""Tests for metallama.app.gguf — GGUF header parsing and VRAM estimation."""
import struct
from pathlib import Path

from metallama.app.gguf import read_metadata, estimate_vram_gb

# GGUF value types
_U8, _I8, _U16, _I16, _U32, _I32, _F32, _BOOL, _STRING, _ARRAY, _U64, _I64, _F64 = range(13)


def _write_gguf(path: Path, kvs: dict[str, str | int | float]):
    """Write a minimal GGUF v3 file with scalar KVs (0 tensors)."""
    def _write_string(f, s):
        encoded = s.encode("utf-8")
        f.write(struct.pack("<Q", len(encoded)))
        f.write(encoded)

    def _write_kv(f, key, vtype, value):
        _write_string(f, key)
        f.write(struct.pack("<I", vtype))
        if isinstance(value, str):
            _write_string(f, value)
        elif isinstance(value, int):
            f.write(struct.pack("<q", value))  # I64
        elif isinstance(value, float):
            f.write(struct.pack("<d", value))  # F64

    with path.open("wb") as f:
        # Magic + version
        f.write(b"GGUF")
        f.write(struct.pack("<I", 3))  # version
        # tensor_count (8 bytes, skipped by reader via f.seek(8, 1))
        f.write(struct.pack("<Q", 0))
        # KV count
        f.write(struct.pack("<Q", len(kvs)))
        # Write KVs
        for key, value in kvs.items():
            if isinstance(value, str):
                _write_kv(f, key, _STRING, value)
            elif isinstance(value, int):
                _write_kv(f, key, _I64, value)
            elif isinstance(value, float):
                _write_kv(f, key, _F64, value)


def test_read_metadata_basic(tmp_path: Path):
    p = tmp_path / "test.gguf"
    _write_gguf(p, {
        "general.architecture": "llama",
        "general.name": "test-model",
        "llama.context_length": 4096,
    })
    meta = read_metadata(p)
    assert meta is not None
    assert meta["general.architecture"] == "llama"
    assert meta["general.name"] == "test-model"
    assert meta["llama.context_length"] == 4096


def test_read_metadata_non_gguf_returns_none(tmp_path: Path):
    p = tmp_path / "not.gguf"
    p.write_bytes(b"This is not a GGUF file at all")
    assert read_metadata(p) is None


def test_estimate_vram_gb_sane(tmp_path: Path):
    p = tmp_path / "model.gguf"
    _write_gguf(p, {
        "general.architecture": "llama",
        "llama.context_length": 8192,
        "llama.embedding_length": 4096,
        "llama.block_count": 32,
    })
    vram = estimate_vram_gb(p, context_tokens=4096)
    assert vram is not None
    assert vram["total_gb"] > 0
    assert "weights_gb" in vram
    assert "kv_cache_gb" in vram


def test_estimate_vram_gb_non_gguf_returns_none(tmp_path: Path):
    p = tmp_path / "bad.gguf"
    p.write_bytes(b"garbage data")
    # estimate_vram_gb only checks file size, not GGUF magic — returns a dict
    result = estimate_vram_gb(p, context_tokens=1024)
    assert result is not None
    assert "weights_gb" in result
    assert "kv_cache_gb" in result
    assert "total_gb" in result

"""Deterministic experimental segmentation; does not change the live index."""
import hashlib
from dataclasses import dataclass

CHUNK_VERSION = "chars-600-overlap-100-v1"
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    text: str
    digest: str


def split_knowledge(content: str) -> list[Chunk]:
    """Segment exact original text with fixed overlap and stable versioned hashes."""
    if not content:
        return []
    result = []
    start = 0
    while start < len(content):
        end = min(start + CHUNK_SIZE, len(content))
        text = content[start:end]
        digest = hashlib.sha256(f"{CHUNK_VERSION}\n{start}\n{end}\n{text}".encode()).hexdigest()
        result.append(Chunk(len(result), start, end, text, digest))
        if end == len(content):
            break
        start = end - CHUNK_OVERLAP
    return result

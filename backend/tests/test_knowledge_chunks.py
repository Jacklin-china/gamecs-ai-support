from app.knowledge_chunks import CHUNK_SIZE, CHUNK_OVERLAP, split_knowledge


def test_chunks_cover_original_with_stable_overlap_and_hashes():
    text = "游戏规则与说明" * 220
    chunks = split_knowledge(text)
    assert len(chunks) > 1
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    for chunk in chunks:
        assert chunk.text == text[chunk.start:chunk.end]
        assert len(chunk.text) <= CHUNK_SIZE
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.end - current.start == CHUNK_OVERLAP
    assert chunks == split_knowledge(text)
    assert split_knowledge(text + "改")[0].digest == chunks[0].digest
    assert split_knowledge("") == []

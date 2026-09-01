class TextChunker:
    """Deterministic word-window chunking with a small overlap."""

    def __init__(self, *, chunk_size_words: int, overlap_words: int) -> None:
        if chunk_size_words < 20 or not 0 <= overlap_words < chunk_size_words:
            raise ValueError("Invalid chunking configuration")
        self._chunk_size_words = chunk_size_words
        self._overlap_words = overlap_words

    @property
    def chunk_size_words(self) -> int:
        return self._chunk_size_words

    @property
    def overlap_words(self) -> int:
        return self._overlap_words

    def chunk(self, content: str) -> list[str]:
        words = content.split()
        if not words:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = min(start + self._chunk_size_words, len(words))
            chunks.append(" ".join(words[start:end]))
            if end == len(words):
                break
            start = end - self._overlap_words
        return chunks

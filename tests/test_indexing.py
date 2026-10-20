import unittest
from pathlib import Path
from unittest.mock import patch

import src.indexing as idx
from src import config
from src.tools import collecting_info


class TestVectorStoreIndexing(unittest.TestCase):
    def test_paths_configured(self):
        self.assertTrue(isinstance(config.INDEX_PATH, Path))
        self.assertTrue(isinstance(config.DEFAULT_PDF_PATH, Path))
        self.assertTrue(config.DEFAULT_PDF_PATH.is_file(), f"PDF not found at {config.DEFAULT_PDF_PATH}")

    def test_singleton_in_memory_cache(self):
        vs1 = idx.get_vector_store()
        vs2 = idx.get_vector_store()
        self.assertIs(vs1, vs2, "get_vector_store should return the exact same singleton instance")

    def test_no_reindexing_when_persistent_store_exists(self):
        self.assertTrue(config.INDEX_PATH.is_file(), f"Index file should exist at {config.INDEX_PATH}")
        with patch("src.indexing.extract_paragraphs") as mock_extract:
            # Force resetting the in-memory cache to test loading from disk
            idx._cached_vector_store = None
            vs = idx.get_vector_store()
            self.assertIsNotNone(vs)
            mock_extract.assert_not_called()

    def test_collecting_info_tool(self):
        result = collecting_info.invoke({"query": "diet of penguins"})
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()

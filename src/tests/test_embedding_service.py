import unittest
from unittest.mock import patch, MagicMock
import asyncio
import numpy as np

from embeddings import EmbeddingService

class TestEmbeddingServiceHuggingFace(unittest.TestCase):
    @patch("embeddings.EMBEDDING_PROVIDER", "huggingface")
    @patch("embeddings.HF_MODEL", "intfloat/multilingual-e5-large-instruct")
    def test_hf_init_and_embed(self):
        service = EmbeddingService()
        self.assertEqual(service.provider, "huggingface")
        self.assertIn("intfloat/multilingual-e5-large-instruct", service.allowed_models)
        self.assertEqual(service.default_model, "intfloat/multilingual-e5-large-instruct")
        # Test embed
        result = asyncio.run(service.embed("hello world"))
        self.assertTrue(isinstance(result, np.ndarray) or isinstance(result, list))
        self.assertEqual(len(result), 1024)

class TestEmbeddingServiceOpenAI(unittest.TestCase):
    @patch("embeddings.EMBEDDING_PROVIDER", "openai")
    def test_openai_init_and_embed(self):
        service = EmbeddingService()
        self.assertEqual(service.provider, "openai")
        self.assertIn("text-embedding-3-small", service.allowed_models)
        self.assertEqual(service.default_model, "text-embedding-3-small")
        # Test embed
        result = asyncio.run(service.embed("hello world"))
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1536)

class TestEmbeddingServiceGemini(unittest.TestCase):
    @patch("embeddings.EMBEDDING_PROVIDER", "gemini")
    def test_gemini_init_and_embed(self):
        service = EmbeddingService()
        self.assertEqual(service.provider, "gemini")
        self.assertIn("text-embedding-004", service.allowed_models)
        self.assertEqual(service.default_model, "text-embedding-004")
        # Test embed
        result = asyncio.run(service.embed("hello world"))
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 768)

class TestEmbeddingServiceOllama(unittest.TestCase):
    @patch("embeddings.EMBEDDING_PROVIDER", "ollama")
    def test_ollama_init_and_embed(self):
        service = EmbeddingService()
        self.assertEqual(service.provider, "ollama")
        self.assertIn("nomic-embed-text", service.allowed_models)
        self.assertEqual(service.default_model, "nomic-embed-text")
        # Test embed
        result = asyncio.run(service.embed("hello world"))
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 768)

if __name__ == "__main__":
    unittest.main()

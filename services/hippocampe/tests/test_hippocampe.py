import sys
import os
import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import numpy as np

# Mock sentence_transformers and qdrant_client before importing local modules
mock_st = MagicMock()
mock_encoder = MagicMock()
mock_encoder.get_sentence_embedding_dimension.return_value = 384
mock_encoder.encode.return_value = np.array([[0.1] * 384])
mock_st.SentenceTransformer.return_value = mock_encoder
sys.modules['sentence_transformers'] = mock_st

mock_qc = MagicMock()
mock_qdrant_client = AsyncMock()
mock_qdrant_client.get_collections.return_value = MagicMock(collections=[])
mock_qc.AsyncQdrantClient.return_value = mock_qdrant_client
sys.modules['qdrant_client'] = mock_qc

mock_models = MagicMock()
sys.modules['qdrant_client.http.models'] = mock_models

# Mock sqlalchemy
mock_sqla = MagicMock()
mock_engine = AsyncMock()
mock_sqla.create_async_engine.return_value = mock_engine
sys.modules['sqlalchemy.ext.asyncio'] = mock_sqla

# Mock database module variables
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

# Reset the database AsyncSessionLocal mock for testing
database.AsyncSessionLocal = MagicMock()

from rag_manager import RAGManager
import main

@pytest.mark.asyncio
async def test_add_message():
    # Setup mock session
    mock_session = AsyncMock()
    database.AsyncSessionLocal.return_value.__aenter__.return_value = mock_session

    await database.add_message("user", "Hello World")
    
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_get_recent_history():
    mock_session = AsyncMock()
    database.AsyncSessionLocal.return_value.__aenter__.return_value = mock_session

    # Mock DB result
    mock_row_1 = MagicMock()
    mock_row_1.role = "user"
    mock_row_1.content = "hello"
    mock_row_1.timestamp = MagicMock()
    mock_row_1.timestamp.strftime.return_value = "2026-06-13 18:00:00"

    mock_row_2 = MagicMock()
    mock_row_2.role = "assistant"
    mock_row_2.content = "hi"
    mock_row_2.timestamp = MagicMock()
    
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row_1, mock_row_2]
    mock_session.execute.return_value = mock_result

    history = await database.get_recent_history(5)
    assert len(history) == 2
    assert history[0]["role"] == "assistant"  # history is reversed
    assert history[1]["role"] == "user"
    assert "hello" in history[1]["content"]

@pytest.mark.asyncio
async def test_rag_ensure_collection():
    rag = RAGManager()
    # Mock collection list to not include COLLECTION_NAME
    mock_qdrant_client.get_collections.return_value = MagicMock(collections=[])
    
    await rag.ensure_collection()
    mock_qdrant_client.create_collection.assert_called_once()

@pytest.mark.asyncio
async def test_rag_add_memory():
    rag = RAGManager()
    result = await rag.add_memory_async("remember this info")
    assert "Successfully saved" in result
    mock_qdrant_client.upsert.assert_called_once()

@pytest.mark.asyncio
async def test_rag_query_memory():
    rag = RAGManager()
    
    # Mock query search results
    mock_hit = MagicMock()
    mock_hit.score = 0.9
    mock_hit.payload = {"content": "matching memory info"}
    mock_qdrant_client.search.return_value = [mock_hit]
    
    results = await rag.query_memory_async("query", threshold=0.5)
    assert results == "matching memory info"

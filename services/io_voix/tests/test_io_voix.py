import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
import numpy as np

# Intercept and mock third-party C-libraries/hardware packages
mock_sd = MagicMock()
sys.modules['sounddevice'] = mock_sd

mock_ort = MagicMock()
sys.modules['onnxruntime'] = mock_ort

mock_kokoro = MagicMock()
sys.modules['kokoro_onnx'] = mock_kokoro

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main

def test_ensure_models():
    # Test ensure_models downloads files if missing
    with patch('os.path.exists', return_value=False), \
         patch('os.makedirs') as mock_makedirs, \
         patch('urllib.request.urlretrieve') as mock_urlretrieve:
         
         model_path, voices_path = main.ensure_models()
         
         assert "kokoro-v1.0.onnx" in model_path
         assert "voices-v1.0.bin" in voices_path
         assert mock_makedirs.called
         assert mock_urlretrieve.call_count == 2

    # Test ensure_models doesn't download if files exist
    with patch('os.path.exists', return_value=True), \
         patch('os.makedirs') as mock_makedirs, \
         patch('urllib.request.urlretrieve') as mock_urlretrieve:
         
         model_path, voices_path = main.ensure_models()
         
         assert not mock_urlretrieve.called

def test_build_optimized_kokoro():
    # Verify session options config for Ryzen 5 5500U (6 physical cores)
    mock_session_options = MagicMock()
    mock_ort.SessionOptions.return_value = mock_session_options
    
    with patch('onnxruntime.InferenceSession') as mock_inference_session, \
         patch('kokoro_onnx.Kokoro.from_session') as mock_kokoro_from_session:
         
         main.build_optimized_kokoro("fake_model.onnx", "fake_voices.bin")
         
         assert mock_session_options.intra_op_num_threads == 6
         assert mock_session_options.inter_op_num_threads == 1
         assert mock_inference_session.called
         assert mock_kokoro_from_session.called

def test_audio_player_worker():
    # Test the audio player worker queue and NATS integration
    mock_nc = AsyncMock()
    mock_loop = MagicMock()
    
    # Put a dummy audio packet in queue, then None to stop the worker
    fake_samples = np.zeros(100)
    main.audio_sync_queue.put((fake_samples, 1, "test text", True))
    main.audio_sync_queue.put(None)
    
    mock_output_stream = MagicMock()
    mock_sd.OutputStream.return_value.__enter__.return_value = mock_output_stream
    
    with patch('asyncio.run_coroutine_threadsafe') as mock_run_coroutine:
        main.native_audio_player_worker(mock_loop, mock_nc)
        
        # Verify direct hardware stream write
        mock_output_stream.write.assert_called_once()
        # Verify NATS publications were scheduled in loop thread
        assert mock_run_coroutine.call_count == 2

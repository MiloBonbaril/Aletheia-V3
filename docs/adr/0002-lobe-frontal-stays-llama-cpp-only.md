# lobe_frontal remains hardcoded to llama.cpp; Groq/Mistral interfaces and the INTERFACE env var stay unwired

`services/lobe_frontal/.env` declares an `INTERFACE` variable and API keys for Groq and Mistral, and `Groq/interface.py` / `Mistral/interface.py` both implement working `get_stream()` methods — but `main.py` ignores all of this and hardcodes `OpenAIInterface` (pointed at a local llama.cpp server) at import time. We're leaving this as-is: the TUI overhaul does not wire up `INTERFACE`-based backend switching.

The project is currently fully committed to local llama.cpp; multi-backend switching depends on capabilities that don't exist yet (a proper TTS pipeline, OCR), so building interface selection now would be speculative. This is a deliberate scope boundary, not an oversight — a future engineer finding the unused `INTERFACE` env var and dead Groq/Mistral classes should not assume they're bugs to fix.

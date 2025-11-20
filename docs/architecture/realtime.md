# Realtime & Voice

## Realtime Chat

* Use WebSockets (FastAPI `websockets`) or a managed service (Pusher, Ably) for real-time messaging and progress updates.
* Messages are persisted to `AIConversation` and optionally indexed.

## Voice (Realtime Conversation)

Options:

1. WebRTC for low-latency audio streams — signaling handled by FastAPI WebSocket endpoint, media servers as needed.
2. For simpler implementation: use client-side recording → send audio chunks to `/api/v1/ai/voice` (multipart) → backend streams to STT (Speech-to-Text) → process text with LLM → TTS response streamed back.

## Suggested Flow for MVP Voice

* Client records short chunks (1–5s) and streams to `/ai/voice-stream` via websocket.
* Backend forwards to STT (e.g., OpenAI/whisper, cloud STT) and receives partial transcripts.
* Backend sends transcript to AI chat pipeline and returns assistant response text.
* TTS (e.g., gTTS, cloud TTS) produces audio and server streams playable URL or base64 audio chunk back to client.

## Security

* Require auth token for voice endpoints; limit duration & rate.


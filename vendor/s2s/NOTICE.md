# Upstream attribution

Project: [Hugging Face speech-to-speech](https://github.com/huggingface/speech-to-speech).
Revision: `14f9e33d5aa7a1d65c94cfb7b9c0f9c630310689`.
License: Apache-2.0, reproduced in `LICENSE`.

`thread_manager.py` and `vad_iterator.py` are unmodified upstream source files.
The application uses both modules directly.
`app/pipeline.py` adapts the upstream four-stage queue architecture for a local-only browser transport, bounded queues, cancellation, sentence synthesis, and one shared model lifecycle lock.
The upstream cloud transports and model download mechanisms are not used.

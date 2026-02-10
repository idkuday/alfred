# Piper Voice Models

This directory contains Piper TTS voice models (`.onnx` files).

## Download Voice Model

**Recommended: British English Male Voice (Alan)**

1. Visit: https://github.com/rhasspy/piper/blob/master/VOICES.md
2. Download `en_GB-alan-medium.onnx` and `en_GB-alan-medium.onnx.json`
3. Place both files in this directory

Or use curl:

```bash
# Download voice model
curl -L -o ai_server/audio/voices/en_GB-alan-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx

# Download config
curl -L -o ai_server/audio/voices/en_GB-alan-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json
```

## Other Voices

Browse available voices at: https://rhasspy.github.io/piper-samples/

Model files are ~20-60MB each and are gitignored.

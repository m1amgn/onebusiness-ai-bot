# Synthetic voice fixture

`voice-en.ogg` contains synthetic English speech, not a real conversation:

> Please remind me to call the supplier tomorrow morning about the invoice.

Created on macOS, then encoded as mono OGG/Opus at 16 kbit/s:

```sh
say -v Samantha -o /tmp/voice-en.aiff "Please remind me to call the supplier tomorrow morning about the invoice."
ffmpeg -i /tmp/voice-en.aiff -c:a libopus -b:a 16k -ac 1 -application voip voice-en.ogg
```

The committed file can be used on Linux without `say`. Local transcription uses
the whisper.cpp server with `--convert -l auto` and ffmpeg on PATH.

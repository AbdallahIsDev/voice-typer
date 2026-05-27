# Qwen3-ASR-0.6B Integration

## What's Done

### Dependencies Installed
- `qwen-asr==0.0.6` + all deps (`transformers==4.57.6`, `accelerate==1.12.0`, `qwen-omni-utils`, `librosa`, `soundfile`, `flask`, `gradio`, etc.)
- PyTorch 2.11.0+cu128 (CUDA 12.8) — confirmed working, `torch.cuda.is_available() == True`
- vLLM is **NOT** installed. vLLM has **no Windows wheels** — Linux-only. Streaming transcription (`streaming_transcribe()`) won't work on Windows. Batch-only via transformers backend.

### Model Weights — NOT Downloaded (User Downloads Manually)
**CRITICAL: Do NOT auto-download model weights.** The user wants to download them manually.

The user will download the model themselves. Weights are **missing** until then.
Once downloaded, they will be at a local path the user specifies.

To download manually (for reference, do NOT run this automatically):
```
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir ./Qwen3-ASR-0.6B
```

### No Code Written Yet
Zero Qwen references in source. `voice_typer/transcription.py` is pure `faster-whisper`.

## Integration Surface

### Qwen3ASRModel API (`qwen_asr.Qwen3ASRModel`)

| Method | Signature | Notes |
|--------|-----------|-------|
| `from_pretrained()` | `(model_name_or_path: str, **kwargs) -> Qwen3ASRModel` | Uses `AutoModel.from_pretrained()` under the hood |
| `transcribe()` | `(audio: AudioLike | List[AudioLike], language, return_time_stamps) -> List[ASRTranscription]` | Accepts `(np.ndarray, sr)` tuples. Returns text per input. |
| `streaming_transcribe()` | `(pcm16k: np.ndarray, state: ASRStreamingState) -> ASRStreamingState` | **vLLM backend only** — won't work on Windows |
| `init_streaming_state()` | `() -> ASRStreamingState` | For streaming |
| `get_supported_languages()` | `() -> list` | Check available languages |

### Audio Input Format
- `transcribe()` accepts `(np.ndarray, sample_rate)` tuples
- Expects 16kHz mono PCM float32 internally (same as Whisper)
- The project already provides 16kHz float32 audio via `voice_typer/recording.py`

### ASRTranscription Return Type
```python
@dataclass
class ASRTranscription:
    text: str
    language: Optional[str]
    # timestamps available if forced_aligner is provided + return_time_stamps=True
```

## Architecture Decision: Qwen as Parallel Backend

- Qwen adds as a **new optional backend** — completely separate from Whisper
- **Never delete or replace** existing faster-whisper code
- **Never modify existing files** — create new `voice_typer/qwen_engine.py`
- Whisper stays as the **default and fallback**
- Qwen activated via config key (e.g. `"asr_backend": "qwen"`)
- Both models can coexist — Qwen loaded on-demand, Whisper always available
- Model path configurable (user downloads manually, sets path in config)

### Design
- New file: `voice_typer/qwen_engine.py` — `QwenEngine` class with `transcribe(audio: np.ndarray) -> str`
- Same interface as Whisper's `TranscriptionEngine.transcribe()` — plug-and-play
- Config: `"qwen_model_path": "<local_path>"` — path to manually downloaded weights
- If path is empty or weights missing → graceful fallback to Whisper, no crash

## Testing Strategy (No Auto-Download)

**Unit tests MUST mock the Qwen model.** No real weights needed.

```python
# Mock approach for tests
@pytest.fixture
def mock_qwen_engine(monkeypatch):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = [MagicMock(text="hello world")]
    monkeypatch.setattr("qwen_asr.Qwen3ASRModel.from_pretrained", 
                        lambda *a, **kw: mock_model)
    yield
```

Integration tests only run when user confirms they've downloaded the model manually and sets a flag/environment variable.

## Critical Constraints

1. **No auto-download**: The `from_pretrained()` call will fail if the local path doesn't have weights. Code must handle this gracefully — catch the error, log it, fall back to Whisper.

2. **Streaming unsupported on Windows**: vLLM has no Windows wheels. Qwen streaming won't work. Only batch transcription.

3. **GPU memory**: Qwen3-ASR-0.6B needs ~1.2-1.6 GB VRAM. Current GPU already runs Whisper small.en on CUDA. Both simultaneously may exceed VRAM — consider unloading Whisper when Qwen is active.

4. **vLLM not available**: transformers backend only. Expect RTF ~0.05-0.1 (slower than faster-whisper's ~0.01-0.02). User is okay with 1-2 second transcription delay.

5. **No changes to existing files**: New module only. Whisper code untouched.

## Existing relevant code (READ ONLY)

- `voice_typer/transcription.py` — `TranscriptionEngine` class, `transcribe()`, `load()`
- `voice_typer/app.py:83` — `TranscriptionEngine` instantiation
- `voice_typer/config.py` — config model/device handling
- `tests/test_transcription.py` — existing fallback chain tests

## Files & paths

- Repo root: `C:\Users\11\tools\persistent-voice-typing`
- qwen-asr package: `C:\Users\11\AppData\Local\Programs\Python\Python312\Lib\site-packages\qwen_asr`
- HF model: `Qwen/Qwen3-ASR-0.6B` (1.88 GB, safetensors)

## Task Order

This is the **first priority** (see PROBLEMS.md). Complete Qwen integration before fixing any existing bugs.

### Steps
1. Create `voice_typer/qwen_engine.py` with `QwenEngine` class
2. Interface: `transcribe(audio: np.ndarray) -> str` (same as Whisper)
3. Config key `qwen_model_path` for local path to weights
4. Graceful fallback to Whisper if Qwen unavailable
5. Add config/settings wiring for backend selection
6. Write tests (mocked — no real model)
7. Integration test with real weights only after user confirms download

## Suggested skills

- **code** — surgical changes, no greenfield rewrites, preserve existing Whisper code
- **tdd** — test-first for the new Qwen backend wrapper

"""Metadata registry for all supported ASR models.

This module is the single source of truth for *rich*
model metadata — anything beyond the bare ``download_size_mb`` table
that lives in :mod:`voice_typer.server.transcription`.

Each entry in :data:`MODEL_REGISTRY` is a :class:`ModelMetadata`
instance capturing:

- ``download_size_mb`` — same value as ``_MODEL_SIZE_MB[name]``
- ``required_vram_mb`` — estimated VRAM for inference (GPU) or RAM (CPU)
- ``backend`` — ``"whisper"`` for the standard Systran faster-whisper
  repos, ``"distil-whisper"`` for the distilled variants.  Used by
  :func:`get_models_by_backend` to filter the catalog.
- ``multilingual`` — whether the model handles non-English audio
- ``supported_languages`` — ``None`` (all) or a list like ``["en"]``
- ``description`` — one-line, user-facing description shown in the
  Models page card.
- ``repo_id`` — HuggingFace repo ID.  Distilled variants use the
  ``Systran/faster-distil-whisper-*`` prefix; the rest use
  ``Systran/faster-whisper-*``.
- ``is_distilled`` — ``True`` for distil-* models
- ``speed_rating`` — ``"fast"`` / ``"medium"`` / ``"slow"``
- ``accuracy_rating`` — ``"low"`` / ``"medium"`` / ``"high"``
- ``wer`` — published Word Error Rate (WER, %) on the LibriSpeech
  ``test-clean`` benchmark (lower is better), sourced from each model's
  official model card / evaluation.  ``None`` means no reliable
  published figure is available — the Models page must omit the WER
  field for that model rather than guessing.
``network_behavior`` () — one of ``"local-only"``,
  ``"downloads-on-first-use-consent-gated"``,
  ``"downloads-on-first-use-no-consent"``, or ``"cloud-per-call"``.
  Declares what network activity the model requires so the UI / privacy
  surface can show "downloads on first use (consent gated)" vs.
  "local-only" vs. "cloud per call" honestly.  Whisper + distil variants
  are consent-gated HF downloads; parakeet downloads without explicit
consent (documented honestly per  — this is a known issue to
  fix in a follow-up); qwen is local-only (user supplies the model
  path); cloud providers (not in this registry) are per-call.

The registry is consulted by:

- :func:`voice_typer.server.handlers.model_handlers._handle_get_model_catalog`
  to populate the Models page with rich metadata.
- :meth:`voice_typer.server.service.VoiceTyperService.download_model`
  to resolve the repo_id for new variants (turbo, distil-*) without
  hard-coding a separate name→repo map.

Models are NEVER auto-downloaded at import time.  Importing this
module is side-effect-free; downloads only happen when the user
clicks "Download" on the Models page.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelMetadata:
    """Rich metadata for a single ASR model variant.

    ``frozen=True`` so registry entries can be safely shared between
    threads (the renderer reads them via IPC; the service layer reads
    them during downloads) without copying.
    """

    name: str
    download_size_mb: int
    required_vram_mb: int
    backend: str
    multilingual: bool
    supported_languages: list[str] | None  # None = all languages
    description: str
    repo_id: str
    is_distilled: bool = False
    speed_rating: str = "medium"  # "fast", "medium", "slow"
    accuracy_rating: str = "high"  # "low", "medium", "high"
    # Published WER (%) on LibriSpeech test-clean — sourced per entry
    # (see the comments in MODEL_REGISTRY). ``None`` = no reliable
    # published figure; the Models page omits the WER field for it.
    wer: float | None = None
    # declares what network activity the model requires, so the
    # UI/privacy surface can show "downloads on first use (consent gated)"
    # vs. "local-only" vs. "cloud per call" honestly.  Default
    # ``"local-only"`` is the safest assumption — entries that DO
    # download must override explicitly so the catalog cannot silently
    # misrepresent a download as offline.
    network_behavior: str = "local-only"
    # User-facing display name shown on the model card. ``None`` means
    # the renderer falls back to the bare ``name`` (e.g. whisper
    # variants render as ``tiny`` / ``large-v3`` / ``large-v3-turbo``).
    # Detailed names are set for parakeet ("Parakeet-TDT-0.6b-V3") and
    # qwen ("Qwen-3") so the Models page shows the full model name
    # under the family header.
    display_name: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict for IPC transport.

        ``asdict`` handles the dataclass fields; we additionally
        normalize ``supported_languages`` so the renderer can always
        treat ``None`` as "all languages" via the explicit ``null``.
        """
        return asdict(self)


# ── Registry ────────────────────────────────────────────────────────
#
# Order matters for the Models page: entries appear in this order in
# the catalog.  Group by size (tiny → large → turbo → distil) so the
# user sees the most relevant models first.

# The canonical DEFAULT model size — the single source of truth for
# the model that fresh installs / config resets fall back to (config
# dataclass default, load-time coercion reset target, onboarding
# pre-selection, IPC payload defaults, and the renderer's fallback).
# Changing the default later is a ONE-LINE change here (plus the
# client mirror ``MODEL_DEFAULT`` in
# ``client/src/renderer/src/pages/onboarding/lib/constants.ts`` — the
# parity test ``tests/test_default_model_sync.py`` keeps them in
# lockstep). MUST reference an entry that stays in ``MODEL_REGISTRY``.
DEFAULT_MODEL_SIZE: str = "tiny"

# Sentinel for "no model selected": the config's ``model_size`` can be
# the empty string, meaning the user has genuinely not picked a model
# yet (or their previous selection was cleared because the model's
# weights were removed and no other model was on disk). The app must
# NOT try to load a model in this state — it reports "No model
# selected" in the tray tooltip / Models page and waits for the user
# to pick one. ``""`` is used instead of ``None`` so ``model_size``
# stays a plain ``str`` end-to-end (JSON round-trip, IPC types, the
# renderer's ``ModelSize`` union).
NO_MODEL_SIZE: str = ""

MODEL_REGISTRY: dict[str, ModelMetadata] = {
    # ── Standard Whisper variants (Systran/faster-whisper-*) ──────
    "tiny": ModelMetadata(
        name="tiny",
        download_size_mb=75,
        required_vram_mb=512,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Fastest multilingual model. Low accuracy, good for testing.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-tiny",
        speed_rating="fast",
        accuracy_rating="low",
        # WER 7.5% on LibriSpeech test-clean — self-reported in the
        # openai/whisper-tiny model card ("Test WER ... self-reported
        # 7.540"); Whisper paper Table 9 reports 7.6 (greedy). The
        # Systran/faster-whisper-tiny weights are the same OpenAI
        # checkpoints.
        wer=7.5,
    ),
    # ``large-v3`` — highest-accuracy multilingual Whisper. Restored
    # to the catalog 2026-08-15 at the user's request (the initial
    # catalog prune kept only tiny + large-v3-turbo).
    "large-v3": ModelMetadata(
        name="large-v3",
        download_size_mb=3000,
        required_vram_mb=4096,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Highest-accuracy Whisper model. Slow on CPU; GPU strongly recommended.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v3",
        speed_rating="slow",
        accuracy_rating="high",
        # WER 2.0% on LibriSpeech test-clean — published Whisper
        # benchmark consensus (whisper.cpp benchmark tables / HF Open
        # ASR Leaderboard; e.g. "Whisper Large-v3 ... 2.0% WER on
        # LibriSpeech test-clean"). OpenAI does not pin a number in the
        # large-v3 model card; 2.0% is the standard published figure
        # for these weights.
        wer=2.0,
    ),
    # Turbo () ─────────────────────────────────────
    # ``large-v3-turbo`` is OpenAI's 2024 fast multilingual model:
    # near-large-v3 accuracy at ~8x speed.
    "large-v3-turbo": ModelMetadata(
        name="large-v3-turbo",
        download_size_mb=809,
        required_vram_mb=2000,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Turbo model — near-large-v3 accuracy at 8x speed. Recommended for most users.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v3-turbo",
        speed_rating="fast",
        accuracy_rating="high",
        # WER 2.1% on LibriSpeech test-clean — published benchmark
        # consensus for the pruned turbo checkpoint (slightly above
        # large-v3's 2.0%; OpenAI's turbo release notes describe it as
        # "minor quality degradation" vs large-v3).
        wer=2.1,
    ),
    # ── Parakeet (by NVIDIA) ──────────────────────────────────────
    # added to registry so get_model_status() can resolve the
    # repo_id and check HF cache download status. Previously the model
    # was only hardcoded in service.py / tray_models.py, causing status
    # checks to fail silently (returning downloaded=false even when the
    # model was fully cached).
    "parakeet": ModelMetadata(
        name="parakeet",
        display_name="Parakeet-TDT-0.6b-V3",
        download_size_mb=1275,
        required_vram_mb=3072,
        backend="parakeet",
        multilingual=True,
        supported_languages=None,
        description="NVIDIA Parakeet TDT 0.6b v3 — ONNX fp16 export (grikdotnet), fast CPU/GPU ASR without PyTorch.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="grikdotnet/parakeet-tdt-0.6b-fp16",
        speed_rating="fast",
        accuracy_rating="high",
        # WER 1.93% on LibriSpeech test-clean — self-reported in the
        # nvidia/parakeet-tdt-0.6b-v3 model card (model-index:
        # "LibriSpeech (clean) ... Test WER 1.93"; test-other 3.59).
        # The app downloads the grikdotnet ONNX fp16 export of the
        # same checkpoint.
        wer=1.93,
    ),
    # ── Qwen (by Alibaba) ─────────────────────────────────────────
    # added to registry for status consistency. Qwen uses a
    # different download mechanism — the user must manually configure
    # ``qwen_model_path`` in Settings (pointing at a local snapshot of
    # ``Qwen/Qwen3-``). The repo_id below is informational
    # only; it is NOT auto-fetched. ``_check_qwen_deps()`` verifies
    # the configured path exists locally before the engine loads.
    #
    # previously the description claimed
    # "Auto-downloaded on first use", which was inaccurate — the
    # engine does not auto-download. Corrected to "Requires manual
    # model path setup in Settings" so the user is not misled into
    # expecting a transparent first-use download.
    "qwen": ModelMetadata(
        name="qwen",
        display_name="Qwen-3",
        download_size_mb=0,  # local-only — size depends on user-supplied snapshot
        required_vram_mb=4096,
        backend="qwen",
        multilingual=True,
        supported_languages=None,
        description="Alibaba Qwen3-ASR-1.7B (ONNX) — multilingual ASR via "
        "onnxruntime. Requires manual model path setup in Settings "
        "(pre-exported ONNX dir, see PLAN_ONNX_INTEGRATION.md §4.3 C-2).",
        network_behavior="local-only",
        # The pre-exported ONNX repo (torch-free, 2026-08-15) — the old
        # torch ``Qwen/Qwen-Audio`` repo_id was removed with the torch
        # engine.
        repo_id="andrewleech/qwen3-asr-1.7b-onnx",
        speed_rating="medium",
        accuracy_rating="high",
        # WER 1.63% on LibriSpeech test-clean — from the official
        # Qwen/Qwen3-ASR-1.7B model card evaluation table
        # ("LibriSpeech clean | other ... 1.63 | 3.38", best of the
        # compared models). The app runs a pre-exported ONNX snapshot
        # (andrewleech/qwen3-asr-1.7b-onnx) of the same checkpoint.
        wer=1.63,
    ),
}


def get_model_metadata(model_size: str) -> ModelMetadata | None:
    """Return metadata for ``model_size`` or ``None`` if unknown.

    Safe to call with any string — never raises.  Used by
    :meth:`VoiceTyperService.download_model` to resolve the HF repo_id
    without hard-coding a separate name→repo map.
    """
    return MODEL_REGISTRY.get(model_size)


def get_default_model_size() -> str:
    """Return the canonical default model size.

    Single indirection over :data:`DEFAULT_MODEL_SIZE` so callers that
    want to swap the default later can do so in one place.
    """
    return DEFAULT_MODEL_SIZE


def get_all_models() -> list[ModelMetadata]:
    """Return all registered models in registry order.

    The Models page renders the catalog in this order (tiny → large →
    turbo → distil).
    """
    return list(MODEL_REGISTRY.values())


def get_models_by_backend(backend: str) -> list[ModelMetadata]:
    """Return only models whose ``backend`` matches ``backend``.

    Used by the Models page to filter by backend (e.g. show only
    distilled variants).  Returns an empty list if no models match.
    """
    return [m for m in MODEL_REGISTRY.values() if m.backend == backend]


# the canonical allowlist of model names a user is
# permitted to select (onboarding picker + Config.load() validation +
# Settings page model selector). Exposed as a function (not a module
# constant) so callers always see the current registry state — if a
# model is added to ``MODEL_REGISTRY`` in a future PR, this allowlist
# updates automatically without needing a parallel hardcoded set.
#
# This is the durable fix for : the pre-fix ``ALLOWED_USER_MODELS``
# hardcoded set in ``config_validators.py`` drifted out of sync with
# ``OnboardingController.MODEL_OPTIONS`` (the onboarding picker offered
# ``"tiny"``, ``"small"``, ``"medium"`` multilingual variants, but the
# allowlist only contained the ``.en`` English-only variants — so
# Config.load() silently reset non-English users to ``"small.en"`` on
# every restart). The allowlist now lives here next to the registry;
# ``config_validators.ALLOWED_USER_MODELS`` can be populated from it,
# and the ``tests/test_allowed_user_models.py`` regression test
# asserts every name in ``OnboardingController.MODEL_OPTIONS`` is in
# this set.
#
# The function returns a ``frozenset`` so callers can cheaply do
# ``name in get_user_selectable_model_names()`` membership checks
# without re-traversing the registry on each call site. The set is
# rebuilt on every call (cheap — ~20 entries) so it always reflects
# the current registry state.
def get_user_selectable_model_names() -> frozenset[str]:
    """Return the set of model names a user is allowed to select.

    This is the single source of truth for ``ALLOWED_USER_MODELS`` —
    onboarding, config validation, and the Settings model selector all
    derive from this set so they can never drift out of sync.

    Currently returns every name in ``MODEL_REGISTRY`` (every entry is
    user-selectable). If a future PR adds internal-only models (e.g. a
    test fixture or an experimental backend not yet exposed in the UI),
    add a ``user_selectable: bool = True`` field to ``ModelMetadata``
    and filter here — but for now all registry entries are user-facing.
    """
    return frozenset(MODEL_REGISTRY.keys())


__all__ = [
    "ModelMetadata",
    "MODEL_REGISTRY",
    "DEFAULT_MODEL_SIZE",
    "NO_MODEL_SIZE",
    "get_default_model_size",
    "get_model_metadata",
    "get_all_models",
    "get_models_by_backend",
    "get_user_selectable_model_names",
]

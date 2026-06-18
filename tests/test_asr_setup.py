"""Tests for ASR auto-setup utilities."""

import sys
import pytest
from unittest.mock import MagicMock, patch, call


# ARCH-001 / DEAD-001: TestDetectGpu was removed because detect_gpu() was
# dead code removed from asr_setup.py. The function is archived at
# archive/asr_setup_dead_code.py::_archived_detect_gpu().
#
# DEAD-002: TestCheckDependencies was removed because check_dependencies()
# was dead code removed from asr_setup.py. The function is archived at
# archive/asr_setup_dead_code.py::_archived_check_dependencies().
#
# ARCH-001: TestPipInstall and TestDownloadWeights were removed because the
# corresponding functions (pip_install, download_weights) were dead code
# moved to archive/asr_setup_dead_code.py.
#
# Only ensure_hf_env() and download_parakeet_weights() remain as live
# functions with active tests.
#
# If any of these functions are revived, restore the corresponding test
# classes from git history (commit HEAD~1 for detect_gpu/check_dependencies,
# or earlier for pip_install/download_weights).

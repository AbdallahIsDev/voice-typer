# CI Errors

> Auto-generated from the latest GitHub Actions run via `scripts/ci/write_ci_errors.py`. Do not edit by hand — it is overwritten on every CI run.

**98 failing/errored test(s)** across 8 matrix leg(s).

### 1. `tests.handlers.test_handler_signature_conformance`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/handlers/test_handler_signature_conformance.py:39`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/handlers/test_handler_signature_conformance.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/handlers/test_handler_signature_conformance.py:39: in <module>
    from voice_typer.server.handlers.dictation_handlers import (  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 2. `tests.handlers.test_privacy_handlers`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/handlers/test_privacy_handlers.py:40`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/handlers/test_privacy_handlers.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/handlers/test_privacy_handlers.py:40: in <module>
    from voice_typer.server.handlers import PrivacyHandlersMixin as ReExportedMixin
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 3. `tests.regressions.test_cli_exit_codes`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/regressions/test_cli_exit_codes.py:38`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/regressions/test_cli_exit_codes.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/regressions/test_cli_exit_codes.py:38: in <module>
    from voice_typer.server import ipc_server
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 4. `tests.regressions.test_tcp_live`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/regressions/test_tcp_live.py:47`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/regressions/test_tcp_live.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/regressions/test_tcp_live.py:47: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 5. `tests.security.test_tcp_accept_worker_pool`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/security/test_tcp_accept_worker_pool.py:26`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/security/test_tcp_accept_worker_pool.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/security/test_tcp_accept_worker_pool.py:26: in <module>
    from voice_typer.server.ipc_server import IPCServer  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 6. `tests.server`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/server/conftest.py:57`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
<frozen importlib._bootstrap_external>:883: in exec_module
    ???
<frozen importlib._bootstrap>:241: in _call_with_frames_removed
    ???
tests/server/conftest.py:57: in <module>
    from voice_typer.server import event_bus, ipc_server  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 7. `tests.service.test_status_volume_cache`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/service/test_status_volume_cache.py:47`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/service/test_status_volume_cache.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/service/test_status_volume_cache.py:47: in <module>
    from voice_typer.server.service.status import StatusMixin
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 8. `tests.test_asr_errors_consent`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_asr_errors_consent.py:57`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_asr_errors_consent.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_asr_errors_consent.py:57: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 9. `tests.test_cloud_connection_ipc_wiring`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_cloud_connection_ipc_wiring.py:42`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_cloud_connection_ipc_wiring.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_cloud_connection_ipc_wiring.py:42: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 10. `tests.test_cloud_provider_map_single_source`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_cloud_provider_map_single_source.py:42`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_cloud_provider_map_single_source.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_cloud_provider_map_single_source.py:42: in <module>
    from voice_typer.server.handlers import cloud_test_handlers
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 11. `tests.test_cloud_test_handlers_redirect`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_cloud_test_handlers_redirect.py:49`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_cloud_test_handlers_redirect.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_cloud_test_handlers_redirect.py:49: in <module>
    from voice_typer.server.handlers import cloud_test_handlers
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 12. `tests.test_command_registry_parity`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_command_registry_parity.py:44`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_command_registry_parity.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_command_registry_parity.py:44: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 13. `tests.test_dead_code_stays_removed`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_dead_code_stays_removed.py:26`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_dead_code_stays_removed.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_dead_code_stays_removed.py:26: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 14. `tests.test_di_providers`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_di_providers.py:38`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_di_providers.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_di_providers.py:38: in <module>
    from voice_typer.server.ipc_server import IPCServer  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 15. `tests.test_download_model_dispatcher_structure`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_download_model_dispatcher_structure.py:39`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_download_model_dispatcher_structure.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_download_model_dispatcher_structure.py:39: in <module>
    from voice_typer.server.service._download_helpers import (
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 16. `tests.test_download_model_return_shape`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_download_model_return_shape.py:20`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_download_model_return_shape.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_download_model_return_shape.py:20: in <module>
    from voice_typer.server.service import VoiceTyperService
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 17. `tests.test_download_progress_events`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_download_progress_events.py:15`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_download_progress_events.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_download_progress_events.py:15: in <module>
    from voice_typer.server.service import VoiceTyperService
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 18. `tests.test_e2e_pipeline`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_e2e_pipeline.py:36`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_e2e_pipeline.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_e2e_pipeline.py:36: in <module>
    from voice_typer.server.ipc_server import IPCServer  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 19. `tests.test_heartbeat`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_heartbeat.py:54`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_heartbeat.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_heartbeat.py:54: in <module>
    from voice_typer.server.ipc_server import (
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 20. `tests.test_heartbeat_force_exit`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_heartbeat_force_exit.py:44`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_heartbeat_force_exit.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_heartbeat_force_exit.py:44: in <module>
    from voice_typer.server.ipc_server import (
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 21. `tests.test_ipc_deadlock_regression`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_deadlock_regression.py:37`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_deadlock_regression.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_deadlock_regression.py:37: in <module>
    from voice_typer.server.ipc_server import IPCServer, _TCPLineIO
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 22. `tests.test_ipc_dispatch_errors`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_dispatch_errors.py:56`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_dispatch_errors.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_dispatch_errors.py:56: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 23. `tests.test_ipc_error_envelope_helper`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_error_envelope_helper.py:33`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_error_envelope_helper.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_error_envelope_helper.py:33: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 24. `tests.test_ipc_error_envelope_parity`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_error_envelope_parity.py:54`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_error_envelope_parity.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_error_envelope_parity.py:54: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 25. `tests.test_ipc_no_client_log_redaction`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_no_client_log_redaction.py:41`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_no_client_log_redaction.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_no_client_log_redaction.py:41: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 26. `tests.test_ipc_pending_tcp_remerge`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_pending_tcp_remerge.py:42`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_pending_tcp_remerge.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_pending_tcp_remerge.py:42: in <module>
    from voice_typer.server.ipc_server import IPCServer, _TCPLineIO
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 27. `tests.test_ipc_rate_limiter_dual_window`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_rate_limiter_dual_window.py:29`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_rate_limiter_dual_window.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_rate_limiter_dual_window.py:29: in <module>
    from voice_typer.server.ipc_server import (
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 28. `tests.test_ipc_send_shutdown_allowlist`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_send_shutdown_allowlist.py:41`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_send_shutdown_allowlist.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_send_shutdown_allowlist.py:41: in <module>
    from voice_typer.server.ipc_server import _SHUTDOWN_ALLOWLIST, IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 29. `tests.test_ipc_sender_select`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_sender_select.py:31`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_sender_select.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_sender_select.py:31: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 30. `tests.test_ipc_server`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_server.py:67`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_server.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_server.py:67: in <module>
    from voice_typer.server.ipc_server import (
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 31. `tests.test_ipc_server_main_diagnostics`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_server_main_diagnostics.py:47`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_server_main_diagnostics.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_server_main_diagnostics.py:47: in <module>
    import voice_typer.server.ipc_server  # noqa: F401
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 32. `tests.test_ipc_shutdown_registry`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_shutdown_registry.py:37`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_shutdown_registry.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_shutdown_registry.py:37: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 33. `tests.test_ipc_tray_click_validation`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_ipc_tray_click_validation.py:42`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_ipc_tray_click_validation.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_ipc_tray_click_validation.py:42: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 34. `tests.test_keyboard_ownership_watchdog`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_keyboard_ownership_watchdog.py:29`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_keyboard_ownership_watchdog.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_keyboard_ownership_watchdog.py:29: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 35. `tests.test_module_constant_hoist`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_module_constant_hoist.py:12`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_module_constant_hoist.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_module_constant_hoist.py:12: in <module>
    from voice_typer.server.ipc_server import (
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 36. `tests.test_pack_atomic_swap`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_atomic_swap.py:36`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_atomic_swap.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_atomic_swap.py:36: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 37. `tests.test_pack_checksum_background`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_checksum_background.py:29`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_checksum_background.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_checksum_background.py:29: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 38. `tests.test_pack_consent_gate`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_consent_gate.py:31`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_consent_gate.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_consent_gate.py:31: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 39. `tests.test_pack_corruption_recovery`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_corruption_recovery.py:28`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_corruption_recovery.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_corruption_recovery.py:28: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 40. `tests.test_pack_disk_full_during_download`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_disk_full_during_download.py:26`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_disk_full_during_download.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_disk_full_during_download.py:26: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 41. `tests.test_pack_disk_space_check`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_disk_space_check.py:32`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_disk_space_check.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_disk_space_check.py:32: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 42. `tests.test_pack_download_resume`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_download_resume.py:29`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_download_resume.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_download_resume.py:29: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 43. `tests.test_pack_dual_instance`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_dual_instance.py:35`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_dual_instance.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_dual_instance.py:35: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 44. `tests.test_pack_fallback_dir`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_fallback_dir.py:27`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_fallback_dir.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_fallback_dir.py:27: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 45. `tests.test_pack_github_rate_limit`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_github_rate_limit.py:28`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_github_rate_limit.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_github_rate_limit.py:28: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 46. `tests.test_pack_install`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_install.py:43`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_install.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_install.py:43: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 47. `tests.test_pack_missing_on_launch`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_missing_on_launch.py:33`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_missing_on_launch.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_missing_on_launch.py:33: in <module>
    from voice_typer.server.service import offline_pack, update_check
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 48. `tests.test_pack_proxy`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_proxy.py:23`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_proxy.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_proxy.py:23: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 49. `tests.test_pack_schema_caps`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_schema_caps.py:48`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_schema_caps.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_schema_caps.py:48: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 50. `tests.test_pack_signing`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_signing.py:32`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_signing.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_signing.py:32: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 51. `tests.test_pack_version_change_during_download`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_pack_version_change_during_download.py:30`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_pack_version_change_during_download.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_pack_version_change_during_download.py:30: in <module>
    from voice_typer.server.service import offline_pack
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 52. `tests.test_privacy_helpers`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_privacy_helpers.py:37`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_privacy_helpers.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_privacy_helpers.py:37: in <module>
    from voice_typer.server.service.privacy import PrivacyMixin
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 53. `tests.test_sender_select_timeout`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_sender_select_timeout.py:31`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_sender_select_timeout.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_sender_select_timeout.py:31: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 54. `tests.test_service_download_consent`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_service_download_consent.py:34`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_service_download_consent.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_service_download_consent.py:34: in <module>
    from voice_typer.server.service import VoiceTyperService
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 55. `tests.test_service_llm_consent`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_service_llm_consent.py:11`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_service_llm_consent.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_service_llm_consent.py:11: in <module>
    from voice_typer.server.service import VoiceTyperService
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 56. `tests.test_sidecar_ready_emitted`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_sidecar_ready_emitted.py:40`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_sidecar_ready_emitted.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_sidecar_ready_emitted.py:40: in <module>
    from voice_typer.server.ipc_server import IPCServer  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 57. `tests.test_sidecar_ws_ready_ordering`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_sidecar_ws_ready_ordering.py:40`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_sidecar_ws_ready_ordering.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_sidecar_ws_ready_ordering.py:40: in <module>
    from voice_typer.server.ipc_server import IPCServer  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 58. `tests.test_startup_error_log_cap`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_startup_error_log_cap.py:47`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_startup_error_log_cap.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_startup_error_log_cap.py:47: in <module>
    from voice_typer.server import ipc_server
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 59. `tests.test_tcp_dispatch_concurrency`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_tcp_dispatch_concurrency.py:53`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_tcp_dispatch_concurrency.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_tcp_dispatch_concurrency.py:53: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 60. `tests.test_tcp_drain_batching`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_tcp_drain_batching.py:36`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_tcp_drain_batching.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_tcp_drain_batching.py:36: in <module>
    from voice_typer.server.ipc_server import IPCServer, _TCPLineIO
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 61. `tests.test_tcp_idle_read_timeout`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_tcp_idle_read_timeout.py:26`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_tcp_idle_read_timeout.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_tcp_idle_read_timeout.py:26: in <module>
    from voice_typer.server.ipc_server import IPCServer  # noqa: E402
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 62. `tests.test_transport_write_raw`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_transport_write_raw.py:22`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_transport_write_raw.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_transport_write_raw.py:22: in <module>
    from voice_typer.server.ipc_server import _TCPLineIO
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 63. `tests.test_trusted_extra_hosts`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_trusted_extra_hosts.py:37`

```
ImportError: cannot import name 'IPCServer' from 'tests.server.conftest' (/Users/runner/work/voice-typer/voice-typer/tests/server/conftest.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_trusted_extra_hosts.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_trusted_extra_hosts.py:37: in <module>
    from tests.server.conftest import IPCServer, MockApp  # noqa: E402
E   ImportError: cannot import name 'IPCServer' from 'tests.server.conftest' (/Users/runner/work/voice-typer/voice-typer/tests/server/conftest.py)
```

### 64. `tests.test_update_check`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_update_check.py:44`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_update_check.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_update_check.py:44: in <module>
    from voice_typer.server.service import update_check
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 65. `tests.test_vocabulary_backend_duplicates`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_vocabulary_backend_duplicates.py:22`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_vocabulary_backend_duplicates.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_vocabulary_backend_duplicates.py:22: in <module>
    from voice_typer.server.service.vocabulary import (
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 66. `tests.test_vocabulary_delete_persistence`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/test_vocabulary_delete_persistence.py:33`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_vocabulary_delete_persistence.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_vocabulary_delete_persistence.py:33: in <module>
    from voice_typer.server.service.vocabulary import VocabularyMixin
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 67. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_does_not_swallow_system_exit`

- Legs: macos-14-3.10, windows-2022-3.10
- Location: `tests/app/test_lifecycle.py:1648`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
tests/app/test_lifecycle.py:1648: in test_main_does_not_swallow_system_exit
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 68. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_warning_when_faulthandler_enable_raises`

- Legs: macos-14-3.10
- Location: `tests/app/test_lifecycle.py:1690`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
tests/app/test_lifecycle.py:1690: in test_main_logs_warning_when_faulthandler_enable_raises
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 69. `pytest.internal`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13, windows-2022-3.10
- Location: `(pytest internal error — no test location)`

```
internal error

internal error
def worker_internal_error(
        self, node: WorkerController, formatted_error: str
    ) -> None:
        """
        pytest_internalerror() was called on the worker.
    
        pytest_internalerror() arguments are an excinfo and an excrepr, which can't
        be serialized, so we go with a poor man's solution of raising an exception
        here ourselves using the formatted message.
        """
        self._active_nodes.remove(node)
        try:
>           assert False, formatted_error
E           AssertionError: Traceback (most recent call last):
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/_pytest/main.py", line 289, in wrap_session
E                 session.exitstatus = doit(config, session) or 0
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/_pytest/main.py", line 343, in _main
E                 config.hook.pytest_runtestloop(session=session)
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_hooks.py", line 512, in __call__
E                 return self._hookexec(self.name, self._hookimpls.copy(), kwargs, firstresult)
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_manager.py", line 120, in _hookexec
E                 return self._inner_hookexec(hook_name, methods, kwargs, firstresult)
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_callers.py", line 167, in _multicall
E                 raise exception
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_callers.py", line 139, in _multicall
E                 teardown.throw(exception)
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/_pytest/logging.py", line 801, in pytest_runtestloop
E                 return (yield)  # Run all the tests.
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_callers.py", line 139, in _multicall
E                 teardown.throw(exception)
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/_pytest/terminal.py", line 688, in pytest_runtestloop
E                 result = yield
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_callers.py", line 139, in _multicall
E                 teardown.throw(exception)
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pytest_cov/plugin.py", line 348, in pytest_runtestloop
E                 result = yield
E               File "/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/pluggy/_callers.py", line 121, in _multicall
```

### 70. `tests.tauri.mig16.test_externalbin_spawn_macos.test_spawn_rs_server_started_log_line_format`

- Legs: macos-14-3.12
- Location: `tests/tauri/mig16/test_externalbin_spawn_macos.py:529`

```
+    where <built-in method search of re.Pattern object at 0x143ecaf90> = re.compile('\\[SIDECAR\\]\\s*server_started\\s*port=\\{[^}]*\\}').search

AssertionError: spawn.rs must log '[SIDECAR] server_started port={}' on success (runbook §5 pass criteria greps for this line on macOS)
assert None
 +  where None = <built-in method search of re.Pattern object at 0x143ecaf90>('//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).\n//!\n//! Module layout (split out of the former single-file module):\n//!\n//! - `self` — orchestration: the public spawn entry points\n//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release\n//!   dispatch (`spawn_sidecar_and_get_port_inner`), the cold-start\n//!   wiring (`initialize_sidecar`), and the panic-captured background\n//!   task body (`initialize_sidecar_guarded`) that `main.rs`\'s\n//!   `.setup` spawns.\n//! - [`dev_mode`] — `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn\n//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).\n//! - [`release_mode`] — release-build `externalBin` spawn\n//!   (`spawn_sidecar_release`).\n//! - [`handshake`] — `server_started` stdout parsing\n//!   (`parse_server_started`) + the shutting-down loop short-circuit\n//!   (`is_shutting_down`).\n//! - [`handshake_loop`] — the shared stdout-handshake read loops used by\n//!   all four spawn paths (`spawn_sidecar_release` /\n//!   `spawn_sidecar_dev_mode` / `spawn_worker_release` /\n//!   `spawn_worker_dev_mode`): one loop body for the shell-plugin\n//!   `CommandEvent` pair, one for the tokio `read_li...sync move {\n        let state = app_handle\n            .state::<Arc<WorkerState>>()\n            .inner()\n            .clone();\n        if !try_claim_restart_slot(&state.respawn_in_progress) {\n            log::info!(\n                "[WORKER-INIT] pack verified while a worker (re)start is in flight — skipping duplicate"\n            );\n            return;\n        }\n        // Stop-first: the verified event fires right after the\n        // atomic swap, so a still-running worker may hold the OLD\n        // pack files open (Windows file-lock swap failure).\n        stop_worker_child(&state).await;\n        if state.shutting_down.load(Ordering::SeqCst) {\n            state.respawn_in_progress.store(false, Ordering::SeqCst);\n            return;\n        }\n        if !worker_binary_present() {\n            log::info!(\n                "[WORKER-INIT] pack verified but no worker binary on disk — skipping worker start"\n            );\n            state.respawn_in_progress.store(false, Ordering::SeqCst);\n            return;\n        }\n        initialize_worker(&app_handle, state.clone()).await;\n        state.respawn_in_progress.store(false, Ordering::SeqCst);\n    });\n}\n')
 +    where <built-in method search of re.Pattern object at 0x143ecaf90> = re.compile('\\[SIDECAR\\]\\s*server_started\\s*port=\\{[^}]*\\}').search
tests/tauri/mig16/test_externalbin_spawn_macos.py:529: in test_spawn_rs_server_started_log_line_format
    assert port_log_re.search(spawn_rs_source), (
E   AssertionError: spawn.rs must log '[SIDECAR] server_started port={}' on success (runbook §5 pass criteria greps for this line on macOS)
E   assert None
E    +  where None = <built-in method search of re.Pattern object at 0x143ecaf90>('//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).\n//!\n//! Module layout (split out of the former single-file module):\n//!\n//! - `self` — orchestration: the public spawn entry points\n//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release\n//!   dispatch (`spawn_sidecar_and_get_port_inner`), the cold-start\n//!   wiring (`initialize_sidecar`), and the panic-captured background\n//!   task body (`initialize_sidecar_guarded`) that `main.rs`\'s\n//!   `.setup` spawns.\n//! - [`dev_mode`] — `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn\n//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).\n//! - [`release_mode`] — release-build `extern
… (truncated)
```

### 71. `tests.tauri.mig16.test_faster_whisper_macos.test_model_path_resolves_to_library_application_support_on_macos`

- Legs: macos-14-3.12
- Location: `tests/tauri/mig16/test_faster_whisper_macos.py:318`

```
assert PosixPath('/Users/runner/.voice-typer/models') == PosixPath('/Users/runner/Library/Application Support/voice-typer/models')

AssertionError: model path on macOS must resolve to ~/Library/Application Support/voice-typer/models (got: /Users/runner/.voice-typer/models, expected: /Users/runner/Library/Application Support/voice-typer/models)
assert PosixPath('/Users/runner/.voice-typer/models') == PosixPath('/Users/runner/Library/Application Support/voice-typer/models')
tests/tauri/mig16/test_faster_whisper_macos.py:318: in test_model_path_resolves_to_library_application_support_on_macos
    assert models_dir == expected, (
E   AssertionError: model path on macOS must resolve to ~/Library/Application Support/voice-typer/models (got: /Users/runner/.voice-typer/models, expected: /Users/runner/Library/Application Support/voice-typer/models)
E   assert PosixPath('/Users/runner/.voice-typer/models') == PosixPath('/Users/runner/Library/Application Support/voice-typer/models')
```

### 72. `tests.tauri.mig16.test_shutdown_macos.TestSupervisorSource.test_returns_ok_on_successful_respawn`

- Legs: macos-14-3.12
- Location: `tests/tauri/mig16/test_shutdown_macos.py:811`

```
assert (35061 - 32012) < 2000

AssertionError: `return Ok(())` after 'respawn succeeded' log must be in the same match arm (within 400 chars); gap was 3049 chars — the supervisor must return immediately on successful reconnect_ws (reset-on-success: the loop exits early, the next crash starts a fresh backoff schedule)
assert (35061 - 32012) < 2000
tests/tauri/mig16/test_shutdown_macos.py:811: in test_returns_ok_on_successful_respawn
    assert idx_return - idx_log < 2000, (
E   AssertionError: `return Ok(())` after 'respawn succeeded' log must be in the same match arm (within 400 chars); gap was 3049 chars — the supervisor must return immediately on successful reconnect_ws (reset-on-success: the loop exits early, the next crash starts a fresh backoff schedule)
E   assert (35061 - 32012) < 2000
```

### 73. `tests.test_single_instance.TestFlockAcquiredAfterStaleOExcl.test_flock_failure_ewouldblock_exits_with_pid_diagnostic`

- Legs: macos-14-3.12
- Location: `tests/test_single_instance.py:183`

```
Failed: DID NOT RAISE <class 'SystemExit'>

Failed: DID NOT RAISE <class 'SystemExit'>
tests/test_single_instance.py:183: in test_flock_failure_ewouldblock_exits_with_pid_diagnostic
    with pytest.raises(SystemExit) as exc_info:
         ^^^^^^^^^^^^^^^^^^^^^^^^^
E   Failed: DID NOT RAISE <class 'SystemExit'>
```

### 74. `tests.test_single_instance.TestFlockAcquiredAfterStaleOExcl.test_flock_succeeds_when_previous_holder_dead`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:141: in test_flock_succeeds_when_previous_holder_dead
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 75. `tests.test_single_instance.TestPosixSingleInstanceHandleRelease.test_handle_is_int_subclass`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:248: in test_handle_is_int_subclass
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 76. `tests.test_single_instance.TestFlockAcquiredAfterStaleOExcl.test_no_pid_liveness_check_before_flock`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:220: in test_no_pid_liveness_check_before_flock
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 77. `tests.test_single_instance.TestPosixSingleInstanceHandleRelease.test_release_closes_fd`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:259: in test_release_closes_fd
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 78. `tests.test_single_instance.TestPosixSingleInstanceHandleRelease.test_release_unlinks_lockfile`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:273: in test_release_unlinks_lockfile
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 79. `tests.test_single_instance.TestPosixSingleInstanceHandleRelease.test_release_is_idempotent`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:284: in test_release_is_idempotent
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 80. `tests.test_single_instance.TestPosixSingleInstanceHandleRelease.test_release_safe_after_manual_os_close`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:296: in test_release_safe_after_manual_os_close
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 81. `tests.test_single_instance.TestFlockAcquiredAfterStaleOExcl.test_lockfile_pid_refreshed_after_flock_reclaim`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance.py:161: in test_lockfile_pid_refreshed_after_flock_reclaim
    handle = si_mod._ensure_single_instance_posix(silent=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 82. `tests.test_single_instance_posix.TestFirstInstanceAcquiresLock.test_returns_int_fd`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:140: in test_returns_int_fd
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 83. `tests.test_single_instance_chmod.TestConfigDirChmod.test_config_dir_mode_is_0o700_on_creation`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_chmod.py:103: in test_config_dir_mode_is_0o700_on_creation
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 84. `tests.test_single_instance_posix.TestFirstInstanceAcquiresLock.test_creates_backend_lock_file`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:150: in test_creates_backend_lock_file
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 85. `tests.test_single_instance_posix.TestFirstInstanceAcquiresLock.test_writes_backend_pid_file`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:174: in test_writes_backend_pid_file
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 86. `tests.test_single_instance_posix.TestFirstInstanceAcquiresLock.test_writes_our_pid_into_lock_file`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:160: in test_writes_our_pid_into_lock_file
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 87. `tests.test_single_instance_posix.TestFirstInstanceAcquiresLock.test_lockfile_permissions_are_restricted`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:190: in test_lockfile_permissions_are_restricted
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 88. `tests.test_single_instance_posix.TestStaleLockRecovery.test_reclaims_stale_lock`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:306: in test_reclaims_stale_lock
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 89. `tests.test_single_instance_posix.TestStaleLockRecovery.test_writes_backend_pid_file_after_recovery`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:335: in test_writes_backend_pid_file_after_recovery
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 90. `tests.test_single_instance_posix.TestStaleLockRecovery.test_overwrites_stale_pid_with_our_pid`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:320: in test_overwrites_stale_pid_with_our_pid
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 91. `tests.test_single_instance_posix.TestStaleLockRecovery.test_empty_pid_treated_as_stale`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:364: in test_empty_pid_treated_as_stale
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 92. `tests.test_single_instance_posix.TestStaleLockRecovery.test_garbage_pid_treated_as_stale`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_posix.py:349: in test_garbage_pid_treated_as_stale
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 93. `tests.test_single_instance_chmod.TestConfigDirChmod.test_config_dir_chmod_tightens_existing_loose_perms`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_chmod.py:134: in test_config_dir_chmod_tightens_existing_loose_perms
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 94. `tests.test_single_instance_chmod.TestNoFollowSymlink.test_normal_lockfile_creation_succeeds`

- Legs: macos-14-3.12
- Location: `voice_typer/server/single_instance.py:800`

```
SystemExit: 1

SystemExit: 1
voice_typer/server/single_instance.py:800: in _ensure_single_instance_posix
    fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
E   BlockingIOError: [Errno 35] Resource temporarily unavailable

During handling of the above exception, another exception occurred:
tests/test_single_instance_chmod.py:257: in test_normal_lockfile_creation_succeeds
    fd = si_mod._ensure_single_instance_posix(silent=True)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
voice_typer/server/single_instance.py:829: in _ensure_single_instance_posix
    sys.exit(1)
E   SystemExit: 1
```

### 95. `tests.app.test_config_wiring.TestSettingsWindowIntegration.test_restart_app_does_not_spawn_subprocess`

- Legs: windows-2022-3.10, windows-2022-3.12
- Location: `tests/app/test_config_wiring.py:369`

```
#x1B[92m+ ]#x1B[39;49;00m#x1B[90m#x1B[39;49;00m

AssertionError: restart_app must NOT spawn a replacement backend/Electron subprocess (port-race); got: [((['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'Add-Type -TypeDefinition @\'\nusing System;\nusing System.Runtime.InteropServices;\nusing System.Text;\npublic static class LnkAumid {\n    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]\n    private class CShellLink { }\n    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n    private interface IShellLinkW {\n        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);\n        void GetIDList(out IntPtr ppidl);\n        void SetIDList(IntPtr pidl);\n        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);\n        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);\n        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);\n        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);\n        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);\n        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);\n        void GetHotkey(out short pwHotkey);\n        void SetHotkey(short wHotkey);\n        void GetShowCmd(out int piShowCmd);\n        void SetShowCmd(int iShowCmd);\n        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);\n        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);\n        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);\n        void Resolve(IntPtr hwnd, uint fFlags);\n        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);\n    }\n    [ComImport, Guid("0000010B-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n    private interface IPersistFile {\n        void GetClassID(out Guid pClassID);\n        int IsDirty();\n        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, int dwMode);\n        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);\n        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);\n        void GetCurFile(out IntPtr ppszFileName);\n    }\n    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n    private interface IPropertyStore {\n        [PreserveSig] int GetCount(out uint cProps);\n        [PreserveSig] int GetAt(uint iProp, out PROPERTYKEY pkey);\n        [PreserveSig] int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);\n        [PreserveSig] int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);\n        [PreserveSig] int Commit();\n    }\n    [StructLayout(LayoutKind.Sequential)]\n    private struct PROPERTYKEY { public Guid fmtid; public int pid; }\n    [StructLayout(LayoutKind.Sequential)]\n    private struct PROPVARIANT {\n        public ushort vt;\n        public ushort wReserved1, wReserved2, wReserved3;\n        public IntPtr p;\n    }\n    public static int Set(string path, string target, string arguments, string workingDir, string description, string iconPath, string aumid) {\n        object link = new CShellLink();\n        IShellLinkW sl = (IShellLinkW)link;\n        sl.SetPath(target);\n        if (!string.IsNullOrEmpty(arguments)) sl.SetArguments(arguments);\n        if (!string.IsNullOrEmpty(workingDir)) sl.SetWorkingDirectory(workingDir);\n        if (!string.IsNullOrEmpty(description)) sl.SetDescription(description);\n        if (!string.IsNullOrEmpty(iconPath)) sl.SetIconLocation(iconPath, 0);\n        IPropertyStore ps = (IPropertyStore)link
… (truncated)
```

### 96. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_and_exits_when_ipc_main_raises`

- Legs: windows-2022-3.10
- Location: `tests/app/test_lifecycle.py:1606`

```
ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)
tests\app\test_lifecycle.py:1606: in test_main_logs_and_exits_when_ipc_main_raises
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer\server\ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer\server\handlers\__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer\server\handlers\vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer\server\service\__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer\server\service\model\__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer\server\service\model\mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer\server\service\model\_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer\server\service\_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)
```

### 97. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_warning_when_faulthandler_import_fails`

- Legs: windows-2022-3.10
- Location: `tests/app/test_lifecycle.py:1721`

```
ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)
tests\app\test_lifecycle.py:1721: in test_main_logs_warning_when_faulthandler_import_fails
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer\server\ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer\server\handlers\__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer\server\handlers\vocabulary_handlers.py:16: in <module>
    from voice_typer.server.service.vocabulary import VocabularyDuplicateError
voice_typer\server\service\__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer\server\service\model\__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer\server\service\model\mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer\server\service\model\_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer\server\service\_download_helpers.py:37: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)
```

### 98. `tests.test_ipc_shutdown_registry.TestShutdownCommandRegistry.test_handle_shutdown_returns_ack_even_if_service_quit_raises`

- Legs: windows-2022-3.12
- Location: `tests/test_ipc_shutdown_registry.py:170`

```
AssertionError: Expected 'quit' to be called once. Called 0 times.

AssertionError: Expected 'quit' to be called once. Called 0 times.
tests\test_ipc_shutdown_registry.py:170: in test_handle_shutdown_returns_ack_even_if_service_quit_raises
    service.quit.assert_called_once_with()
C:\hostedtoolcache\windows\Python\3.12.10\x64\Lib\unittest\mock.py:960: in assert_called_once_with
    raise AssertionError(msg)
E   AssertionError: Expected 'quit' to be called once. Called 0 times.
```

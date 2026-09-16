# CI Errors

> Auto-generated from the latest GitHub Actions run via `scripts/ci/write_ci_errors.py`. Do not edit by hand, it is overwritten on every CI run.

**81 failing/errored test(s)** across 11 matrix leg(s).

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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 14. `tests.test_di_providers`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 15. `tests.test_download_model_dispatcher_structure`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 16. `tests.test_download_model_return_shape`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 17. `tests.test_download_progress_events`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 18. `tests.test_e2e_pipeline`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 19. `tests.test_heartbeat`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 20. `tests.test_heartbeat_force_exit`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 21. `tests.test_ipc_deadlock_regression`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 22. `tests.test_ipc_dispatch_errors`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 23. `tests.test_ipc_error_envelope_helper`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 24. `tests.test_ipc_error_envelope_parity`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 25. `tests.test_ipc_no_client_log_redaction`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 26. `tests.test_ipc_pending_tcp_remerge`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 27. `tests.test_ipc_rate_limiter_dual_window`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 28. `tests.test_ipc_send_shutdown_allowlist`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 29. `tests.test_ipc_sender_select`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 30. `tests.test_ipc_server`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 31. `tests.test_ipc_server_main_diagnostics`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 32. `tests.test_ipc_shutdown_registry`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 33. `tests.test_ipc_tray_click_validation`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 34. `tests.test_keyboard_ownership_watchdog`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 35. `tests.test_module_constant_hoist`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 36. `tests.test_pack_atomic_swap`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 37. `tests.test_pack_checksum_background`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 38. `tests.test_pack_consent_gate`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 39. `tests.test_pack_corruption_recovery`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 40. `tests.test_pack_disk_full_during_download`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 41. `tests.test_pack_disk_space_check`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 42. `tests.test_pack_download_resume`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 43. `tests.test_pack_dual_instance`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 44. `tests.test_pack_fallback_dir`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 45. `tests.test_pack_github_rate_limit`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 46. `tests.test_pack_install`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 47. `tests.test_pack_missing_on_launch`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 48. `tests.test_pack_proxy`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 49. `tests.test_pack_schema_caps`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 50. `tests.test_pack_signing`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 51. `tests.test_pack_version_change_during_download`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 52. `tests.test_privacy_helpers`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 53. `tests.test_segmented_progress_tracker`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
- Location: `tests/test_segmented_progress_tracker.py:22`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_segmented_progress_tracker.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_segmented_progress_tracker.py:22: in <module>
    from voice_typer.server.service._download_helpers import (
voice_typer/server/service/__init__.py:42: in <module>
    from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
voice_typer/server/service/model/__init__.py:7: in <module>
    from .mixin import ModelMixin
voice_typer/server/service/model/mixin.py:7: in <module>
    from ._downloads import DownloadsMixin
voice_typer/server/service/model/_downloads.py:11: in <module>
    from voice_typer.server.service._download_helpers import DownloadOutcome
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 54. `tests.test_sender_select_timeout`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 55. `tests.test_service_download_consent`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 56. `tests.test_service_llm_consent`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 57. `tests.test_sidecar_ready_emitted`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 58. `tests.test_sidecar_ws_ready_ordering`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 59. `tests.test_startup_error_log_cap`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 60. `tests.test_tcp_dispatch_concurrency`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 61. `tests.test_tcp_drain_batching`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 62. `tests.test_tcp_idle_read_timeout`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 63. `tests.test_transport_write_raw`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 64. `tests.test_trusted_extra_hosts`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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

### 65. `tests.test_update_check`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 66. `tests.test_vocabulary_backend_duplicates`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 67. `tests.test_vocabulary_delete_persistence`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 68. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_does_not_swallow_system_exit`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 69. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_warning_when_faulthandler_enable_raises`

- Legs: macos-14-3.10, ubuntu-22.04-3.10, windows-2022-3.10
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
voice_typer/server/service/_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
```

### 70. `pytest.internal`

- Legs: macos-14-3.10, macos-14-3.11, ubuntu-22.04-3.10, ubuntu-22.04-3.11, windows-2022-3.10
- Location: `(pytest internal error, no test location)`

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

### 71. `tests.tauri.mig16.test_externalbin_spawn_macos.test_spawn_rs_server_started_log_line_format`

- Legs: macos-14-3.11, macos-14-3.13
- Location: `tests/tauri/mig16/test_externalbin_spawn_macos.py:529`

```
+    where <built-in method search of re.Pattern object at 0x13b59d750> = re.compile('\\[SIDECAR\\]\\s*server_started\\s*port=\\{[^}]*\\}').search

AssertionError: spawn.rs must log '[SIDECAR] server_started port={}' on success (runbook §5 pass criteria greps for this line on macOS)
assert None
 +  where None = <built-in method search of re.Pattern object at 0x13b59d750>('//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).\n//!\n//! Module layout (split out of the former single-file module):\n//!\n//! - `self`: orchestration: the public spawn entry points\n//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release\n//!   dispatch (`spawn_sidecar_and_get_port_inner`), the cold-start\n//!   wiring (`initialize_sidecar`), and the panic-captured background\n//!   task body (`initialize_sidecar_guarded`) that `main.rs`\'s\n//!   `.setup` spawns.\n//! - [`dev_mode`]: `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn\n//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).\n//! - [`release_mode`]: release-build `externalBin` spawn\n//!   (`spawn_sidecar_release`).\n//! - [`handshake`]: `server_started` stdout parsing\n//!   (`parse_server_started`) + the shutting-down loop short-circuit\n//!   (`is_shutting_down`).\n//! - [`handshake_loop`]: the shared stdout-handshake read loops used by\n//!   all four spawn paths (`spawn_sidecar_release` /\n//!   `spawn_sidecar_dev_mode` / `spawn_worker_release` /\n//!   `spawn_worker_dev_mode`): one loop body for the shell-plugin\n//!   `CommandEvent` pair, one for the tokio `read_line` p....clone();\n    tauri::async_runtime::spawn(async move {\n        let state = app_handle.state::<Arc<WorkerState>>().inner().clone();\n        if !try_claim_restart_slot(&state.respawn_in_progress) {\n            log::info!(\n                "[WORKER-INIT] pack verified while a worker (re)start is in flight: skipping duplicate"\n            );\n            return;\n        }\n        // Stop-first: the verified event fires right after the\n        // atomic swap, so a still-running worker may hold the OLD\n        // pack files open (Windows file-lock swap failure).\n        stop_worker_child(&state).await;\n        if state.shutting_down.load(Ordering::SeqCst) {\n            state.respawn_in_progress.store(false, Ordering::SeqCst);\n            return;\n        }\n        if !worker_binary_present() {\n            log::info!(\n                "[WORKER-INIT] pack verified but no worker binary on disk: skipping worker start"\n            );\n            state.respawn_in_progress.store(false, Ordering::SeqCst);\n            return;\n        }\n        initialize_worker(&app_handle, state.clone()).await;\n        state.respawn_in_progress.store(false, Ordering::SeqCst);\n    });\n}\n')
 +    where <built-in method search of re.Pattern object at 0x13b59d750> = re.compile('\\[SIDECAR\\]\\s*server_started\\s*port=\\{[^}]*\\}').search
tests/tauri/mig16/test_externalbin_spawn_macos.py:529: in test_spawn_rs_server_started_log_line_format
    assert port_log_re.search(spawn_rs_source), (
E   AssertionError: spawn.rs must log '[SIDECAR] server_started port={}' on success (runbook §5 pass criteria greps for this line on macOS)
E   assert None
E    +  where None = <built-in method search of re.Pattern object at 0x13b59d750>('//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).\n//!\n//! Module layout (split out of the former single-file module):\n//!\n//! - `self`: orchestration: the public spawn entry points\n//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release\n//!   dispatch (`spawn_sidecar_and_get_port_inner`), the cold-start\n//!   wiring (`initialize_sidecar`), and the panic-captured background\n//!   task body (`initialize_sidecar_guarded`) that `main.rs`\'s\n//!   `.setup` spawns.\n//! - [`dev_mode`]: `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn\n//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).\n//! - [`release_mode`]: release-build `externalB
… (truncated)
```

### 72. `tests.tauri.mig16.test_shutdown_macos.TestSupervisorSource.test_returns_ok_on_successful_respawn`

- Legs: macos-14-3.11, macos-14-3.13
- Location: `tests/tauri/mig16/test_shutdown_macos.py:811`

```
assert (34982 - 31937) < 2000

AssertionError: `return Ok(())` after 'respawn succeeded' log must be in the same match arm (within 400 chars); gap was 3045 chars, the supervisor must return immediately on successful reconnect_ws (reset-on-success: the loop exits early, the next crash starts a fresh backoff schedule)
assert (34982 - 31937) < 2000
tests/tauri/mig16/test_shutdown_macos.py:811: in test_returns_ok_on_successful_respawn
    assert idx_return - idx_log < 2000, (
E   AssertionError: `return Ok(())` after 'respawn succeeded' log must be in the same match arm (within 400 chars); gap was 3045 chars, the supervisor must return immediately on successful reconnect_ws (reset-on-success: the loop exits early, the next crash starts a fresh backoff schedule)
E   assert (34982 - 31937) < 2000
```

### 73. `tests.test_console_flash_hidden_spawn.TestDesktopShortcutPowershellHidden.test_lnk_powershell_fallback_passes_create_no_window`

- Legs: macos-14-3.11, macos-14-3.13, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13, windows-2022-3.11, windows-2022-3.12, windows-2022-3.13
- Location: `tests/test_console_flash_hidden_spawn.py:212`

```
ModuleNotFoundError: No module named 'win32com'

ModuleNotFoundError: No module named 'win32com'
tests/test_console_flash_hidden_spawn.py:212: in test_lnk_powershell_fallback_passes_create_no_window
    monkeypatch.setattr("win32com.client.Dispatch", None, raising=False)
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/_pytest/monkeypatch.py:102: in derive_importpath
    target = resolve(module)
             ^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/_pytest/monkeypatch.py:65: in resolve
    found: object = __import__(used)
                    ^^^^^^^^^^^^^^^^
E   ModuleNotFoundError: No module named 'win32com'
```

### 74. `tests.test_dictation_pipeline_check_resources.TestCheckResourcesXZEH008SilentExcept.test_ram_ctypes_fallback_failure_logs_debug`

- Legs: macos-14-3.13, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_dictation_pipeline_check_resources.py:515`

```
TypeError: statvfs: path should be string, bytes, os.PathLike or integer, not _StubPath

TypeError: statvfs: path should be string, bytes, os.PathLike or integer, not _StubPath
tests/test_dictation_pipeline_check_resources.py:515: in test_ram_ctypes_fallback_failure_logs_debug
    pipeline._check_resources()
voice_typer/server/dictation_pipeline/transcribe_step.py:176: in _check_resources
    check_resources(logger=log)
voice_typer/server/resource_probe.py:300: in check_resources
    drive_info = os.statvfs(path) if hasattr(os, "statvfs") else None
                 ^^^^^^^^^^^^^^^^
E   TypeError: statvfs: path should be string, bytes, os.PathLike or integer, not _StubPath
```

### 75. `tests.test_level_monitor_worker_lifecycle.TestThreadRegistryRegistration.test_idle_exit_unregisters_level_worker`

- Legs: macos-14-3.13, ubuntu-22.04-3.12, ubuntu-22.04-3.13, windows-2022-3.11, windows-2022-3.12, windows-2022-3.13
- Location: `tests/test_level_monitor_worker_lifecycle.py:216`

```
+    where is_alive = <Thread(level-monitor-worker, started daemon 14163685376)>.is_alive

assert not True
 +  where True = is_alive()
 +    where is_alive = <Thread(level-monitor-worker, started daemon 14163685376)>.is_alive
tests/test_level_monitor_worker_lifecycle.py:216: in test_idle_exit_unregisters_level_worker
    assert not thread.is_alive()
E   assert not True
E    +  where True = is_alive()
E    +    where is_alive = <Thread(level-monitor-worker, started daemon 14163685376)>.is_alive
```

### 76. `tests.test_logging_rotation_perms.test_do_rollover_chmod_runs_inside_lock`

- Legs: macos-14-3.13, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_logging_rotation_perms.py:338`

```
+    where <built-in method index of list object at 0x142488900> = ['release', 'chmod', 'release'].index

AssertionError: chmod must run BEFORE release; got call_order=['release', 'chmod', 'release']
assert 1 < 0
 +  where 1 = <built-in method index of list object at 0x142488900>('chmod')
 +    where <built-in method index of list object at 0x142488900> = ['release', 'chmod', 'release'].index
 +  and   0 = <built-in method index of list object at 0x142488900>('release')
 +    where <built-in method index of list object at 0x142488900> = ['release', 'chmod', 'release'].index
tests/test_logging_rotation_perms.py:338: in test_do_rollover_chmod_runs_inside_lock
    assert call_order.index("chmod") < call_order.index("release"), (
E   AssertionError: chmod must run BEFORE release; got call_order=['release', 'chmod', 'release']
E   assert 1 < 0
E    +  where 1 = <built-in method index of list object at 0x142488900>('chmod')
E    +    where <built-in method index of list object at 0x142488900> = ['release', 'chmod', 'release'].index
E    +  and   0 = <built-in method index of list object at 0x142488900>('release')
E    +    where <built-in method index of list object at 0x142488900> = ['release', 'chmod', 'release'].index
```

### 77. `tests.test_install_permissions_gsettings.TestSwayFlow.test_appends_block_when_no_existing_line`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_install_permissions_gsettings.py:430`

```
AssertionError: assert '# Voice Typer — Caps Lock neutralization' in 'set $mod Mod4\nbindsym Mod4+Return exec foot\n\n# Voice Typer. Caps Lock neutralization\ninput * xkb_options caps:none\n'

AssertionError: assert '# Voice Typer — Caps Lock neutralization' in 'set $mod Mod4\nbindsym Mod4+Return exec foot\n\n# Voice Typer. Caps Lock neutralization\ninput * xkb_options caps:none\n'
tests/test_install_permissions_gsettings.py:430: in test_appends_block_when_no_existing_line
    assert "# Voice Typer — Caps Lock neutralization" in new_text
E   AssertionError: assert '# Voice Typer — Caps Lock neutralization' in 'set $mod Mod4\nbindsym Mod4+Return exec foot\n\n# Voice Typer. Caps Lock neutralization\ninput * xkb_options caps:none\n'
```

### 78. `tests.test_install_permissions_gsettings.TestUninstallRestore.test_sway_restore_removes_block_when_no_prior_line`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_install_permissions_gsettings.py:607`

```
bindsym Mod4+Return exec foot

AssertionError: assert '# Voice Typ...utralization' not in 'set $mod Mo... exec foot\n'
  
  '# Voice Typer — Caps Lock neutralization' is contained here:
    set $mod Mod4
    # Voice Typer — Caps Lock neutralization
    bindsym Mod4+Return exec foot
tests/test_install_permissions_gsettings.py:607: in test_sway_restore_removes_block_when_no_prior_line
    assert "# Voice Typer — Caps Lock neutralization" not in new_text
E   AssertionError: assert '# Voice Typ...utralization' not in 'set $mod Mo... exec foot\n'
E     
E     '# Voice Typer — Caps Lock neutralization' is contained here:
E       set $mod Mod4
E       # Voice Typer — Caps Lock neutralization
E       bindsym Mod4+Return exec foot
```

### 79. `tests.test_mic_test_quality_grading.TestPersistFailureEnvelope.test_write_failure_returns_quality_without_raising`

- Legs: ubuntu-22.04-3.13
- Location: `tests/test_mic_test_quality_grading.py:291`

```
+ very_low

AssertionError: assert 'very_low' == 'good'
  
  - good
  + very_low
tests/test_mic_test_quality_grading.py:291: in test_write_failure_returns_quality_without_raising
    assert result["quality"]["volume_level"] == "good"
E   AssertionError: assert 'very_low' == 'good'
E     
E     - good
E     + very_low
```

### 80. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_and_exits_when_ipc_main_raises`

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
voice_typer\server\service\_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)
```

### 81. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_warning_when_faulthandler_import_fails`

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
voice_typer\server\service\_download_helpers.py:39: in <module>
    from typing import NotRequired, TypedDict
E   ImportError: cannot import name 'NotRequired' from 'typing' (C:\hostedtoolcache\windows\Python\3.10.11\x64\lib\typing.py)
```

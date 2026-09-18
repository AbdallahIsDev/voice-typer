# CI Errors

> Auto-generated from the latest GitHub Actions run via `scripts/ci/write_ci_errors.py`. Do not edit by hand, it is overwritten on every CI run.

**100 failing/errored test(s)** across 7 matrix leg(s).

### 1. `tests.handlers.test_handler_signature_conformance`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
- Location: `tests/handlers/test_handler_signature_conformance.py:50`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/handlers/test_handler_signature_conformance.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/handlers/test_handler_signature_conformance.py:50: in <module>
    import voice_typer.server.handlers as _handlers_pkg
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

- Legs: macos-14-3.10, ubuntu-22.04-3.10
- Location: `tests/regressions/test_cli_exit_codes.py:39`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/regressions/test_cli_exit_codes.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/regressions/test_cli_exit_codes.py:39: in <module>
    from voice_typer.server import ipc_server
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 4. `tests.server`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 5. `tests.service.test_status_volume_cache`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 6. `tests.test_cloud_connection_ipc_wiring`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 7. `tests.test_cloud_provider_map_single_source`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 8. `tests.test_cloud_test_handlers_redirect`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 9. `tests.test_dead_code_stays_removed`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 10. `tests.test_di_providers`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 11. `tests.test_download_model_dispatcher_structure`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 12. `tests.test_download_model_return_shape`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 13. `tests.test_download_progress_events`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 14. `tests.test_heartbeat`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
- Location: `tests/test_heartbeat.py:52`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_heartbeat.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_heartbeat.py:52: in <module>
    from voice_typer.server.ipc_server import (
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 15. `tests.test_heartbeat_force_exit`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 16. `tests.test_ipc_deadlock_regression`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 17. `tests.test_ipc_no_client_log_redaction`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 18. `tests.test_ipc_rate_limiter_dual_window`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 19. `tests.test_ipc_send_shutdown_allowlist`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 20. `tests.test_ipc_sender_select`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 21. `tests.test_ipc_server`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 22. `tests.test_ipc_server_main_diagnostics`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 23. `tests.test_ipc_shutdown_registry`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 24. `tests.test_ipc_tray_click_validation`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 25. `tests.test_keyboard_ownership_watchdog`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
- Location: `tests/test_keyboard_ownership_watchdog.py:24`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

collection failure
ImportError while importing test module '/Users/runner/work/voice-typer/voice-typer/tests/test_keyboard_ownership_watchdog.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_keyboard_ownership_watchdog.py:24: in <module>
    from voice_typer.server.ipc_server import IPCServer
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 26. `tests.test_module_constant_hoist`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 27. `tests.test_pack_atomic_swap`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 28. `tests.test_pack_checksum_background`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 29. `tests.test_pack_consent_gate`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 30. `tests.test_pack_corruption_recovery`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 31. `tests.test_pack_disk_full_during_download`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 32. `tests.test_pack_disk_space_check`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 33. `tests.test_pack_download_resume`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 34. `tests.test_pack_dual_instance`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 35. `tests.test_pack_fallback_dir`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 36. `tests.test_pack_github_rate_limit`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 37. `tests.test_pack_install`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 38. `tests.test_pack_missing_on_launch`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 39. `tests.test_pack_proxy`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 40. `tests.test_pack_schema_caps`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 41. `tests.test_pack_signing`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 42. `tests.test_pack_version_change_during_download`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 43. `tests.test_privacy_helpers`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 44. `tests.test_segmented_progress_tracker`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 45. `tests.test_sender_select_timeout`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 46. `tests.test_service_download_consent`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 47. `tests.test_service_llm_consent`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 48. `tests.test_sidecar_ready_emitted`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 49. `tests.test_sidecar_ws_ready_ordering`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 50. `tests.test_startup_error_log_cap`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 51. `tests.test_transport_write_raw`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 52. `tests.test_trusted_extra_hosts`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 53. `tests.test_update_check`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 54. `tests.test_vocabulary_backend_duplicates`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 55. `tests.test_vocabulary_delete_persistence`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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

### 56. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_and_exits_when_ipc_main_raises`

- Legs: macos-14-3.10
- Location: `tests/app/test_lifecycle.py:1606`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
tests/app/test_lifecycle.py:1606: in test_main_logs_and_exits_when_ipc_main_raises
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 57. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_does_not_swallow_system_exit`

- Legs: macos-14-3.10, ubuntu-22.04-3.10
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
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 58. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_warning_when_faulthandler_import_fails`

- Legs: macos-14-3.10
- Location: `tests/app/test_lifecycle.py:1721`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/typing.py)
tests/app/test_lifecycle.py:1721: in test_main_logs_warning_when_faulthandler_import_fails
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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

### 59. `pytest.internal`

- Legs: macos-14-3.10, macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
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

### 60. `tests.tauri.mig16.test_autostart_installer_macos.test_single_instance_plugin_enforced`

- Legs: macos-14-3.11, macos-14-3.12
- Location: `tests/tauri/mig16/test_autostart_installer_macos.py:749`

```
assert ('get_webview_window' in '//! Voice Typer: Tauri v2 host (ADR-0020 implementation).\n//!\n//! Rust shell replacing the Electron main process. Responsibilities:\n//! 1. Spawn the Python sidecar via Tauri\'s `externalBin` mechanism,\n//!    passing `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.\n//! 2. Open a WebSocket client to `ws://127.0.0.1:N` and perform the\n//!    bearer-token auth handshake (`{"type":"auth","token":...}`).\n//! 3. Expose ONE generic `dispatch` command to the webview and\n//!    re-emit server-initiated events as Tauri events.\n//! 4. Run the supervisor (respawn with backoff, then full-app relaunch)\n//!    and coalesce `bubble_level` events to ≤30 Hz.\n//! 5. Single-instance gate runs BEFORE any sidecar init so a second\n//!    launch doesn\'t spawn a zombie sidecar.\n//!\n//! # Cross-platform\n//!\n//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).\n//! - macOS: WKWebView (Safari-based, system).\n//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).\n//!\n//! # Module layout\n//!\n//! Wiring-only (C-ARCH-1): app builder, plugin registration, `.setup`\n//! glue (window bootstrap → `window_bootstrap`, sidecar cold-start\n//! task → `sidecar::spa...tauri::generate_context!())\n        .unwrap_or_else(|e| {\n            eprintln!("[FATAL] tauri build failed: {e:?}");\n            log::error!("[FATAL] tauri build failed: {e:?}");\n            std::process::exit(1);\n        })\n        .run(|app_handle, event| match event {\n            RunEvent::ExitRequested { .. } | RunEvent::Exit => {\n                // Teardown body: `state::on_host_exit`, dedicated thread\n                // + bounded-time `block_on` (see `sidecar::lifecycle`).\n                crate::state::on_host_exit(app_handle);\n            }\n            // macOS Dock-icon activation (Electron `app.on("activate")`\n            // parity, MO-112): macOS keeps the process alive after the\n            // last window is closed (tray / Dock), so a Dock click must\n            // bring the dashboard back instead of doing nothing. The\n            // shared routine recreates the window when it is gone and\n            // otherwise runs the full raise sequence (MO-109).\n            #[cfg(target_os = "macos")]\n            RunEvent::Reopen { .. } => {\n                crate::host_events::show_main_window(app_handle);\n            }\n            _ => {}\n        });\n}\n')

AssertionError: single-instance callback must show + focus the existing main window (second launch → focus first, no duplicate window)
assert ('get_webview_window' in '//! Voice Typer: Tauri v2 host (ADR-0020 implementation).\n//!\n//! Rust shell replacing the Electron main process. Responsibilities:\n//! 1. Spawn the Python sidecar via Tauri\'s `externalBin` mechanism,\n//!    passing `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.\n//! 2. Open a WebSocket client to `ws://127.0.0.1:N` and perform the\n//!    bearer-token auth handshake (`{"type":"auth","token":...}`).\n//! 3. Expose ONE generic `dispatch` command to the webview and\n//!    re-emit server-initiated events as Tauri events.\n//! 4. Run the supervisor (respawn with backoff, then full-app relaunch)\n//!    and coalesce `bubble_level` events to ≤30 Hz.\n//! 5. Single-instance gate runs BEFORE any sidecar init so a second\n//!    launch doesn\'t spawn a zombie sidecar.\n//!\n//! # Cross-platform\n//!\n//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).\n//! - macOS: WKWebView (Safari-based, system).\n//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).\n//!\n//! # Module layout\n//!\n//! Wiring-only (C-ARCH-1): app builder, plugin registration, `.setup`\n//! glue (window bootstrap → `window_bootstrap`, sidecar cold-start\n//! task → `sidecar::spa...tauri::generate_context!())\n        .unwrap_or_else(|e| {\n            eprintln!("[FATAL] tauri build failed: {e:?}");\n            log::error!("[FATAL] tauri build failed: {e:?}");\n            s
… (truncated)
```

### 61. `tests.tauri.mig16.test_externalbin_spawn_macos.test_spawn_rs_server_started_log_line_format`

- Legs: macos-14-3.11, macos-14-3.12
- Location: `tests/tauri/mig16/test_externalbin_spawn_macos.py:529`

```
+    where <built-in method search of re.Pattern object at 0x14a5382a0> = re.compile('\\[SIDECAR\\]\\s*server_started\\s*port=\\{[^}]*\\}').search

AssertionError: spawn.rs must log '[SIDECAR] server_started port={}' on success (runbook §5 pass criteria greps for this line on macOS)
assert None
 +  where None = <built-in method search of re.Pattern object at 0x14a5382a0>('//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).\n//!\n//! Module layout (split out of the former single-file module):\n//!\n//! - `self`: orchestration: the public spawn entry points\n//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release\n//!   dispatch (`spawn_sidecar_and_get_port_inner`), the cold-start\n//!   wiring (`initialize_sidecar`), and the panic-captured background\n//!   task body (`initialize_sidecar_guarded`) that `main.rs`\'s\n//!   `.setup` spawns.\n//! - [`dev_mode`]: `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn\n//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).\n//! - [`release_mode`]: release-build `externalBin` spawn\n//!   (`spawn_sidecar_release`).\n//! - [`handshake`]: `server_started` stdout parsing\n//!   (`parse_server_started`) + the shutting-down loop short-circuit\n//!   (`is_shutting_down`).\n//! - [`handshake_loop`]: the shared stdout-handshake read loops used by\n//!   all four spawn paths (`spawn_sidecar_release` /\n//!   `spawn_sidecar_dev_mode` / `spawn_worker_release` /\n//!   `spawn_worker_dev_mode`): one loop body for the shell-plugin\n//!   `CommandEvent` pair, one for the tokio `read_line` p....clone();\n    tauri::async_runtime::spawn(async move {\n        let state = app_handle.state::<Arc<WorkerState>>().inner().clone();\n        if !try_claim_restart_slot(&state.respawn_in_progress) {\n            log::info!(\n                "[WORKER-INIT] pack verified while a worker (re)start is in flight: skipping duplicate"\n            );\n            return;\n        }\n        // Stop-first: the verified event fires right after the\n        // atomic swap, so a still-running worker may hold the OLD\n        // pack files open (Windows file-lock swap failure).\n        stop_worker_child(&state).await;\n        if state.shutting_down.load(Ordering::SeqCst) {\n            state.respawn_in_progress.store(false, Ordering::SeqCst);\n            return;\n        }\n        if !worker_binary_present() {\n            log::info!(\n                "[WORKER-INIT] pack verified but no worker binary on disk: skipping worker start"\n            );\n            state.respawn_in_progress.store(false, Ordering::SeqCst);\n            return;\n        }\n        initialize_worker(&app_handle, state.clone()).await;\n        state.respawn_in_progress.store(false, Ordering::SeqCst);\n    });\n}\n')
 +    where <built-in method search of re.Pattern object at 0x14a5382a0> = re.compile('\\[SIDECAR\\]\\s*server_started\\s*port=\\{[^}]*\\}').search
tests/tauri/mig16/test_externalbin_spawn_macos.py:529: in test_spawn_rs_server_started_log_line_format
    assert port_log_re.search(spawn_rs_source), (
E   AssertionError: spawn.rs must log '[SIDECAR] server_started port={}' on success (runbook §5 pass criteria greps for this line on macOS)
E   assert None
E    +  where None = <built-in method search of re.Pattern object at 0x14a5382a0>('//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).\n//!\n//! Module layout (split out of the former single-file module):\n//!\n//! - `self`: orchestration: the public spawn entry points\n//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release\n//!   dispatch (`spawn_sidecar_and_get_port_inner`), the cold-start\n//!   wiring (`initialize_sidecar`), and the panic-captured background\n//!   task body (`initialize_sidecar_guarded`) that `main.rs`\'s\n//!   `.setup` spawns.\n//! - [`dev_mode`]: `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn\n//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).\n//! - [`release_mode`]: release-build `externalB
… (truncated)
```

### 62. `tests.tauri.mig16.test_shutdown_macos.TestSupervisorSource.test_returns_ok_on_successful_respawn`

- Legs: macos-14-3.11, macos-14-3.12
- Location: `tests/tauri/mig16/test_shutdown_macos.py:811`

```
assert (40663 - 37618) < 2000

AssertionError: `return Ok(())` after 'respawn succeeded' log must be in the same match arm (within 400 chars); gap was 3045 chars, the supervisor must return immediately on successful reconnect_ws (reset-on-success: the loop exits early, the next crash starts a fresh backoff schedule)
assert (40663 - 37618) < 2000
tests/tauri/mig16/test_shutdown_macos.py:811: in test_returns_ok_on_successful_respawn
    assert idx_return - idx_log < 2000, (
E   AssertionError: `return Ok(())` after 'respawn succeeded' log must be in the same match arm (within 400 chars); gap was 3045 chars, the supervisor must return immediately on successful reconnect_ws (reset-on-success: the loop exits early, the next crash starts a fresh backoff schedule)
E   assert (40663 - 37618) < 2000
```

### 63. `tests.tauri.test_rust_log_file_perms.test_pi7_rust_unit_test_log_file_mode_0o600_passes`

- Legs: macos-14-3.11
- Location: `(unknown location)`

```
(no error line)

failed on setup with "worker 'gw1' crashed while running 'tests/tauri/test_rust_log_file_perms.py::test_pi7_rust_unit_test_log_file_mode_0o600_passes'"
worker 'gw1' crashed while running 'tests/tauri/test_rust_log_file_perms.py::test_pi7_rust_unit_test_log_file_mode_0o600_passes'
```

### 64. `tests.tauri.test_window_lifecycle_parity.test_main_runtime_grants_the_window_queries_the_bridge_calls`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/test_window_lifecycle_parity.py:92`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/voice-typer/voice-typer/src-tauri/gen/schemas/acl-manifests.json'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/voice-typer/voice-typer/src-tauri/gen/schemas/acl-manifests.json'
tests/tauri/test_window_lifecycle_parity.py:92: in test_main_runtime_grants_the_window_queries_the_bridge_calls
    resolved = _capability_permissions("main-runtime")
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/tauri/test_window_lifecycle_parity.py:82: in _capability_permissions
    manifest = json.loads(ACL_MANIFESTS.read_text(encoding="utf-8"))
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/voice-typer/voice-typer/src-tauri/gen/schemas/acl-manifests.json'
```

### 65. `tests.tauri.test_window_lifecycle_parity.test_main_runtime_grants_on_resized_via_event_listen`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/test_window_lifecycle_parity.py:133`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/voice-typer/voice-typer/src-tauri/gen/schemas/acl-manifests.json'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/voice-typer/voice-typer/src-tauri/gen/schemas/acl-manifests.json'
tests/tauri/test_window_lifecycle_parity.py:133: in test_main_runtime_grants_on_resized_via_event_listen
    resolved = _capability_permissions("main-runtime")
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/tauri/test_window_lifecycle_parity.py:82: in _capability_permissions
    manifest = json.loads(ACL_MANIFESTS.read_text(encoding="utf-8"))
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/voice-typer/voice-typer/src-tauri/gen/schemas/acl-manifests.json'
```

### 66. `tests.test_dictation_pipeline_check_resources.TestCheckResourcesXZEH008SilentExcept.test_ram_ctypes_fallback_failure_logs_debug`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
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

### 67. `tests.test_logging_rotation_perms.test_do_rollover_chmod_runs_inside_lock`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_logging_rotation_perms.py:338`

```
+    where <built-in method index of list object at 0x119989d40> = ['release', 'chmod', 'release'].index

AssertionError: chmod must run BEFORE release; got call_order=['release', 'chmod', 'release']
assert 1 < 0
 +  where 1 = <built-in method index of list object at 0x119989d40>('chmod')
 +    where <built-in method index of list object at 0x119989d40> = ['release', 'chmod', 'release'].index
 +  and   0 = <built-in method index of list object at 0x119989d40>('release')
 +    where <built-in method index of list object at 0x119989d40> = ['release', 'chmod', 'release'].index
tests/test_logging_rotation_perms.py:338: in test_do_rollover_chmod_runs_inside_lock
    assert call_order.index("chmod") < call_order.index("release"), (
E   AssertionError: chmod must run BEFORE release; got call_order=['release', 'chmod', 'release']
E   assert 1 < 0
E    +  where 1 = <built-in method index of list object at 0x119989d40>('chmod')
E    +    where <built-in method index of list object at 0x119989d40> = ['release', 'chmod', 'release'].index
E    +  and   0 = <built-in method index of list object at 0x119989d40>('release')
E    +    where <built-in method index of list object at 0x119989d40> = ['release', 'chmod', 'release'].index
```

### 68. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_menu_has_toggle_dictation`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:194`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:194: in test_menu_has_toggle_dictation
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 69. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_models_submenu_in_menu`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:228`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:228: in test_models_submenu_in_menu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 70. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_menu_has_quit`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:202`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:202: in test_menu_has_quit
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 71. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_undo_last_item_not_in_menu`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:243`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:243: in test_undo_last_item_not_in_menu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 72. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_menu_has_required_items`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:208`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:208: in test_menu_has_required_items
    items = _FakeIcon.last_kwargs["menu"]()
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 73. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_force_cancel_not_in_menu_when_idle`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:267`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:267: in test_force_cancel_not_in_menu_when_idle
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 74. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_no_advanced_submenu`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:274`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:274: in test_no_advanced_submenu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 75. `tests.test_tray.TestMenuIsPystrayMenuInstance.test_menu_is_fake_menu_instance`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:294`

```
+  where False = isinstance(None, _FakeMenu)

assert False
 +  where False = isinstance(None, _FakeMenu)
tests/test_tray.py:294: in test_menu_is_fake_menu_instance
    assert isinstance(menu, _FakeMenu)
E   assert False
E    +  where False = isinstance(None, _FakeMenu)
```

### 76. `tests.test_tray.TestTrayStartIsNonBlocking.test_start_returns_without_blocking`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:315`

```
AttributeError: 'NoneType' object has no attribute '_run_called'

AttributeError: 'NoneType' object has no attribute '_run_called'
tests/test_tray.py:315: in test_start_returns_without_blocking
    assert not tray._icon._run_called
               ^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: 'NoneType' object has no attribute '_run_called'
```

### 77. `tests.test_tray.TestMenuCallableSignature.test_menu_materialization_works`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:374`

```
+  where False = isinstance(None, _FakeMenu)

assert False
 +  where False = isinstance(None, _FakeMenu)
tests/test_tray.py:374: in test_menu_materialization_works
    assert isinstance(menu, _FakeMenu)
E   assert False
E    +  where False = isinstance(None, _FakeMenu)
```

### 78. `tests.test_tray.TestTrayPendingState.test_pending_state_flushed_on_run`

- Legs: macos-14-3.12, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `(unknown location)`

```
(no error line)

failed on setup with "worker 'gw0' crashed while running 'tests/test_tray.py::TestTrayPendingState::test_pending_state_flushed_on_run'"
worker 'gw0' crashed while running 'tests/test_tray.py::TestTrayPendingState::test_pending_state_flushed_on_run'
```

### 79. `tests.test_tray.TestUndoLastAbsentFromTrayMenu.test_undo_last_item_not_in_menu`

- Legs: macos-14-3.12
- Location: `tests/test_tray.py:753`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:753: in test_undo_last_item_not_in_menu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 80. `tests.test_tray.TestUndoLastAbsentFromTrayMenu.test_undo_last_not_above_force_cancel`

- Legs: macos-14-3.12
- Location: `tests/test_tray.py:766`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:766: in test_undo_last_not_above_force_cancel
    items = _FakeIcon.last_kwargs["menu"]()
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 81. `tests.test_tray.TestForceCancelConditional.test_force_cancel_hidden_when_idle`

- Legs: macos-14-3.12
- Location: `tests/test_tray.py:908`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:908: in test_force_cancel_hidden_when_idle
    items = _FakeIcon.last_kwargs["menu"]()
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 82. `tests.test_tray.TestElapsedRecordingTooltip.test_set_state_recording_starts_timer`

- Legs: macos-14-3.12
- Location: `(unknown location)`

```
(no error line)

failed on setup with "worker 'gw1' crashed while running 'tests/test_tray.py::TestElapsedRecordingTooltip::test_set_state_recording_starts_timer'"
worker 'gw1' crashed while running 'tests/test_tray.py::TestElapsedRecordingTooltip::test_set_state_recording_starts_timer'
```

### 83. `tests.test_waveform_bubble_wiring.TestWorkerCoalescesStaleLevels.test_coalesces_multiple_bubble_levels_to_one_publish`

- Legs: macos-14-3.12
- Location: `tests/test_waveform_bubble_wiring.py:532`

```
+  where 2 = len([{'data': {'peak': 0.2, 'rms': 0.4}, 'type': 'bubble_level'}, {'data': {'icon': 'recording', 'tooltip': 'Voice Typer | Recording... (00:03) (Caps Lock)'}, 'type': 'tray_state'}])

AssertionError: expected exactly 1 publish call (coalesced bubble_level only), got 2: [{'type': 'bubble_level', 'data': {'rms': 0.4, 'peak': 0.2}}, {'type': 'tray_state', 'data': {'icon': 'recording', 'tooltip': 'Voice Typer | Recording... (00:03) (Caps Lock)'}}]
assert 2 == 1
 +  where 2 = len([{'data': {'peak': 0.2, 'rms': 0.4}, 'type': 'bubble_level'}, {'data': {'icon': 'recording', 'tooltip': 'Voice Typer | Recording... (00:03) (Caps Lock)'}, 'type': 'tray_state'}])
tests/test_waveform_bubble_wiring.py:532: in test_coalesces_multiple_bubble_levels_to_one_publish
    assert len(published_calls) == 1, (
E   AssertionError: expected exactly 1 publish call (coalesced bubble_level only), got 2: [{'type': 'bubble_level', 'data': {'rms': 0.4, 'peak': 0.2}}, {'type': 'tray_state', 'data': {'icon': 'recording', 'tooltip': 'Voice Typer | Recording... (00:03) (Caps Lock)'}}]
E   assert 2 == 1
E    +  where 2 = len([{'data': {'peak': 0.2, 'rms': 0.4}, 'type': 'bubble_level'}, {'data': {'icon': 'recording', 'tooltip': 'Voice Typer | Recording... (00:03) (Caps Lock)'}, 'type': 'tray_state'}])
```

### 84. `tests.app.test_lifecycle.TestMainWrapsIpcMain.test_main_logs_warning_when_faulthandler_enable_raises`

- Legs: ubuntu-22.04-3.10
- Location: `tests/app/test_lifecycle.py:1690`

```
ImportError: cannot import name 'NotRequired' from 'typing' (/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/typing.py)

ImportError: cannot import name 'NotRequired' from 'typing' (/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/typing.py)
tests/app/test_lifecycle.py:1690: in test_main_logs_warning_when_faulthandler_enable_raises
    import voice_typer.server.ipc_server as ipc_server_module
voice_typer/server/ipc_server.py:304: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402
voice_typer/server/handlers/__init__.py:61: in <module>
    from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
voice_typer/server/handlers/vocabulary_handlers.py:17: in <module>
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
E   ImportError: cannot import name 'NotRequired' from 'typing' (/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/typing.py)
```

### 85. `tests.tauri.mig17.test_autostart_installer_linux.test_single_instance_plugin_wired_in_tauri`

- Legs: ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig17/test_autostart_installer_linux.py:728`

```
assert 'get_webview_window' in '//! Voice Typer: Tauri v2 host (ADR-0020 implementation).\n//!\n//! Rust shell replacing the Electron main process. Responsibilities:\n//! 1. Spawn the Python sidecar via Tauri\'s `externalBin` mechanism,\n//!    passing `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.\n//! 2. Open a WebSocket client to `ws://127.0.0.1:N` and perform the\n//!    bearer-token auth handshake (`{"type":"auth","token":...}`).\n//! 3. Expose ONE generic `dispatch` command to the webview and\n//!    re-emit server-initiated events as Tauri events.\n//! 4. Run the supervisor (respawn with backoff, then full-app relaunch)\n//!    and coalesce `bubble_level` events to ≤30 Hz.\n//! 5. Single-instance gate runs BEFORE any sidecar init so a second\n//!    launch doesn\'t spawn a zombie sidecar.\n//!\n//! # Cross-platform\n//!\n//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).\n//! - macOS: WKWebView (Safari-based, system).\n//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).\n//!\n//! # Module layout\n//!\n//! Wiring-only (C-ARCH-1): app builder, plugin registration, `.setup`\n//! glue (window bootstrap → `window_bootstrap`, sidecar cold-start\n//! task → `sidecar::spa...tauri::generate_context!())\n        .unwrap_or_else(|e| {\n            eprintln!("[FATAL] tauri build failed: {e:?}");\n            log::error!("[FATAL] tauri build failed: {e:?}");\n            std::process::exit(1);\n        })\n        .run(|app_handle, event| match event {\n            RunEvent::ExitRequested { .. } | RunEvent::Exit => {\n                // Teardown body: `state::on_host_exit`, dedicated thread\n                // + bounded-time `block_on` (see `sidecar::lifecycle`).\n                crate::state::on_host_exit(app_handle);\n            }\n            // macOS Dock-icon activation (Electron `app.on("activate")`\n            // parity, MO-112): macOS keeps the process alive after the\n            // last window is closed (tray / Dock), so a Dock click must\n            // bring the dashboard back instead of doing nothing. The\n            // shared routine recreates the window when it is gone and\n            // otherwise runs the full raise sequence (MO-109).\n            #[cfg(target_os = "macos")]\n            RunEvent::Reopen { .. } => {\n                crate::host_events::show_main_window(app_handle);\n            }\n            _ => {}\n        });\n}\n'

AssertionError: main.rs's single-instance callback must call app.get_webview_window("main") to focus the existing window.
assert 'get_webview_window' in '//! Voice Typer: Tauri v2 host (ADR-0020 implementation).\n//!\n//! Rust shell replacing the Electron main process. Responsibilities:\n//! 1. Spawn the Python sidecar via Tauri\'s `externalBin` mechanism,\n//!    passing `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.\n//! 2. Open a WebSocket client to `ws://127.0.0.1:N` and perform the\n//!    bearer-token auth handshake (`{"type":"auth","token":...}`).\n//! 3. Expose ONE generic `dispatch` command to the webview and\n//!    re-emit server-initiated events as Tauri events.\n//! 4. Run the supervisor (respawn with backoff, then full-app relaunch)\n//!    and coalesce `bubble_level` events to ≤30 Hz.\n//! 5. Single-instance gate runs BEFORE any sidecar init so a second\n//!    launch doesn\'t spawn a zombie sidecar.\n//!\n//! # Cross-platform\n//!\n//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).\n//! - macOS: WKWebView (Safari-based, system).\n//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).\n//!\n//! # Module layout\n//!\n//! Wiring-only (C-ARCH-1): app builder, plugin registration, `.setup`\n//! glue (window bootstrap → `window_bootstrap`, sidecar cold-start\n//! task → `sidecar::spa...tauri::generate_context!())\n        .unwrap_or_else(|e| {\n            eprintln!("[FATAL] tauri build failed: {e:?}");\n            log::error!("[FATAL] tauri build failed: {e:?}");\n            std::process::exi
… (truncated)
```

### 86. `tests.test_install_permissions_gsettings.TestSwayFlow.test_appends_block_when_no_existing_line`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_install_permissions_gsettings.py:430`

```
AssertionError: assert '# Voice Typer — Caps Lock neutralization' in 'set $mod Mod4\nbindsym Mod4+Return exec foot\n\n# Voice Typer. Caps Lock neutralization\ninput * xkb_options caps:none\n'

AssertionError: assert '# Voice Typer — Caps Lock neutralization' in 'set $mod Mod4\nbindsym Mod4+Return exec foot\n\n# Voice Typer. Caps Lock neutralization\ninput * xkb_options caps:none\n'
tests/test_install_permissions_gsettings.py:430: in test_appends_block_when_no_existing_line
    assert "# Voice Typer — Caps Lock neutralization" in new_text
E   AssertionError: assert '# Voice Typer — Caps Lock neutralization' in 'set $mod Mod4\nbindsym Mod4+Return exec foot\n\n# Voice Typer. Caps Lock neutralization\ninput * xkb_options caps:none\n'
```

### 87. `tests.test_install_permissions_gsettings.TestUninstallRestore.test_sway_restore_removes_block_when_no_prior_line`

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

### 88. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_menu_has_restart`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:198`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:198: in test_menu_has_restart
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 89. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_toggle_label_includes_current_hotkey`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:223`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:223: in test_toggle_label_includes_current_hotkey
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 90. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_microphone_submenu_in_menu`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:238`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:238: in test_microphone_submenu_in_menu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 91. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_settings_history_help_items_in_menu`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:250`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:250: in test_settings_history_help_items_in_menu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 92. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_no_hotkey_submenu`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:279`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:279: in test_no_hotkey_submenu
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 93. `tests.test_tray.TestTrayMenuHasMinimalOptions.test_no_start_on_login`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:283`

```
KeyError: 'menu'

KeyError: 'menu'
tests/test_tray.py:283: in test_no_start_on_login
    labels = _menu_labels(tray)
             ^^^^^^^^^^^^^^^^^^
tests/test_tray.py:184: in _menu_labels
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'menu'
```

### 94. `tests.test_tray.TestMenuIsPystrayMenuInstance.test_menu_callable_is_passed_to_menu_constructor`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:299`

```
+  where False = isinstance(None, _FakeMenu)

assert False
 +  where False = isinstance(None, _FakeMenu)
tests/test_tray.py:299: in test_menu_callable_is_passed_to_menu_constructor
    assert isinstance(menu, _FakeMenu)
E   assert False
E    +  where False = isinstance(None, _FakeMenu)
```

### 95. `tests.test_tray.TestTrayStartIsNonBlocking.test_start_without_bg_work_does_not_crash`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:321`

```
+  where None = <voice_typer.server.tray.TrayIcon object at 0x7f37a4beaab0>._icon

assert None is not None
 +  where None = <voice_typer.server.tray.TrayIcon object at 0x7f37a4beaab0>._icon
tests/test_tray.py:321: in test_start_without_bg_work_does_not_crash
    assert tray._icon is not None
E   assert None is not None
E    +  where None = <voice_typer.server.tray.TrayIcon object at 0x7f37a4beaab0>._icon
```

### 96. `tests.test_tray.TestTrayRunBlocksMainThread.test_run_calls_icon_run`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:327`

```
AttributeError: 'NoneType' object has no attribute '_run_called'

AttributeError: 'NoneType' object has no attribute '_run_called'
tests/test_tray.py:327: in test_run_calls_icon_run
    assert not tray._icon._run_called
               ^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: 'NoneType' object has no attribute '_run_called'
```

### 97. `tests.test_tray.TestTrayPendingState.test_notification_before_run_is_queued`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:351`

```
+    where [] = <voice_typer.server.tray.TrayIcon object at 0x7f376cb32b40>._pending_notifications

assert 0 == 1
 +  where 0 = len([])
 +    where [] = <voice_typer.server.tray.TrayIcon object at 0x7f376cb32b40>._pending_notifications
tests/test_tray.py:351: in test_notification_before_run_is_queued
    assert len(tray._pending_notifications) == 1
E   assert 0 == 1
E    +  where 0 = len([])
E    +    where [] = <voice_typer.server.tray.TrayIcon object at 0x7f376cb32b40>._pending_notifications
```

### 98. `tests.test_tray.TestMenuCallableSignature.test_menu_callable_takes_zero_positional_args`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:367`

```
+  where False = isinstance(None, _FakeMenu)

assert False
 +  where False = isinstance(None, _FakeMenu)
tests/test_tray.py:367: in test_menu_callable_takes_zero_positional_args
    assert isinstance(menu, _FakeMenu)
E   assert False
E    +  where False = isinstance(None, _FakeMenu)
```

### 99. `tests.test_tray.TestTrayUnavailableFallback.test_voice_typer_no_tray_env_var_other_value_does_not_skip`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_tray.py:475`

```
+  where None = <voice_typer.server.tray.TrayIcon object at 0x7f37aad5e810>._icon

assert None is not None
 +  where None = <voice_typer.server.tray.TrayIcon object at 0x7f37aad5e810>._icon
tests/test_tray.py:475: in test_voice_typer_no_tray_env_var_other_value_does_not_skip
    assert tray._icon is not None
E   assert None is not None
E    +  where None = <voice_typer.server.tray.TrayIcon object at 0x7f37aad5e810>._icon
```

### 100. `tests.test_tray.TestNotifySafety.test_notify_safety_bypasses_toggle`

- Legs: ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `(unknown location)`

```
(no error line)

failed on setup with "worker 'gw1' crashed while running 'tests/test_tray.py::TestNotifySafety::test_notify_safety_bypasses_toggle'"
worker 'gw1' crashed while running 'tests/test_tray.py::TestNotifySafety::test_notify_safety_bypasses_toggle'
```

# Custom torch hook overriding _pyinstaller_hooks_contrib's default.
#
# The stock hook calls collect_submodules("torch") with no filter, which
# triggers __import__ on every torch subpackage -- including torch.distributed,
# torch.utils.tensorboard, torch._inductor and others that we never use.
# On Windows, importing torch.distributed.* during the build sets process-level
# signal handlers and emits deprecation warnings; somewhere in that flood,
# PyInstaller catches a KeyboardInterrupt and aborts with
# "Aborted by user request." at a random hook.
#
# Filtering these out at collect time prevents the imports altogether.

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    is_module_or_submodule,
    PY_DYLIB_PATTERNS,
)

module_collection_mode = "pyz+py"
warn_on_missing_hiddenimports = False

_EXCLUDED_TORCH_SUBPACKAGES = (
    "torch.distributed",
    "torch.distributions",
    "torch.utils.tensorboard",
    "torch.testing",
    "torch.onnx",
    "torch._inductor",
    "torch._dynamo",
    "torch.fx.experimental",
)


def _torch_filter(name: str) -> bool:
    return not any(is_module_or_submodule(name, pkg) for pkg in _EXCLUDED_TORCH_SUBPACKAGES)


datas = collect_data_files(
    "torch",
    excludes=[
        "**/*.h", "**/*.hpp", "**/*.cuh", "**/*.lib", "**/*.cpp",
        "**/*.pyi", "**/*.cmake",
        "**/distributed/**", "**/tensorboard/**", "**/testing/**",
    ],
)

hiddenimports = collect_submodules("torch", filter=_torch_filter)

binaries = collect_dynamic_libs(
    "torch",
    search_patterns=PY_DYLIB_PATTERNS + ['*.so.*'],
)

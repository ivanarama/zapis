# sherpa-onnx (диаризация) держит нативную часть в подкаталоге lib/, который не
# является пакетом: __init__.py там нет, и `sherpa_onnx.lib` — implicit namespace
# package. Из-за этого collect_submodules() расширение не находит, а без него
# `from sherpa_onnx.lib._sherpa_onnx import ...` в __init__.py пакета падает уже
# в собранном приложении.
#
# Рядом с расширением лежат sherpa-onnx-c-api.dll и своя копия onnxruntime.dll
# (та, что в пакете onnxruntime для GigaAM, ему не подходит) — их забираем как
# бинарники в тот же каталог, иначе .pyd не загрузится.
from PyInstaller.utils.hooks import PY_DYLIB_PATTERNS, collect_dynamic_libs, collect_submodules

hiddenimports = collect_submodules("sherpa_onnx") + ["sherpa_onnx.lib._sherpa_onnx"]

# Само расширение забираем явно: PY_DYLIB_PATTERNS не содержит *.pyd, а имя
# .so у него не начинается с «lib», так что ни один стандартный шаблон его не
# ловит. Полагаться на то, что модульный граф дотянется до него через
# namespace-пакет, не хочется — ошибка вылезла бы только в собранном exe.
binaries = collect_dynamic_libs(
    "sherpa_onnx",
    search_patterns=PY_DYLIB_PATTERNS + ["*.pyd", "*.so", "*.so.*"],
)

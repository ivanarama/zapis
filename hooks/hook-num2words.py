# num2words выбирает языковой модуль (num2words.lang_RU и т.п.) динамически по коду
# языка, поэтому PyInstaller сам их не находит — собираем все подмодули явно.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("num2words")

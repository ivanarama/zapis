"""Свой словарь ударений — поверх автоматической расстановки.

ruaccent закрывает большинство слов, но на именах, топонимах и редких формах
ошибается, и переспорить его нечем: правку слышит только человек. Этот модуль
даёт файл, который можно править руками и применять последним — после
нормализации и после ruaccent. Что записано здесь, то и прозвучит.

Формат — обычный JSON «слово → слово с ударением»:

    {
      "муромцы":  "м+уромцы",
      "аксак*":   "акс+ак",
      "богурна":  "богурн+а"
    }

Ключ со звёздочкой на конце — основа: подходит любому слову, которое с неё
начинается, заменяется только сама основа («аксак*» покроет и «Аксаковых», и
«Аксаковыми»). Регистр ключа неважен, заглавная буква исходного слова
сохраняется. Знак «+» ставится перед ударной гласной — так же, как его ставит
ruaccent и понимает Silero (см. backend.tts.stress).

Словарь общий для книг и для презентаций: одни и те же фамилии читаются одинаково.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("zavuk.tts.stress_dict")

WORD = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё+]*")


class StressDict:
    """Словарь ручных ударений: точные слова и основы."""

    def __init__(self, exact: dict[str, str] | None = None, stems: dict[str, str] | None = None):
        self.exact = {k.lower(): v for k, v in (exact or {}).items()}
        self.stems = {k.lower(): v for k, v in (stems or {}).items()}
        # длинные основы проверяем первыми: «дворянин» должен победить «дворян»
        self._stem_order = sorted(self.stems, key=len, reverse=True)

    def __len__(self) -> int:
        return len(self.exact) + len(self.stems)

    @classmethod
    def load(cls, path: str | Path) -> "StressDict":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # словарь необязателен: при поломке молча озвучиваем без него
            log.warning("не читается словарь ударений %s: %s", path, exc)
            return cls()
        exact, stems = {}, {}
        for key, value in raw.items():
            if not isinstance(value, str):
                continue
            if key.endswith("*"):
                stems[key[:-1]] = value
            else:
                exact[key] = value
        log.info("словарь ударений: %d слов, %d основ (%s)", len(exact), len(stems), path)
        return cls(exact, stems)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(self.exact)
        data.update({k + "*": v for k, v in self.stems.items()})
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    # ---- применение ----

    @staticmethod
    def _match_case(sample: str, word: str) -> str:
        """Вернуть заглавную букву, если она была в исходном слове."""
        if sample[:1].isupper():
            return word[:1].upper() + word[1:]
        return word

    def _replace(self, word: str) -> str:
        bare = word.replace("+", "")
        low = bare.lower()
        if low in self.exact:
            return self._match_case(bare, self.exact[low])
        for stem in self._stem_order:
            if low.startswith(stem):
                return self._match_case(bare, self.stems[stem] + bare[len(stem) :])
        return word

    def apply(self, text: str) -> str:
        """Проставить свои ударения, стерев чужие в тех же словах."""
        if not self.exact and not self.stems:
            return text
        return WORD.sub(lambda m: self._replace(m.group()), text)


def load_default(app_dir: str | Path) -> StressDict:
    """Словарь из рабочего каталога приложения (`<app>/stress.json`)."""
    return StressDict.load(Path(app_dir) / "stress.json")

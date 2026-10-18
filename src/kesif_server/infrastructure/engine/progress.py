"""Motor günlüğünden yapılandırılmış ilerleme türetir.

Motor adımlarını `log.info("=== hastalık: adım ===")` ve sayaçları `"künye: 1000/4903"` biçiminde yazar. Burada bu satırlar
kullanıcı diline çevrilir; arayüz hiçbir zaman ham günlüğü yorumlamak zorunda kalmaz (ham günlük ayrıca dosyada tutulur).
"""

from __future__ import annotations

import re
from typing import Optional

from ...domain.entities import RunProgress

STEP_RE = re.compile(r"^=== .+?: (\w+) ===$")
COUNT_RE = re.compile(r"(\d[\d.,]*)\s*/\s*(\d[\d.,]*)")

STEP_LABELS = {
    "fetch": "Makaleler indiriliyor ve işleniyor",
    "drugs": "İlaç sözlüğü hazırlanıyor",
    "labels": "Doğrulama etiketleri toplanıyor",
    "tune": "Parametreler ayarlanıyor",
    "rank": "Adaylar sıralanıyor",
    "backtest": "Geriye dönük doğrulama yapılıyor",
    "novelty": "Yenilik kontrolü yapılıyor",
    "report": "Rapor yazılıyor",
}

# günlük parçası → insan dili (ilk eşleşen kazanır)
PHRASES = [
    ("makaleleri aranıyor", "Makaleler aranıyor"),
    ("künye", "Makale künyeleri alınıyor"),
    ("işaretleme", "Makaleler işleniyor — gen, ilaç ve ilişkiler çıkarılıyor"),
    ("Köprü genler seçiliyor", "Köprü genler seçiliyor ve gen literatürü toplanıyor"),
    ("İndeks yazılıyor", "Derlem kaydediliyor"),
    ("İlaç sözlüğü", "İlaç sözlüğü kuruluyor"),
    ("Etiketler", "Doğrulama etiketleri hazırlanıyor"),
    ("Aday sınıfları", "Adaylar sınıflandırılıyor"),
    ("aday yazıldı", "Aday listesi yazıldı"),
    ("Keşif özeti", "Keşif özeti çıkarılıyor"),
    ("Parametre ayarı", "Parametre ayarı tamamlandı"),
    ("Rapor", "Rapor hazırlanıyor"),
    ("Ağ hatası", "Bağlantı sorunu — yeniden denenecek"),
]


def _num(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


class ProgressTracker:
    """Günlük satırlarını sırayla alır, `RunProgress` üretir. Değişiklik olduğunda `on_change` çağrılır."""

    def __init__(self, on_change, total_steps: Optional[list] = None):
        self._on_change = on_change
        self._p = RunProgress(step=None, message="Başlıyor", completed_steps=[])
        self._steps = list(total_steps or [])

    @property
    def progress(self) -> RunProgress:
        return self._p

    def feed(self, line: str) -> None:
        text = line.strip()
        m = STEP_RE.match(text)
        if m:
            if self._p.step and self._p.step not in self._p.completed_steps:
                self._p.completed_steps.append(self._p.step)
            self._p.step = m.group(1)
            self._p.message = STEP_LABELS.get(self._p.step, self._p.step)
            self._p.done = self._p.total = None
            self._emit()
            return
        for needle, label in PHRASES:
            if needle in text:
                self._p.message = label
                c = COUNT_RE.search(text)
                if c:
                    self._p.done, self._p.total = _num(c.group(1)), _num(c.group(2))
                else:
                    self._p.done = self._p.total = None
                self._emit()
                return

    def finish(self) -> None:
        if self._p.step and self._p.step not in self._p.completed_steps:
            self._p.completed_steps.append(self._p.step)
        self._p.message = "Tamamlandı"
        self._p.done = self._p.total = None
        self._emit()

    def _emit(self) -> None:
        self._on_change(RunProgress.from_dict(self._p.to_dict()))

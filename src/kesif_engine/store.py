from __future__ import annotations

import json
from pathlib import Path


class JsonlCache:
    """PMID anahtarlı, satır satır JSON dosyası. Yarıda kesilen indirme kaldığı yerden sürer."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.data[rec["pmid"]] = rec

    def __contains__(self, key) -> bool:
        return key in self.data

    def get(self, key):
        return self.data.get(key)

    def add_many(self, records) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            for rec in records:
                self.data[rec["pmid"]] = rec
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

from __future__ import annotations

import csv
from datetime import datetime
from typing import Iterator, List
from core.models import Candle


class CsvDataLoader:
    """
    محمل بيانات تاريخية يقرأ ملف CSV ويحول الصفوف إلى كائنات Candle تدريجياً.
    """

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath

    def load_candles(self) -> Iterator[Candle]:
        """قراءة الملف وإرجاع الشموع واحدة تلو الأخرى (Generator)."""
        with open(self.filepath, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                yield Candle(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )

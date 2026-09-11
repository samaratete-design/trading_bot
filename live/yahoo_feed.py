"""
live/yahoo_feed.py

Alternate live 15m candle feed for BTC-USD, sourced from Yahoo Finance's
public chart REST endpoint. Selected ONLY via LIVE_DATA_FEED=yahoo (see
live/run_live_paper.py::get_data_feed). Does not modify or replace
live/binance_feed.py, which remains the runtime default
(LIVE_DATA_FEED=binance or unset).

Candle here is the canonical core.models.Candle -- NOT the local
placeholder dataclass defined in strategies/btc_trend_v1.py, and NOT
imported from live.binance_feed. timestamp is a real datetime (UTC),
never converted to a millisecond integer.

Only ever returns fully CLOSED candles: candle_close_time <= current UTC
time. The currently-forming candle is always dropped, even if Yahoo
includes it in the response.

No API key required (public chart endpoint). No order placement
capability exists in this file at all.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests

from core.models import Candle

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo only retains 15m-granularity history for a limited recent window;
# "5d" comfortably covers 250+ 15m bars (250 * 15m ~= 2.6 days) with margin
# for weekends/gaps even though BTC-USD itself trades 24/7.
_RANGE = "5d"

_INTERVAL_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class YahooFeedError(RuntimeError):
    """Raised on any Yahoo network, parse, or data-availability failure.

    Deliberately NOT swallowed into an empty list -- network and data
    errors must stay observable to the poll loop / operator, per the
    closed-candle contract (see live/run_live_paper.py's [feed error]
    handling, which already expects fetch calls to raise rather than
    return []).
    """


class YahooFeed:
    """Live closed-candle feed for BTC-USD sourced from Yahoo Finance.

    Exposes the shape live/run_live_paper.py::get_data_feed() expects
    from any feed instance: .symbol, .interval, .fetch_closed_candles(limit).
    """

    def __init__(self, symbol: str = "BTC-USD", interval: str = "15m") -> None:
        self.symbol = symbol
        self.interval = interval

    def fetch_closed_candles(self, limit: int = 250) -> List[Candle]:
        """
        Returns exactly the newest `limit` valid, fully CLOSED candles for
        self.symbol, oldest first (same ordering convention as
        live/binance_feed.py::fetch_recent_closed_candles, which
        live/run_live_paper.py already iterates in that order for warmup).

        Raises YahooFeedError if:
          - the network/HTTP request fails
          - the response cannot be parsed into the expected shape
          - fewer than `limit` valid closed candles are available

        Order of operations, per contract: validate/filter first, THEN
        deduplicate + sort, THEN take the newest `limit`. Never slices
        with limit before validation.
        """
        raw_rows = self._fetch_raw()
        valid = self._validate_and_filter(raw_rows)
        deduped = self._dedupe_and_sort(valid)

        if len(deduped) < limit:
            raise YahooFeedError(
                f"only {len(deduped)} valid closed {self.interval} candles "
                f"available for {self.symbol} in range={_RANGE}, need {limit}"
            )

        return deduped[-limit:]

    # -- fetch ----------------------------------------------------------
    def _fetch_raw(self) -> List[Tuple[Optional[int], int, object, object, object, object, object]]:
        try:
            resp = requests.get(
                YAHOO_CHART_URL.format(symbol=self.symbol),
                params={"interval": self.interval, "range": _RANGE, "includePrePost": "false"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise YahooFeedError(f"Yahoo request failed for {self.symbol}: {e}") from e

        try:
            payload = resp.json()
        except ValueError as e:
            raise YahooFeedError(f"Yahoo response was not valid JSON for {self.symbol}: {e}") from e

        try:
            result = payload["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
            opens, highs, lows, closes, volumes = (
                quote["open"], quote["high"], quote["low"], quote["close"], quote["volume"],
            )
            meta = result.get("meta", {})
            interval_seconds = self._interval_to_seconds(meta.get("dataGranularity", self.interval))
        except (KeyError, IndexError, TypeError) as e:
            chart_error = None
            if isinstance(payload, dict):
                chart_error = payload.get("chart", {}).get("error")
            raise YahooFeedError(
                f"unexpected Yahoo chart response shape for {self.symbol}"
                + (f" -- error field: {chart_error}" if chart_error else f": {e!r}")
            ) from e

        rows = []
        for i, ts in enumerate(timestamps):
            rows.append((
                ts,
                interval_seconds,
                opens[i] if i < len(opens) else None,
                highs[i] if i < len(highs) else None,
                lows[i] if i < len(lows) else None,
                closes[i] if i < len(closes) else None,
                volumes[i] if i < len(volumes) else None,
            ))
        return rows

    @staticmethod
    def _interval_to_seconds(interval: str) -> int:
        try:
            n = int(interval[:-1])
            unit_seconds = _INTERVAL_UNIT_SECONDS[interval[-1]]
            return n * unit_seconds
        except (ValueError, KeyError, IndexError):
            # Falls back to the BTC Trend v1 contract interval (15m) --
            # only reached if Yahoo's meta.dataGranularity is missing/odd,
            # not a silent data-correctness issue since candle timestamps
            # themselves come straight from Yahoo's own timestamp array.
            return 15 * 60

    # -- validation -------------------------------------------------------
    def _validate_and_filter(
        self,
        rows: List[Tuple[Optional[int], int, object, object, object, object, object]],
    ) -> List[Candle]:
        now = datetime.now(timezone.utc)
        out: List[Candle] = []
        for open_ts, interval_seconds, o, h, l, c, v in rows:
            if open_ts is None:
                continue  # Yahoo emits a null timestamp slot for gaps

            if any(x is None for x in (o, h, l, c, v)):
                continue  # Yahoo pads gaps with nulls -- reject, never coerce to 0

            values = (o, h, l, c, v)
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in values):
                continue  # reject non-numeric
            if not all(math.isfinite(x) for x in values):
                continue  # reject NaN / +inf / -inf

            if not (o > 0 and h > 0 and l > 0 and c > 0):
                continue  # OHLC must be > 0
            if v < 0:
                continue  # volume must be >= 0

            open_time = datetime.fromtimestamp(open_ts, tz=timezone.utc)
            close_time = open_time + timedelta(seconds=interval_seconds)

            if close_time > now:
                continue  # still forming -- never included, per closed-candle contract

            out.append(Candle(
                timestamp=open_time,
                open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v),
            ))
        return out

    @staticmethod
    def _dedupe_and_sort(candles: List[Candle]) -> List[Candle]:
        by_ts = {}
        for cnd in candles:
            by_ts[cnd.timestamp] = cnd  # last write wins on a duplicate open-timestamp
        return sorted(by_ts.values(), key=lambda cnd: cnd.timestamp)

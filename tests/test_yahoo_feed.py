"""
tests/test_yahoo_feed.py

Unit tests for live/yahoo_feed.py against the ACTUAL core.models.Candle
contract. No network calls -- YahooFeed._fetch_raw is monkeypatched with
synthetic (timestamp, interval_seconds, o, h, l, c, v) rows, which is the
exact shape _fetch_raw returns after parsing Yahoo's JSON.
"""
from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from core.models import Candle
from live.yahoo_feed import YahooFeed, YahooFeedError

INTERVAL_S = 15 * 60


def _row(open_dt: datetime, o=100.0, h=101.0, l=99.0, c=100.5, v=10.0, interval_s=INTERVAL_S):
    return (int(open_dt.timestamp()), interval_s, o, h, l, c, v)


def _closed_rows(n: int, end: datetime, interval_s: int = INTERVAL_S):
    """n closed candles, newest one's close_time exactly at `end`."""
    rows = []
    newest_open = end - timedelta(seconds=interval_s)
    for i in range(n):
        open_dt = newest_open - timedelta(seconds=interval_s * (n - 1 - i))
        rows.append(_row(open_dt, interval_s=interval_s))
    return rows


class FakeYahooFeed(YahooFeed):
    """Swaps out the network call with a fixed set of raw rows."""

    def __init__(self, rows, **kw):
        super().__init__(**kw)
        self._rows = rows

    def _fetch_raw(self):
        return self._rows


class TestCandleContract(unittest.TestCase):
    def test_returns_core_models_candle_instances(self):
        now = datetime.now(timezone.utc)
        feed = FakeYahooFeed(_closed_rows(5, now))
        out = feed._validate_and_filter(feed._fetch_raw())
        self.assertTrue(all(isinstance(c, Candle) for c in out))

    def test_timestamp_is_datetime_not_ms_int(self):
        now = datetime.now(timezone.utc)
        feed = FakeYahooFeed(_closed_rows(1, now))
        out = feed._validate_and_filter(feed._fetch_raw())
        self.assertIsInstance(out[0].timestamp, datetime)
        self.assertNotIsInstance(out[0].timestamp, int)


class TestClosedCandleContract(unittest.TestCase):
    def test_forming_candle_is_excluded(self):
        now = datetime.now(timezone.utc)
        # One fully closed candle, one still-forming candle (open 5 min ago,
        # a 15m bar, so close_time is 10 min in the future).
        closed = _row(now - timedelta(seconds=INTERVAL_S))
        forming = _row(now - timedelta(seconds=5 * 60))
        feed = FakeYahooFeed([closed, forming])
        out = feed._validate_and_filter(feed._fetch_raw())
        self.assertEqual(len(out), 1)
        self.assertLessEqual(out[0].timestamp + timedelta(seconds=INTERVAL_S), now)

    def test_close_time_boundary_exactly_now_is_included(self):
        now = datetime.now(timezone.utc)
        row = _row(now - timedelta(seconds=INTERVAL_S))  # close_time == now
        feed = FakeYahooFeed([row])
        out = feed._validate_and_filter(feed._fetch_raw())
        self.assertEqual(len(out), 1)


class TestOHLCVValidation(unittest.TestCase):
    def _single(self, **kw):
        now = datetime.now(timezone.utc)
        row = _row(now - timedelta(seconds=INTERVAL_S), **kw)
        feed = FakeYahooFeed([row])
        return feed._validate_and_filter(feed._fetch_raw())

    def test_rejects_nan(self):
        self.assertEqual(self._single(o=math.nan), [])

    def test_rejects_positive_inf(self):
        self.assertEqual(self._single(h=math.inf), [])

    def test_rejects_negative_inf(self):
        self.assertEqual(self._single(l=-math.inf), [])

    def test_rejects_zero_open(self):
        self.assertEqual(self._single(o=0.0), [])

    def test_rejects_negative_high(self):
        self.assertEqual(self._single(h=-5.0), [])

    def test_rejects_negative_volume(self):
        self.assertEqual(self._single(v=-1.0), [])

    def test_accepts_zero_volume(self):
        self.assertEqual(len(self._single(v=0.0)), 1)

    def test_rejects_null_field(self):
        now = datetime.now(timezone.utc)
        row = (int((now - timedelta(seconds=INTERVAL_S)).timestamp()), INTERVAL_S, None, 101.0, 99.0, 100.5, 10.0)
        feed = FakeYahooFeed([row])
        self.assertEqual(feed._validate_and_filter(feed._fetch_raw()), [])


class TestDedupeSortAndLimit(unittest.TestCase):
    def test_validate_filter_before_limit_slice(self):
        """Regression test for the 'tail(limit) before validation' bug:
        junk rows interleaved among valid ones must not push valid rows
        out of the final `limit` slice."""
        now = datetime.now(timezone.utc)
        good_rows = _closed_rows(250, now)
        junk_rows = [_row(now - timedelta(seconds=INTERVAL_S * i), o=math.nan) for i in range(1, 50)]
        feed = FakeYahooFeed(good_rows + junk_rows)
        out = feed.fetch_closed_candles(limit=250)
        self.assertEqual(len(out), 250)
        self.assertTrue(all(math.isfinite(c.open) for c in out))

    def test_raises_when_fewer_than_limit_available(self):
        now = datetime.now(timezone.utc)
        feed = FakeYahooFeed(_closed_rows(10, now))
        with self.assertRaises(YahooFeedError):
            feed.fetch_closed_candles(limit=250)

    def test_output_sorted_oldest_first(self):
        now = datetime.now(timezone.utc)
        rows = _closed_rows(20, now)
        feed = FakeYahooFeed(rows)
        out = feed.fetch_closed_candles(limit=20)
        self.assertEqual([c.timestamp for c in out], sorted(c.timestamp for c in out))

    def test_duplicate_timestamps_deduped(self):
        now = datetime.now(timezone.utc)
        base = now - timedelta(seconds=INTERVAL_S)
        rows = [_row(base) for _ in range(5)] + _closed_rows(249, now - timedelta(seconds=INTERVAL_S))
        feed = FakeYahooFeed(rows)
        out = feed.fetch_closed_candles(limit=249)
        timestamps = [c.timestamp for c in out]
        self.assertEqual(len(timestamps), len(set(timestamps)))


class TestErrorHandling(unittest.TestCase):
    def test_network_failure_raises_not_swallowed(self):
        import requests

        class BrokenFeed(YahooFeed):
            def _fetch_raw(self):
                raise YahooFeedError("Yahoo request failed for BTC-USD: connection refused")

        feed = BrokenFeed()
        with self.assertRaises(YahooFeedError):
            feed.fetch_closed_candles(limit=250)


class TestDefaults(unittest.TestCase):
    def test_default_symbol_and_interval(self):
        feed = YahooFeed()
        self.assertEqual(feed.symbol, "BTC-USD")
        self.assertEqual(feed.interval, "15m")


if __name__ == "__main__":
    unittest.main()

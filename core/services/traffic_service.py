from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CounterPoint:
    sampled_at: float
    byte_count: float


@dataclass
class CounterState:
    rx: CounterPoint | None = None
    tx: CounterPoint | None = None


@dataclass(frozen=True)
class TrafficSample:
    source_id: str
    sampled_at: float
    rx_bytes: float | None
    tx_bytes: float | None
    rx_bps: float
    tx_bps: float
    rx_packets: float
    tx_packets: float


class TrafficService:
    """Normalizes router counters and calculates stable traffic rates."""

    MIN_SAMPLE_INTERVAL_SECONDS = 0.25
    MAX_SAMPLE_INTERVAL_SECONDS = 30.0
    LINK_SPEED_HEADROOM = 1.25
    ABSOLUTE_RATE_CEILING_BPS = 100_000_000_000.0

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.client = client
        self.clock = clock or time.monotonic
        self.counters: dict[str, CounterState] = {}

    def sample_time(self) -> float:
        value = float(self.clock())
        return value if math.isfinite(value) else time.monotonic()

    def clear(self) -> None:
        self.counters.clear()

    def reset(self, interface_id: str) -> None:
        self.counters.pop(self._counter_key(interface_id), None)

    @staticmethod
    def _counter_key(interface_id: str) -> str:
        return str(interface_id or "").strip() or "unknown"

    @staticmethod
    def normalize_rate(value: object) -> float:
        if value is None or isinstance(value, bool):
            return 0.0
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(rate) or rate < 0.0:
            return 0.0
        return rate

    @staticmethod
    def first_number(
        *sources: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float:
        value = TrafficService.first_optional_number(*sources, keys=keys)
        return value if value is not None else 0.0

    @staticmethod
    def first_optional_number(
        *sources: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float | None:
        """Return the first finite non-negative number, or ``None``."""
        for source in sources:
            if not isinstance(source, dict):
                continue
            lowered = {
                str(key).strip().casefold(): value
                for key, value in source.items()
            }
            for key in keys:
                value = lowered.get(str(key).casefold())
                if value is None or isinstance(value, bool):
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number) and number >= 0.0:
                    return number
        return None

    @staticmethod
    def extract_link_speed_bps(item: dict[str, Any]) -> float | None:
        if not isinstance(item, dict):
            return None

        for key in ("speed", "link-speed", "link_speed", "rate"):
            value = item.get(key)
            if value is None or isinstance(value, bool):
                continue

            text = str(value).strip().casefold().replace(",", ".")
            try:
                number = float(text)
            except ValueError:
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
                if not match:
                    continue
                number = float(match.group(1))

            if not math.isfinite(number) or number <= 0.0:
                continue
            if any(unit in text for unit in ("gbit", "gbps", "гбит")):
                return number * 1_000_000_000.0
            if any(unit in text for unit in ("mbit", "mbps", "мбит")):
                return number * 1_000_000.0
            if any(unit in text for unit in ("kbit", "kbps", "кбит")):
                return number * 1_000.0

            # Keenetic numeric interface speeds are normally in Mbit/s.
            if number <= 100_000.0:
                return number * 1_000_000.0
            return number
        return None

    def calculate_counter_speeds(
        self,
        interface_id: str,
        rxbytes: float | None,
        txbytes: float | None,
        link_speed_bps: float | None = None,
        *,
        sampled_at: float | None = None,
    ) -> tuple[float, float]:
        valid_rx = self._valid_counter(rxbytes)
        valid_tx = self._valid_counter(txbytes)
        if valid_rx is None and valid_tx is None:
            return 0.0, 0.0

        now = self.sample_time() if sampled_at is None else float(sampled_at)
        if not math.isfinite(now):
            now = self.sample_time()

        state = self.counters.setdefault(
            self._counter_key(interface_id),
            CounterState(),
        )
        ceiling = self._rate_ceiling(link_speed_bps)
        rx_bps = self._direction_rate(
            state=state,
            direction="rx",
            byte_count=valid_rx,
            sampled_at=now,
            ceiling_bps=ceiling,
        )
        tx_bps = self._direction_rate(
            state=state,
            direction="tx",
            byte_count=valid_tx,
            sampled_at=now,
            ceiling_bps=ceiling,
        )
        return rx_bps, tx_bps

    def _direction_rate(
        self,
        *,
        state: CounterState,
        direction: str,
        byte_count: float | None,
        sampled_at: float,
        ceiling_bps: float,
    ) -> float:
        value = self._valid_counter(byte_count)
        if value is None:
            return 0.0

        previous = getattr(state, direction)
        current = CounterPoint(sampled_at, value)
        if previous is None:
            setattr(state, direction, current)
            return 0.0

        elapsed = sampled_at - previous.sampled_at
        if 0.0 <= elapsed < self.MIN_SAMPLE_INTERVAL_SECONDS:
            # Keep the older point so rapid refreshes accumulate a valid span.
            return 0.0

        setattr(state, direction, current)
        if elapsed < 0.0 or elapsed > self.MAX_SAMPLE_INTERVAL_SECONDS:
            return 0.0

        delta = value - previous.byte_count
        if delta < 0.0:
            return 0.0

        rate = delta * 8.0 / elapsed
        if not math.isfinite(rate) or rate < 0.0 or rate > ceiling_bps:
            return 0.0
        return rate

    @staticmethod
    def _valid_counter(value: float | None) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 0.0:
            return None
        return number

    def _rate_ceiling(self, link_speed_bps: float | None) -> float:
        try:
            link_rate = float(link_speed_bps or 0.0)
        except (TypeError, ValueError):
            link_rate = 0.0
        if math.isfinite(link_rate) and link_rate > 0.0:
            return link_rate * self.LINK_SPEED_HEADROOM
        return self.ABSOLUTE_RATE_CEILING_BPS

    def measure_connection(
        self,
        *,
        connection: dict[str, Any],
        interface_data: dict[str, Any],
        sampled_at: float,
    ) -> TrafficSample:
        source_id, statistics = (
            self.client.interface_service.select_traffic_statistics(
                connection=connection,
                interface_data=interface_data,
                number_reader=self.first_number,
            )
        )
        source_id = source_id or str(connection.get("interface_id") or "")
        normalized = self.client.interface_service.normalize_statistics(
            statistics
        )
        rx_bytes = self.first_optional_number(
            normalized,
            keys=("rxbytes", "rx-bytes", "rx_bytes", "received-bytes"),
        )
        tx_bytes = self.first_optional_number(
            normalized,
            keys=("txbytes", "tx-bytes", "tx_bytes", "transmitted-bytes"),
        )
        rx_bps, tx_bps = self.calculate_counter_speeds(
            interface_id=source_id,
            rxbytes=rx_bytes,
            txbytes=tx_bytes,
            link_speed_bps=self.extract_link_speed_bps(
                interface_data.get(source_id, {})
            ),
            sampled_at=sampled_at,
        )
        return TrafficSample(
            source_id=source_id,
            sampled_at=sampled_at,
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
            rx_bps=rx_bps,
            tx_bps=tx_bps,
            rx_packets=self.first_number(
                normalized,
                keys=("rxpackets", "rx-packets", "rx_packets"),
            ),
            tx_packets=self.first_number(
                normalized,
                keys=("txpackets", "tx-packets", "tx_packets"),
            ),
        )

    @staticmethod
    def apply_sample(
        connection: dict[str, Any],
        sample: TrafficSample,
    ) -> None:
        connection["traffic_source"] = sample.source_id
        connection["traffic_sample_time"] = sample.sampled_at
        if sample.rx_bytes is None:
            connection.pop("rxbytes", None)
        else:
            connection["rxbytes"] = sample.rx_bytes
        if sample.tx_bytes is None:
            connection.pop("txbytes", None)
        else:
            connection["txbytes"] = sample.tx_bytes
        connection["rxspeed"] = sample.rx_bps
        connection["txspeed"] = sample.tx_bps
        connection["rxpackets"] = sample.rx_packets
        connection["txpackets"] = sample.tx_packets

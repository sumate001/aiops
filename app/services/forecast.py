"""A1b — Chronos time-series forecasting.

A1's Isolation Forest asks "is this point odd compared with the other features?"
It has no concept of time, so a nightly batch job that always spikes error counts
at 02:00 looks anomalous every single night.

This asks a different question: "is this odd compared with *this host's own past
at this time of day*?" A metric inside its forecast band is normal behaviour for
that host at that hour, however large the absolute number looks.

Both run and AA weighs them. Neither replaces the other — they detect different
things, and dropping either one loses real signal.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Chronos was trained on these quantiles; asking for anything beyond 0.9 gets
# extrapolation the model never learned.
QUANTILES = [0.1, 0.5, 0.9]


class Forecaster:
    """Lazy-loaded Chronos pipeline, shared per process."""

    def __init__(self, model: str = "amazon/chronos-bolt-small", device: str = "cpu"):
        self.model_name = model
        self.device = device
        self._pipeline = None
        self._lock = threading.Lock()
        self._failed = False

    def _ensure(self):
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            from chronos import BaseChronosPipeline  # local: heavy import
            import torch

            t0 = time.monotonic()
            self._pipeline = BaseChronosPipeline.from_pretrained(
                self.model_name,
                device_map=self.device,
                torch_dtype=torch.float32,   # CPU has no useful bfloat16 path
            )
            logger.info("Chronos loaded %s on %s in %.1fs", self.model_name,
                        self.device, time.monotonic() - t0)
            return self._pipeline

    def warm_up(self) -> None:
        """Load ahead of the first request — same reasoning as the embedder."""
        try:
            self.predict([[float(i % 5) for i in range(24)]], prediction_length=2)
            logger.info("Chronos warm-up done")
        except Exception as exc:
            self._failed = True
            logger.error("Chronos warm-up failed — forecasting disabled: %s", exc)

    @property
    def ready(self) -> bool:
        return self._pipeline is not None

    def predict(self, series: list[list[float]], prediction_length: int = 12):
        """Forecast several series at once.

        Batched deliberately: one call over N hosts is far cheaper than N calls,
        and this runs on every analysis with every host present.
        """
        import torch

        if not series:
            return []
        pipeline = self._ensure()
        tensors = [torch.tensor(s, dtype=torch.float32) for s in series]
        # Positional `inputs` — chronos-forecasting 2.x renamed it from `context`.
        quantiles, _mean = pipeline.predict_quantiles(
            tensors,
            prediction_length=prediction_length,
            quantile_levels=QUANTILES,
        )
        # (batch, prediction_length, len(QUANTILES))
        return quantiles.detach().cpu().numpy()


_forecaster: Forecaster | None = None
_singleton_lock = threading.Lock()


def get_forecaster() -> Forecaster:
    global _forecaster
    if _forecaster is None:
        with _singleton_lock:
            if _forecaster is None:
                from app.config import config
                _forecaster = Forecaster(
                    model=config.forecast.model, device=config.forecast.device
                )
    return _forecaster


def breach_of(actual: float, p10: float, p50: float, p90: float) -> tuple[bool, float]:
    """Is `actual` outside the predicted band, and by how much?

    Magnitude is expressed in band-widths rather than raw units so it stays
    comparable across metrics: a host whose error count normally swings 0-500 and
    one that sits at 0-5 both read "1.0" when they miss by their own full spread.
    """
    breach = actual < p10 or actual > p90
    width = max(p90 - p10, 1e-6)
    return breach, round(abs(actual - p50) / width, 3)


async def warm_up() -> None:
    from app.config import config

    if not (config.forecast.enabled and config.forecast.warm_up_on_startup):
        return
    import asyncio
    await asyncio.to_thread(get_forecaster().warm_up)

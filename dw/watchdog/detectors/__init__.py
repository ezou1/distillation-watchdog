"""Detector registry: build_detectors() returns the detectors D1, D2, D3, D4, D6 and D7 with their trained models.

D5 (prompt templates) was removed: in this data it measured the prompt length of the source, not templates.

D3 without its model file returns neutral topic features. D4 and D6 without the context model service leave
unscored texts out (the account model then uses the training mean).
"""
from __future__ import annotations

from pathlib import Path

from dw.common.context import scorer
from dw.watchdog.detectors.base import Detector
from dw.watchdog.detectors.coverage import CoverageDetector
from dw.watchdog.detectors.elicitation import ElicitationDetector
from dw.watchdog.detectors.engagement import EngagementDetector
from dw.watchdog.detectors.fake_env import FakeEnvDetector
from dw.watchdog.detectors.redundancy import RedundancyDetector
from dw.watchdog.detectors.training import load_model
from dw.watchdog.detectors.volume import VolumeDetector


def build_detectors(models_dir: Path | None = None) -> list[Detector]:
    """Return the detectors in order. D3 loads its topic model from models_dir; D4 and D6 use the context model."""
    return [
        RedundancyDetector(),
        EngagementDetector(),
        CoverageDetector(load_model(models_dir, "d3")),
        ElicitationDetector(scorer()),
        FakeEnvDetector(scorer()),
        VolumeDetector(),
    ]

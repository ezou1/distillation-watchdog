"""Detector registry: build_detectors() returns the seven detectors D1 to D7 with their trained models.

A detector whose model file does not exist returns neutral features (0.0) for that model.
"""
from __future__ import annotations

from pathlib import Path

from dw.watchdog.detectors.base import Detector
from dw.watchdog.detectors.coverage import CoverageDetector
from dw.watchdog.detectors.elicitation import ElicitationDetector
from dw.watchdog.detectors.engagement import EngagementDetector
from dw.watchdog.detectors.fake_env import FakeEnvDetector
from dw.watchdog.detectors.redundancy import RedundancyDetector
from dw.watchdog.detectors.templates import TemplateDetector
from dw.watchdog.detectors.training import load_model
from dw.watchdog.detectors.volume import VolumeDetector


def build_detectors(models_dir: Path | None = None) -> list[Detector]:
    """Return the detectors D1..D7 in order. Load the D3, D4 and D6 models from models_dir if they exist."""
    return [
        RedundancyDetector(),
        EngagementDetector(),
        CoverageDetector(load_model(models_dir, "d3")),
        ElicitationDetector(load_model(models_dir, "d4")),
        TemplateDetector(),
        FakeEnvDetector(load_model(models_dir, "d6")),
        VolumeDetector(),
    ]

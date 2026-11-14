"""Tests for D5: skeleton masking, template mass, campaign accessors, and the template inversion call."""
from __future__ import annotations

import asyncio

from dw.common.hashing import text_hash
from dw.common.schema import AccountState, RequestEvent
from dw.watchdog.detectors.base import make_view
from dw.watchdog.detectors.templates import (
    TemplateDetector, first_message_hashes, invert_templates, skeleton, template_skeletons,
)


def feed(prompts: list[str]) -> tuple[TemplateDetector, AccountState, int]:
    det, acct, n_ev = TemplateDetector(), AccountState(account_id="a"), 0
    for i, p in enumerate(prompts):
        ev = RequestEvent(request_id=f"r{i}", account_id="a", sim_time=float(i),
                          messages=[{"role": "user", "text": p}])
        n_ev += len(det.observe(make_view(ev, f"s{i}", 0, True), acct))
        n_ev += len(det.observe(make_view(ev, f"s{i}", 1, False), acct))   # same session again
    return det, acct, n_ev


def test_skeleton_masks_variable_parts() -> None:
    a = skeleton('Task for repo r12: fix `parse_3` in src/util.py line 42 ("bad input")')
    b = skeleton('Task for repo r7: fix `load_x` in lib/io.py line 9 ("other")')
    assert a == b
    assert "<path>" in a and "<num>" in a and "<str>" in a


def test_template_mass_separates_generated_from_varied_prompts() -> None:
    gen = [f"Task for repo r{i}: fix issue {i} in pkg/mod_{i}.py\nShow your full reasoning step by step."
           for i in range(8)]
    det, acct, n_ev = feed(gen)
    feats = det.features(acct)
    assert feats["d5_template_mass"] == 1.0 and n_ev >= 1
    assert len(template_skeletons(acct)) == 2
    assert first_message_hashes(acct) == {text_hash(p) for p in gen}
    varied = ["the login page crashes when I click submit twice", "can you add dark mode to the settings",
              "why is my docker build so slow on mac", "rename the user model to account everywhere",
              "write a migration that drops the old table", "help me understand this stack trace please"]
    det2, acct2, _ = feed(varied)
    assert det2.features(acct2)["d5_template_mass"] == 0.0
    assert template_skeletons(acct2) == set()


def test_local_inversion_gives_slots() -> None:
    prompts = [f"Task for repo r{i}: fix issue {i} now please\nSome unique text {i * 7} about the bug zz{i}"
               for i in range(6)]
    _, acct, _ = feed(prompts)
    out = invert_templates(acct)
    assert "{id}" in out and "{num}" in out
    assert "{task}" in out or "task for repo" in out

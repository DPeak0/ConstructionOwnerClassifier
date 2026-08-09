from __future__ import annotations

import os
import uuid

import pytest

from owner_classifier.single_instance import SingleInstanceGuard


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex behavior")
def test_single_instance_guard_blocks_second_instance_and_releases():
    name = f"Local\\ConstructionOwnerClassifier-Test-{uuid.uuid4()}"
    first = SingleInstanceGuard(name)
    second = SingleInstanceGuard(name)
    third = SingleInstanceGuard(name)

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert third.acquire() is True
    third.release()

from __future__ import annotations

import pytest
from aiogram.fsm.storage.memory import MemoryStorage


@pytest.mark.asyncio
async def test_build_storage_requires_redis_outside_explicit_dev(monkeypatch):
    import main

    monkeypatch.setattr(main.settings, "redis_url", None)
    monkeypatch.setattr(main.settings, "allow_memory_fsm_dev", False)

    with pytest.raises(
        RuntimeError,
        match="REDIS_URL is required in production",
    ):
        main.build_storage()

    monkeypatch.setattr(main.settings, "allow_memory_fsm_dev", True)
    storage = main.build_storage()
    assert isinstance(storage, MemoryStorage)
    await storage.close()

    monkeypatch.setattr(main.settings, "allow_memory_fsm_dev", False)
    print("STORAGE|PASS|production_requires_redis|explicit_dev_allows_memory")

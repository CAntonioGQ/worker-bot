import asyncio
from collections import defaultdict

_project_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def lock_for(project: str) -> asyncio.Lock:
    return _project_locks[project]

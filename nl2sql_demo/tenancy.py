"""Which tenant does this request belong to?

The answer is decided once, at the edge of the request, and kept in a
``ContextVar``; everything below reads it from there instead of passing it
through every function. Two rules make this safe:

* No tenant bound -> refuse. Never fall back to "the default database".
* Worker threads do not inherit ``ContextVar`` values. Background work must be
  submitted through :func:`submit`, which copies the caller's context.
"""
from __future__ import annotations

import contextvars
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


@dataclass(frozen=True)
class Tenant:
    code: str
    db_path: Path
    dialect: str = "sqlite"


class NoTenant(RuntimeError):
    """Raised when code asks for the current tenant outside a tenant scope."""


_current: contextvars.ContextVar[Tenant | None] = contextvars.ContextVar("current_tenant", default=None)


@contextmanager
def tenant_scope(tenant: Tenant) -> Iterator[Tenant]:
    token = _current.set(tenant)
    try:
        yield tenant
    finally:
        _current.reset(token)


def current() -> Tenant:
    tenant = _current.get()
    if tenant is None:
        raise NoTenant("no tenant is bound to this request; refusing to guess a database")
    return tenant


def connect() -> sqlite3.Connection:
    """Open the current tenant's database read-only (the demo's stand-in for a read-only account)."""
    tenant = current()
    uri = Path(tenant.db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tenant-worker")


def submit(fn: Callable, *args, **kwargs) -> Future:
    """Run ``fn`` in a worker thread with the caller's context (and so the caller's tenant)."""
    ctx = contextvars.copy_context()
    return _pool.submit(ctx.run, fn, *args, **kwargs)

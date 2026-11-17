"""Esecuzione di I/O bloccante senza mai bloccare l'app.

Perché: su cartelle sincronizzate (iCloud Drive, Dropbox, dischi di rete) una semplice
`read()` può restare ferma per minuti o per sempre. Per questo:
- il lavoro gira in thread *daemon* (non impediscono l'uscita dal programma);
- ogni attesa ha un timeout: allo scadere il file viene saltato e segnalato;
- `asyncio.to_thread` NON va usato per I/O su file utente: all'uscita asyncio aspetta
  i suoi thread e un thread bloccato impedirebbe di chiudere l'app.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Any, Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")
K = TypeVar("K")


class Cancelled(Exception):
    """L'utente ha interrotto l'operazione."""


class Timeout(Exception):
    """L'operazione non ha risposto entro il tempo massimo."""


def run_blocking(fn: Callable[..., T], *args: Any, timeout: float | None = None) -> "asyncio.Future[T]":
    """Esegue `fn` in un thread daemon e restituisce un awaitable (con timeout opzionale)."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[T] = loop.create_future()

    def deliver(setter: Callable[[Any], None], value: Any) -> None:
        if not fut.done():
            setter(value)

    def work() -> None:
        try:
            res = fn(*args)
        except BaseException as e:  # noqa: BLE001 - trasferito al chiamante
            loop.call_soon_threadsafe(deliver, fut.set_exception, e)
        else:
            loop.call_soon_threadsafe(deliver, fut.set_result, res)

    threading.Thread(target=work, daemon=True, name=f"fi-{getattr(fn, '__name__', 'job')}").start()
    if timeout is None:
        return fut
    return asyncio.ensure_future(_with_timeout(fut, timeout))


async def _with_timeout(fut: "asyncio.Future[T]", timeout: float) -> T:
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout)
    except asyncio.TimeoutError as e:
        raise Timeout(f"nessuna risposta dopo {timeout:.0f}s") from e


def call_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    """Versione sincrona: esegue `fn` in un thread daemon e attende al massimo `timeout`."""
    box: dict[str, Any] = {}
    done = threading.Event()

    def work() -> None:
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    if not done.wait(timeout):
        raise Timeout(f"nessuna risposta dopo {timeout:.0f}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def parallel_map(
    fn: Callable[[K], T],
    items: Iterable[K],
    timeout_for: Callable[[K], float],
    workers: int = 4,
    cancel: threading.Event | None = None,
) -> Iterator[tuple[K, T | None, str]]:
    """Applica `fn` in parallelo (thread daemon) con timeout per elemento.

    Restituisce (item, risultato, errore) nell'ordine di completamento. Un elemento che
    supera il timeout produce (item, None, "timeout…") e il suo thread viene abbandonato.
    """
    results: "queue.Queue[tuple[int, Any, str]]" = queue.Queue()
    pending = iter(items)
    inflight: dict[int, tuple[K, float]] = {}
    counter = 0

    def start_next() -> bool:
        nonlocal counter
        try:
            item = next(pending)
        except StopIteration:
            return False
        counter += 1
        tid = counter

        def work() -> None:
            try:
                results.put((tid, fn(item), ""))
            except Exception as e:  # noqa: BLE001
                results.put((tid, None, str(e) or e.__class__.__name__))

        inflight[tid] = (item, time.monotonic() + timeout_for(item))
        threading.Thread(target=work, daemon=True).start()
        return True

    for _ in range(workers):
        if not start_next():
            break

    while inflight:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            tid, value, err = results.get(timeout=0.25)
        except queue.Empty:
            now = time.monotonic()
            for tid, (item, deadline) in list(inflight.items()):
                if now > deadline:
                    del inflight[tid]
                    yield item, None, "timeout: file bloccato (sincronizzazione iCloud/rete?)"
                    start_next()
            continue
        if tid not in inflight:
            continue  # arrivato dopo il timeout: già segnalato
        item, _ = inflight.pop(tid)
        yield item, value, err
        start_next()

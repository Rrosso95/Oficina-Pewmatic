"""Bus de eventos mínimo.

El grafo emite eventos (estado de cada agente, mensajes del debate) y quien
quiera escucharlos se suscribe. En la Fase 2 FastAPI se suscribirá aquí y
los reenviará por WebSocket al frontend animado.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable

# Estados que usará el frontend para animar a los avatares.
INACTIVO = "inactivo"
PENSANDO = "pensando"
EN_REUNION = "en_reunion"
ESPERANDO_APROBACION = "esperando_aprobacion"

# Proyecto (thread_id) en cuyo contexto se emiten los eventos.
proyecto_actual: ContextVar[str | None] = ContextVar("proyecto_actual", default=None)

_listeners: list[Callable[[dict], None]] = []


def subscribe(fn: Callable[[dict], None]) -> None:
    _listeners.append(fn)


def emit(agente: str, estado: str, mensaje: str | None = None, **extra) -> None:
    """Publica un cambio de estado y, opcionalmente, lo que dice el agente."""
    evento = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agente": agente,
        "estado": estado,
        "mensaje": mensaje,
        "proyecto": proyecto_actual.get(),
        **extra,
    }
    for fn in _listeners:
        fn(evento)

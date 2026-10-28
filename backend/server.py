"""API de la Oficina Pewmatic (Fase 2).

    REST   POST /api/ideas                  → el Fundador entrega una idea
           POST /api/proyecto/respuesta     → responde a una pregunta / aprueba / rechaza / modifica
           GET  /api/estado                 → foto completa (agentes, chat, aprobación pendiente)
    WS     /ws                              → eventos en vivo para animar la oficina

Mensajes del WebSocket (JSON, siempre con "type"):
    snapshot  {agentes, chat, pendiente, proyecto, estado}   al conectar
    agente    {agente, estado, mensaje, ts}                  cambio de estado de un avatar
    chat      {agente, texto, ts}                            lo que dice un agente (bocadillo + panel)
    pendiente {payload}                                      el Fundador debe decidir (botones)
    fin       {decision, entregables}                        proyecto terminado
    error     {detalle}

Arrancar:  .venv/bin/uvicorn server:app --reload --port 8000   →   http://localhost:8000
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from oficina import events
from oficina.agents import AGENTES
from oficina.graph import construir_grafo, nueva_idea, responder


# ───────────────────────────── Estado de la oficina ─────────────────────────────
class Oficina:
    """Una oficina = un proyecto activo a la vez (los avatares son los mismos)."""

    def __init__(self) -> None:
        self.grafo = construir_grafo()
        self.sockets: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.reiniciar()

    def reiniciar(self) -> None:
        self.agentes = {
            aid: {"id": aid, "nombre": a.nombre, "rol": a.rol, "estado": events.INACTIVO, "mensaje": None}
            for aid, a in AGENTES.items()
        }
        self.chat: list[dict] = []
        self.pendiente: dict | None = None
        self.proyecto: str | None = None
        self.estado: Literal["libre", "trabajando", "esperando_fundador", "terminado", "error"] = "libre"
        self.final: dict | None = None

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "agentes": list(self.agentes.values()),
            "chat": self.chat,
            "pendiente": self.pendiente,
            "proyecto": self.proyecto,
            "estado": self.estado,
            "final": self.final,
        }

    # — difusión (se llama desde hilos del grafo, por eso run_coroutine_threadsafe) —
    def difundir(self, msg: dict) -> None:
        if self.loop and self.sockets:
            asyncio.run_coroutine_threadsafe(self._enviar(msg), self.loop)

    async def _enviar(self, msg: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                self.sockets.discard(ws)

    def on_evento(self, e: dict) -> None:
        if e["proyecto"] != self.proyecto:  # evento de un proyecto antiguo
            return
        ag = self.agentes[e["agente"]]
        ag["estado"], ag["mensaje"] = e["estado"], e["mensaje"] or ag["mensaje"]
        self.difundir({"type": "agente", **{k: e[k] for k in ("agente", "estado", "mensaje", "ts")}})
        # Un mensaje en estado en_reunion es una intervención del debate → chat.
        if e["mensaje"] and e["estado"] == events.EN_REUNION and e["mensaje"] != "Voy a la sala.":
            self._chat(e["agente"], e["mensaje"], e["ts"])
        elif e["mensaje"] and e["estado"] in (events.ESPERANDO_APROBACION, events.PENSANDO):
            self._chat(e["agente"], e["mensaje"], e["ts"])

    def _chat(self, agente: str, texto: str, ts: str) -> None:
        item = {"agente": agente, "texto": texto, "ts": ts}
        self.chat.append(item)
        self.difundir({"type": "chat", **item})

    # — ejecución del grafo (síncrona, en un hilo) —
    def ejecutar(self, fn) -> None:
        mio = self.proyecto
        events.proyecto_actual.set(mio)
        self.estado = "trabajando"
        self.pendiente = None
        try:
            res = fn()
            if self.proyecto != mio:  # el Fundador reinició mientras trabajábamos
                return
        except Exception as ex:
            if self.proyecto != mio:
                return  # clave inválida, sin saldo, red…
            self.estado = "error"
            for a in self.agentes.values():
                a["estado"] = events.INACTIVO
            self.difundir(self.snapshot())
            self.difundir({"type": "error", "detalle": f"{type(ex).__name__}: {ex}"})
            return
        if res.get("__interrupt__"):
            self.estado = "esperando_fundador"
            self.pendiente = res["__interrupt__"][0].value
            self.difundir({"type": "pendiente", "payload": self.pendiente})
        else:
            self.estado = "terminado"
            self.final = {"decision": res.get("decision"), "entregables": res.get("entregables", {})}
            self.difundir({"type": "fin", **self.final})


oficina = Oficina()


@asynccontextmanager
async def lifespan(_: FastAPI):
    oficina.loop = asyncio.get_running_loop()
    events.subscribe(oficina.on_evento)
    yield


app = FastAPI(title="Oficina Pewmatic", lifespan=lifespan)
app.add_middleware(  # el frontend se servirá desde otro puerto en desarrollo
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ───────────────────────────── REST ─────────────────────────────
class IdeaIn(BaseModel):
    idea: str


class RespuestaIn(BaseModel):
    # Para preguntas del CPO: `respuesta`. Para aprobaciones: `decision` (+ `comentario`).
    respuesta: str | None = None
    decision: Literal["aprobar", "rechazar", "modificar"] | None = None
    comentario: str = ""


@app.get("/api/estado")
def estado() -> dict:
    return oficina.snapshot()


@app.post("/api/ideas", status_code=202)
async def crear_idea(body: IdeaIn) -> dict:
    if oficina.estado in ("trabajando", "esperando_fundador"):
        raise HTTPException(409, "Ya hay un proyecto en curso. Termínalo o reinícialo.")
    if not body.idea.strip():
        raise HTTPException(422, "La idea no puede estar vacía.")
    oficina.reiniciar()
    oficina.proyecto = str(uuid.uuid4())
    oficina._chat("fundador", body.idea, events.datetime.now(events.timezone.utc).isoformat())
    asyncio.create_task(
        asyncio.to_thread(oficina.ejecutar, lambda: nueva_idea(oficina.grafo, body.idea, oficina.proyecto))
    )
    oficina.estado = "trabajando"
    oficina.difundir(oficina.snapshot())
    return {"proyecto": oficina.proyecto}


@app.post("/api/proyecto/respuesta", status_code=202)
async def responder_fundador(body: RespuestaIn) -> dict:
    if oficina.estado != "esperando_fundador" or not oficina.pendiente:
        raise HTTPException(409, "No hay nada pendiente de tu decisión.")
    if oficina.pendiente["tipo"] == "preguntas":
        if not body.respuesta:
            raise HTTPException(422, "Falta `respuesta`.")
        valor: str | dict = body.respuesta
        resumen = body.respuesta
    else:
        if not body.decision:
            raise HTTPException(422, "Falta `decision`.")
        if body.decision == "modificar" and not body.comentario.strip():
            raise HTTPException(422, "Para modificar indica qué cambiar en `comentario`.")
        valor = {"decision": body.decision, "comentario": body.comentario}
        resumen = f"[{body.decision.upper()}] {body.comentario}".strip()
    oficina._chat("fundador", resumen, events.datetime.now(events.timezone.utc).isoformat())
    asyncio.create_task(
        asyncio.to_thread(oficina.ejecutar, lambda: responder(oficina.grafo, oficina.proyecto, valor))
    )
    oficina.estado = "trabajando"
    oficina.pendiente = None
    return {"ok": True}


@app.post("/api/proyecto/reiniciar")
async def reiniciar() -> dict:
    """Abandona el proyecto actual (el hilo en curso, si lo hay, termina pero se ignora)."""
    oficina.reiniciar()
    oficina.difundir(oficina.snapshot())
    return {"ok": True}


# ───────────────────────────── WebSocket ─────────────────────────────
@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    oficina.sockets.add(socket)
    await socket.send_json(oficina.snapshot())
    try:
        while True:
            await socket.receive_text()  # solo mantenemos la conexión; el cliente usa REST
    except WebSocketDisconnect:
        pass
    finally:
        oficina.sockets.discard(socket)


# ───────────────────────────── Frontend (Fase 3) ─────────────────────────────
# Se monta al final para que /api y /ws tengan prioridad. Abre http://localhost:8000
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")

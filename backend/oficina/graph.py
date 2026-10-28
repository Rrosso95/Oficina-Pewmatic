"""Flujo de trabajo de la oficina como grafo LangGraph con Human-in-the-loop.

    analizar ──► preguntar* ──► convocar ──► debatir ──► sintetizar ──► aprobar_enfoque
                                               ▲                            │
                                               └──── modificar ◄────────────┤
                                                                            ▼ aprobar
                                      aprobar_entregables ◄── producir ◄────┘
                                              │   ▲
                                              └───┴─ modificar (vuelve a producir)

  * Los nodos `preguntar`, `aprobar_enfoque` y `aprobar_entregables` llaman a
    `interrupt()`: el grafo se congela (checkpoint) hasta que el Fundador responde
    con `Command(resume=...)`.
  * Regla importante de LangGraph: al reanudar, el nodo interrumpido se re-ejecuta
    desde el principio. Por eso esos nodos NO llaman al LLM: solo preguntan y
    devuelven la respuesta. Todo el trabajo caro vive en nodos anteriores.
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from . import events
from .agents import AGENTES, ESPECIALISTAS, estructurado, hablar

MAX_PREGUNTAS = 3


# ───────────────────────────── Estado ─────────────────────────────
class Estado(TypedDict, total=False):
    idea: str
    preguntas: list[str]
    respuestas: str
    convocados: list[str]
    debate: Annotated[list[dict], operator.add]  # {"agente", "texto"}; se acumula
    plan: dict
    entregables: dict[str, str]
    feedback: str  # comentario del Fundador al pedir "modificar"
    decision: Literal["aprobar", "rechazar", "modificar"]
    fase: str


# ───────────────────── Esquemas de salida estructurada ─────────────────────
class Analisis(BaseModel):
    preguntas: list[str] = Field(
        default_factory=list,
        description=f"0 a {MAX_PREGUNTAS} preguntas estratégicas, solo si la idea es muy amplia.",
    )
    convocados: list[Literal["viabilidad", "tecnico", "marketing"]] = Field(
        description="Especialistas relevantes para esta idea."
    )


class Plan(BaseModel):
    resumen: str = Field(description="Resumen del enfoque acordado en 3-5 líneas.")
    modulos: list[str] = Field(description="Módulos ejecutables, cada uno con responsable y objetivo.")
    riesgos: list[str]
    propone_pivot: bool = Field(
        description="True solo si el debate concluye que hay que cambiar la idea original."
    )
    motivo_pivot: str = ""


# ───────────────────────────── Utilidades ─────────────────────────────
def _transcripcion(debate: list[dict]) -> str:
    return "\n\n".join(f"[{AGENTES[d['agente']].nombre}]: {d['texto']}" for d in debate)


def _pide_decision(payload: dict) -> tuple[str, str]:
    """Pausa el grafo y devuelve (decision, comentario) cuando el Fundador responde."""
    r = interrupt(payload)
    if isinstance(r, str):
        r = {"decision": r}
    decision = r.get("decision", "aprobar")
    if decision not in ("aprobar", "rechazar", "modificar"):
        decision = "modificar"
        r["comentario"] = f"{r.get('decision')} {r.get('comentario', '')}".strip()
    return decision, r.get("comentario", "")


# ───────────────────────────── Nodos ─────────────────────────────
def analizar(state: Estado) -> dict:
    events.emit("cpo", events.PENSANDO, "Analizando tu idea…")
    a = estructurado(
        AGENTES["cpo"],
        Analisis,
        "Analiza la idea del Fundador. Si es muy amplia, formula preguntas estratégicas para "
        "acotarla; si ya es concreta, no preguntes nada. Elige qué especialistas convocar.",
        contexto=f"IDEA DEL FUNDADOR:\n{state['idea']}",
    )
    convocados = [c for c in a.convocados if c in ESPECIALISTAS] or ESPECIALISTAS
    preguntas = a.preguntas[:MAX_PREGUNTAS]
    if preguntas:
        events.emit("cpo", events.ESPERANDO_APROBACION, "Necesito aclarar un par de cosas contigo.")
    return {"preguntas": preguntas, "convocados": convocados, "fase": "indagacion"}


def preguntar(state: Estado) -> dict:
    """Fase 1 (indagación). Nodo puro: solo interrumpe."""
    respuesta = interrupt({"tipo": "preguntas", "agente": "cpo", "preguntas": state["preguntas"]})
    texto = respuesta if isinstance(respuesta, str) else respuesta.get("respuesta", "")
    events.emit("cpo", events.PENSANDO, "Gracias, con eso afino el plan.")
    return {"respuestas": texto}


def tras_analisis(state: Estado) -> str:
    return "preguntar" if state.get("preguntas") else "convocar"


def convocar(state: Estado) -> dict:
    """Todos los convocados caminan a la sala de brainstorming."""
    events.emit("cpo", events.EN_REUNION, "Convoco reunión de equipo.")
    for aid in state["convocados"]:
        events.emit(aid, events.EN_REUNION, "Voy a la sala.")
    return {"fase": "debate"}


def debatir(state: Estado) -> dict:
    """Fase 2: mesa redonda. Cada especialista habla viendo lo dicho antes."""
    contexto = f"IDEA: {state['idea']}"
    if state.get("respuestas"):
        contexto += f"\nACLARACIONES DEL FUNDADOR: {state['respuestas']}"
    if state.get("feedback"):
        contexto += f"\nCORRECCIONES DEL FUNDADOR AL PLAN ANTERIOR (tenlas muy en cuenta): {state['feedback']}"

    nuevos: list[dict] = []
    for aid in state["convocados"]:
        events.emit(aid, events.PENSANDO)
        previo = _transcripcion(state.get("debate", []) + nuevos)
        texto = hablar(
            AGENTES[aid],
            "Es tu turno en la mesa redonda. Aporta tu perspectiva sobre la idea.",
            contexto=contexto + (f"\n\nDEBATE HASTA AHORA:\n{previo}" if previo else ""),
        )
        nuevos.append({"agente": aid, "texto": texto})
        events.emit(aid, events.EN_REUNION, texto)
    return {"debate": nuevos, "feedback": ""}


def sintetizar(state: Estado) -> dict:
    """Fase 3: el CPO convierte el debate en un plan por módulos."""
    events.emit("cpo", events.PENSANDO, "Sintetizando el plan…")
    plan = estructurado(
        AGENTES["cpo"],
        Plan,
        "Sintetiza el debate en un plan ejecutable dividido en módulos. Si el equipo concluye "
        "que la idea original no se sostiene, marca propone_pivot y explica por qué.",
        contexto=f"IDEA: {state['idea']}\n\nDEBATE:\n{_transcripcion(state['debate'])}",
    )
    # Pausa de espera: todos van a la mesa del Fundador.
    for aid in ["cpo", *state["convocados"]]:
        events.emit(aid, events.ESPERANDO_APROBACION)
    events.emit("cpo", events.ESPERANDO_APROBACION, plan.resumen)
    return {"plan": plan.model_dump(), "fase": "aprobacion_enfoque"}


def aprobar_enfoque(state: Estado) -> dict:
    """Puerta 1 (y puerta 3 si el plan propone pivotar la idea)."""
    plan = state["plan"]
    decision, comentario = _pide_decision(
        {
            "tipo": "cambio_de_rumbo" if plan["propone_pivot"] else "aprobacion_enfoque",
            "agente": "cpo",
            "plan": plan,
        }
    )
    for aid in ["cpo", *state["convocados"]]:
        events.emit(aid, events.PENSANDO if decision != "rechazar" else events.INACTIVO)
    return {"decision": decision, "feedback": comentario}


def tras_enfoque(state: Estado) -> str:
    return {"aprobar": "producir", "modificar": "debatir", "rechazar": END}[state["decision"]]


def producir(state: Estado) -> dict:
    """Cada especialista elabora su entregable según el plan aprobado."""
    plan = state["plan"]
    contexto = (
        f"IDEA: {state['idea']}\n\nPLAN APROBADO:\n{plan['resumen']}\nMódulos:\n- "
        + "\n- ".join(plan["modulos"])
    )
    if state.get("feedback"):
        contexto += f"\n\nCORRECCIONES DEL FUNDADOR AL ENTREGABLE ANTERIOR: {state['feedback']}"

    tareas = {
        "viabilidad": "Redacta el informe de viabilidad: mercado, competencia, modelo de negocio, "
        "números clave y qué validar primero.",
        "tecnico": "Redacta el documento de arquitectura: stack, componentes, flujo de datos, "
        "costes de infraestructura y fases de construcción.",
        "marketing": "Redacta la estrategia de lanzamiento: público, propuesta de valor, copys "
        "principales, canales y calendario de las primeras 4 semanas.",
    }
    entregables: dict[str, str] = {}
    for aid in state["convocados"]:
        events.emit(aid, events.PENSANDO, "Trabajando en mi entregable…")
        entregables[aid] = hablar(
            AGENTES[aid], tareas[aid] + " Puedes extenderte (máx. ~500 palabras).", contexto
        )
    for aid in ["cpo", *state["convocados"]]:
        events.emit(aid, events.ESPERANDO_APROBACION)
    events.emit("cpo", events.ESPERANDO_APROBACION, "Entregables listos para tu revisión.")
    return {"entregables": entregables, "fase": "aprobacion_entregables"}


def aprobar_entregables(state: Estado) -> dict:
    """Puerta 2: visto bueno a los entregables clave."""
    decision, comentario = _pide_decision(
        {"tipo": "aprobacion_entregables", "agente": "cpo", "entregables": state["entregables"]}
    )
    for aid in ["cpo", *state["convocados"]]:
        events.emit(aid, events.PENSANDO if decision == "modificar" else events.INACTIVO)
    return {"decision": decision, "feedback": comentario, "fase": "fin" if decision != "modificar" else "produccion"}


def tras_entregables(state: Estado) -> str:
    return "producir" if state["decision"] == "modificar" else END


# ───────────────────────────── Construcción ─────────────────────────────
def construir_grafo(checkpointer=None):
    g = StateGraph(Estado)
    for nombre, fn in [
        ("analizar", analizar),
        ("preguntar", preguntar),
        ("convocar", convocar),
        ("debatir", debatir),
        ("sintetizar", sintetizar),
        ("aprobar_enfoque", aprobar_enfoque),
        ("producir", producir),
        ("aprobar_entregables", aprobar_entregables),
    ]:
        g.add_node(nombre, fn)

    g.add_edge(START, "analizar")
    g.add_conditional_edges("analizar", tras_analisis, ["preguntar", "convocar"])
    g.add_edge("preguntar", "convocar")
    g.add_edge("convocar", "debatir")
    g.add_edge("debatir", "sintetizar")
    g.add_edge("sintetizar", "aprobar_enfoque")
    g.add_conditional_edges("aprobar_enfoque", tras_enfoque, ["producir", "debatir", END])
    g.add_edge("producir", "aprobar_entregables")
    g.add_conditional_edges("aprobar_entregables", tras_entregables, ["producir", END])

    # MemorySaver: válido para desarrollo. En producción, SqliteSaver/PostgresSaver
    # para que una aprobación pendiente sobreviva a un reinicio del servidor.
    return g.compile(checkpointer=checkpointer or MemorySaver())


def nueva_idea(grafo, idea: str, thread_id: str) -> dict:
    """Arranca un proyecto. Devuelve el estado o una interrupción pendiente."""
    return grafo.invoke({"idea": idea}, {"configurable": {"thread_id": thread_id}})


def responder(grafo, thread_id: str, respuesta: str | dict) -> dict:
    """Reanuda el grafo con la decisión del Fundador."""
    return grafo.invoke(Command(resume=respuesta), {"configurable": {"thread_id": thread_id}})

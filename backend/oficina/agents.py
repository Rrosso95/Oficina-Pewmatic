"""Definición de los agentes y el helper que los hace "hablar" con herramientas."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .tools import buscar_web, calcular_costes_mensuales, calcular_punto_equilibrio

load_dotenv()

MODELO = os.getenv("OFICINA_MODEL", "claude-sonnet-5-5")
MAX_RONDAS_HERRAMIENTAS = 3


@dataclass(frozen=True)
class Agente:
    id: str
    nombre: str
    rol: str
    persona: str  # system prompt
    tools: tuple = field(default_factory=tuple)


REGLAS_COMUNES = (
    "Hablas en español, en primera persona, de forma concreta y breve (máx. 150 palabras "
    "salvo que se te pida un entregable). Si otro agente ya dijo algo, reacciona a ello: "
    "apóyalo, matízalo o discrepa con argumentos. No inventes datos: si no los tienes, "
    "usa tus herramientas o di que es una suposición."
)

AGENTES: dict[str, Agente] = {
    "cpo": Agente(
        id="cpo",
        nombre="Director de Innovación (CPO)",
        rol="Interlocutor principal del Fundador",
        persona=(
            "Eres el Director de Innovación / CPO de Pewmatic. Escuchas la idea del Fundador, "
            "la afinas, decides qué especialistas convocar, moderas el debate y sintetizas "
            "un plan ejecutable por módulos. Eres claro, honesto y orientado a ejecutar. "
            + REGLAS_COMUNES
        ),
    ),
    "viabilidad": Agente(
        id="viabilidad",
        nombre="Analista de Viabilidad y Estrategia",
        rol="Mercado, modelo de negocio y competencia",
        persona=(
            "Eres el Analista de Viabilidad y Estrategia. Investigas mercado, competencia, "
            "modelo de negocio y números básicos (costes, punto de equilibrio). Eres escéptico "
            "por oficio: señalas riesgos y qué habría que validar primero. " + REGLAS_COMUNES
        ),
        tools=(buscar_web, calcular_punto_equilibrio),
    ),
    "tecnico": Agente(
        id="tecnico",
        nombre="Arquitecto Técnico",
        rol="Stack, infraestructura y flujo de datos",
        persona=(
            "Eres el Arquitecto Técnico. Propones el stack mínimo que valide la idea, la "
            "arquitectura, los flujos de datos y una estimación de costes de infraestructura. "
            "Prefieres lo simple y barato antes que lo sofisticado. " + REGLAS_COMUNES
        ),
        tools=(buscar_web, calcular_costes_mensuales),
    ),
    "marketing": Agente(
        id="marketing",
        nombre="Marketing y Crecimiento",
        rol="Lanzamiento, narrativa y canales",
        persona=(
            "Eres el responsable de Marketing y Crecimiento. Defines público objetivo, "
            "propuesta de valor, narrativa, canales de adquisición y un plan de lanzamiento "
            "realista con presupuesto bajo. " + REGLAS_COMUNES
        ),
        tools=(buscar_web,),
    ),
}

ESPECIALISTAS = [a for a in AGENTES if a != "cpo"]


def _texto(contenido) -> str:
    """Normaliza el contenido de un AIMessage (str o lista de bloques) a texto."""
    if isinstance(contenido, str):
        return contenido
    return "".join(b.get("text", "") for b in contenido if isinstance(b, dict))


def _llm() -> ChatAnthropic:
    return ChatAnthropic(model=MODELO, max_tokens=2048)


def hablar(agente: Agente, instruccion: str, contexto: str = "") -> str:
    """El agente responde a `instruccion`, pudiendo usar sus herramientas."""
    mensajes = [
        SystemMessage(content=agente.persona),
        HumanMessage(content=f"{contexto}\n\n---\n{instruccion}" if contexto else instruccion),
    ]
    llm = _llm()
    if not agente.tools:
        return _texto(llm.invoke(mensajes).content).strip()

    por_nombre = {t.name: t for t in agente.tools}
    llm_tools = llm.bind_tools(list(agente.tools))
    for _ in range(MAX_RONDAS_HERRAMIENTAS):
        resp = llm_tools.invoke(mensajes)
        if not resp.tool_calls:
            return _texto(resp.content).strip()
        mensajes.append(resp)
        for call in resp.tool_calls:
            try:
                salida = por_nombre[call["name"]].invoke(call["args"])
            except Exception as e:
                salida = f"Error en la herramienta: {e}"
            mensajes.append(ToolMessage(content=str(salida), tool_call_id=call["id"]))
    # Se agotaron las rondas de herramientas: obliga a concluir sin ellas.
    return _texto(llm.invoke(mensajes).content).strip()


def estructurado(agente: Agente, esquema, instruccion: str, contexto: str = ""):
    """Respuesta del agente forzada a un esquema Pydantic (sin herramientas)."""
    salida = _llm().with_structured_output(esquema)
    return salida.invoke(
        [
            SystemMessage(content=agente.persona),
            HumanMessage(content=f"{contexto}\n\n---\n{instruccion}" if contexto else instruccion),
        ]
    )

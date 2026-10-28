"""Herramientas que los agentes pueden invocar (tool calling)."""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def buscar_web(consulta: str) -> str:
    """Busca en la web información de mercado, competencia o tecnología.
    Devuelve los 5 mejores resultados (título, URL y extracto)."""
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        resultados = list(ddgs.text(consulta, max_results=5))
    except Exception as e:  # sin red, rate limit, paquete ausente...
        return f"Búsqueda no disponible ({e}). Responde con tu conocimiento y dilo."
    if not resultados:
        return "Sin resultados."
    return "\n".join(f"- {r['title']} ({r['href']}): {r['body']}" for r in resultados)


@tool
def calcular_costes_mensuales(partidas: dict[str, float]) -> str:
    """Suma costes mensuales en euros. `partidas` = {"concepto": euros_al_mes}."""
    total = sum(partidas.values())
    detalle = ", ".join(f"{k}: {v:.0f}€" for k, v in partidas.items())
    return f"Total mensual: {total:.0f}€ ({detalle}). Anual: {total * 12:.0f}€."


@tool
def calcular_punto_equilibrio(
    precio_unitario: float, coste_variable_unitario: float, costes_fijos_mensuales: float
) -> str:
    """Calcula cuántas ventas/clientes al mes hacen falta para cubrir costes fijos."""
    margen = precio_unitario - coste_variable_unitario
    if margen <= 0:
        return "Margen unitario <= 0: el modelo no es viable con esos números."
    unidades = costes_fijos_mensuales / margen
    return (
        f"Margen unitario: {margen:.2f}€. "
        f"Punto de equilibrio: {unidades:.0f} unidades/mes ({unidades * precio_unitario:.0f}€/mes)."
    )

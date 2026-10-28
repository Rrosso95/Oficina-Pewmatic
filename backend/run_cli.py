"""Prueba de la Fase 1 por terminal:  python run_cli.py "tu idea"."""
from __future__ import annotations

import sys
import uuid

from oficina import events
from oficina.graph import construir_grafo, nueva_idea, responder

ICONOS = {"inactivo": "💤", "pensando": "🤔", "en_reunion": "🗣️", "esperando_aprobacion": "🟡"}


def pintar(e: dict) -> None:
    linea = f"{ICONOS[e['estado']]} {e['agente']:<10} {e['estado']}"
    if e["mensaje"]:
        linea += f"\n     └─ {e['mensaje']}"
    print(linea)


def pedir(interr: dict) -> str | dict:
    tipo = interr["tipo"]
    print("\n" + "=" * 70)
    if tipo == "preguntas":
        print("El CPO necesita aclarar:")
        for i, p in enumerate(interr["preguntas"], 1):
            print(f"  {i}. {p}")
        return input("\nTu respuesta: ")

    if tipo in ("aprobacion_enfoque", "cambio_de_rumbo"):
        p = interr["plan"]
        print("⚠️  CAMBIO DE RUMBO PROPUESTO: " + p["motivo_pivot"] if p["propone_pivot"] else "PLAN PROPUESTO")
        print(p["resumen"], "\n\nMódulos:")
        print("\n".join(f"  • {m}" for m in p["modulos"]))
        print("\nRiesgos:\n" + "\n".join(f"  ! {r}" for r in p["riesgos"]))
    else:
        for aid, texto in interr["entregables"].items():
            print(f"\n── ENTREGABLE: {aid} ──\n{texto}")

    d = ""
    while d not in ("aprobar", "rechazar", "modificar"):
        d = input("\n[aprobar / rechazar / modificar]: ").strip().lower()
    c = input("Comentario: ") if d == "modificar" else ""
    return {"decision": d, "comentario": c}


def main() -> None:
    idea = " ".join(sys.argv[1:]) or input("Tu idea: ")
    events.subscribe(pintar)
    grafo = construir_grafo()
    hilo = str(uuid.uuid4())

    res = nueva_idea(grafo, idea, hilo)
    while res.get("__interrupt__"):
        res = responder(grafo, hilo, pedir(res["__interrupt__"][0].value))
    print("\n✅ Proceso terminado. Estado final:", res.get("decision", "—"))


if __name__ == "__main__":
    main()

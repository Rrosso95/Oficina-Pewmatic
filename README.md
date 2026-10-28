# Oficina Pewmatic

Incubadora de proyectos con un equipo de agentes de IA (CPO, Viabilidad, Técnico, Marketing) que debaten tu idea en una oficina pixel-art animada. **Nada avanza sin tu aprobación.**

- `backend/` — agentes con LangGraph + Claude, y API FastAPI con WebSocket.
- `frontend/` — oficina 2D en Canvas (un único `index.html`, sin build).

## Arrancar

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # pon tu ANTHROPIC_API_KEY
uvicorn server:app --port 8000
```

Abre http://localhost:8000. También hay una versión por terminal: `python run_cli.py "tu idea"`.

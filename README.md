# worker-bot

Bot de Telegram que actúa como un "trabajador junior": recibe instrucciones por chat, las ejecuta con [Aider](https://aider.chat) contra uno o varios proyectos locales, y puede ejecutar tareas proactivas en horarios programados (crons).

El objetivo es poder mover varios proyectos desde el celular, sin abrir la laptop, usando modelos baratos (DeepSeek vía OpenRouter) en lugar de la API de Claude/OpenAI.

---

## Estado actual

| Fase | Descripción | Estado |
|------|-------------|--------|
| 0 | Prerrequisitos (Python, uv, credenciales) | ✅ |
| 1 | PoC end-to-end: Telegram ↔ Aider ↔ DeepSeek | ✅ |
| 2 | Multi-sesión y cambio de proyecto | ✅ |
| 3 | Crons proactivos con APScheduler | ✅ |
| 4 | Hardening: multi-usuario, autoarranque, logs rotativos | Pendiente |

---

## Arquitectura

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│   Telegram   │────▶│    bot.py       │────▶│ aider_runner │
│   (cliente)  │◀────│  (handlers)     │◀────│  (subprocess)│
└──────────────┘     └────────┬────────┘     └──────┬───────┘
                              │                     │
                     ┌────────┴────────┐            ▼
                     │   db.py         │    ┌──────────────┐
                     │  (SQLite)       │    │  OpenRouter  │
                     │  sessions.db    │    │  DeepSeek V3 │
                     └─────────────────┘    └──────────────┘
                              │
                     ┌────────┴────────┐
                     │   crons.py      │
                     │  (APScheduler)  │
                     └─────────────────┘
```

- **bot.py**: handlers de Telegram (comandos y mensajes de texto).
- **aider_runner.py**: invoca Aider como subprocess, captura y limpia su salida.
- **db.py**: SQLite con estado por chat (`chat_state`) y crons (`crons`).
- **crons.py**: `AsyncIOScheduler` de APScheduler, zona horaria `America/Mexico_City`.
- **locks.py**: locks async por proyecto para evitar invocaciones concurrentes de Aider sobre el mismo repo.

Cada mensaje de Telegram se enruta al proyecto "activo" del chat. Aider corre como subprocess con `--message` (one-shot) pero mantiene historial entre llamadas gracias a su archivo `.aider.chat.history.md` dentro del repo.

---

## Requisitos

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (gestor de paquetes)
- Cuenta en [OpenRouter](https://openrouter.ai) con API key
- Bot de Telegram (creado con `@BotFather`)
- User ID de Telegram (lo da `@userinfobot`)

---

## Setup

```bash
git clone https://github.com/CAntonioGQ/worker-bot.git
cd worker-bot
uv sync
cp .env.example .env
# Editar .env con tus valores reales (ver siguiente sección)
```

### Configuración (`.env`)

| Variable | Descripción |
|----------|-------------|
| `OPENROUTER_API_KEY` | API key de OpenRouter |
| `TELEGRAM_BOT_TOKEN` | Token del bot (BotFather) |
| `TELEGRAM_ALLOWED_USER_ID` | Tu user ID numérico de Telegram |
| `AIDER_MODEL` | Modelo principal. Default: `openrouter/deepseek/deepseek-chat` |
| `AIDER_WEAK_MODEL` | Modelo para tareas menores. Default: igual al principal |
| `PROJECT_AVI_WEBAPP` | Ruta absoluta al proyecto webapp |
| `PROJECT_AVI_ORCHESTRATOR` | Ruta absoluta al proyecto orchestrator |

Los proyectos se registran en `config.py` bajo `PROJECTS`. Para agregar uno nuevo, añade una variable `PROJECT_*` en `.env` y una entrada en el diccionario.

---

## Uso

Levanta el bot:

```bash
uv run python main.py
```

En Telegram, habla con tu bot:

### Comandos de sesión

| Comando | Descripción |
|---------|-------------|
| `/start` | Mensaje de bienvenida + lista de comandos |
| `/ping` | Responde `pong` (test de vida) |
| `/projects` | Lista proyectos y marca el activo |
| `/current` | Muestra proyecto activo y su ruta |
| `/use <nombre>` | Cambia el proyecto activo |
| `/reset` | Borra el historial de Aider del proyecto activo |

Cualquier mensaje de texto que no sea comando se manda a Aider sobre el proyecto activo.

### Comandos de crons (proactividad)

| Comando | Descripción |
|---------|-------------|
| `/cron_add <proyecto>\|<cron>\|<prompt>` | Programa un cron. Formato cron estándar |
| `/cron_list` | Lista crons con próxima ejecución |
| `/cron_del <id>` | Elimina cron |
| `/cron_run <id>` | Dispara el cron manualmente (test) |

**Ejemplos:**

```
/cron_add webapp|0 9 * * 1-5|Revisa TODOs del proyecto y sugiere 3 tareas prioritarias
/cron_add orchestrator|*/30 * * * *|Lista cambios desde hace 30 minutos
```

**Sintaxis cron** (`min hora día-mes mes día-semana`):

| Expresión | Significado |
|-----------|-------------|
| `* * * * *` | Cada minuto |
| `*/30 * * * *` | Cada 30 minutos |
| `0 9 * * *` | Diario a las 9:00 |
| `0 9 * * 1-5` | 9:00 de lunes a viernes |
| `0 */4 * * *` | Cada 4 horas en punto |

Zona horaria: `America/Mexico_City` (configurable en `crons.py`).

---

## Estructura del proyecto

```
worker-bot/
├── main.py              # entry point
├── bot.py               # handlers de Telegram
├── aider_runner.py      # wrapper subprocess de Aider
├── config.py            # carga .env
├── db.py                # SQLite (chat_state)
├── crons.py             # APScheduler + tabla crons
├── locks.py             # locks por proyecto
├── pyproject.toml       # deps + config de pytest
├── .env                 # secretos (gitignored)
├── .env.example         # plantilla de .env
├── sessions.db          # DB SQLite (gitignored)
└── tests/
    ├── conftest.py
    ├── test_aider_runner.py
    ├── test_db.py
    └── test_crons.py
```

---

## Desarrollo

### Ejecutar tests

```bash
uv run pytest
```

Los tests usan una DB temporal por test (fixture `tmp_db` en `tests/conftest.py`), no tocan `sessions.db` real. Las credenciales del `.env` se sobrescriben con valores dummy antes de importar los módulos.

### Agregar un proyecto nuevo

1. Añade `PROJECT_MI_PROYECTO=...` en `.env`.
2. Añade la entrada en `config.py`:
   ```python
   PROJECTS = {
       "webapp": Path(os.environ["PROJECT_AVI_WEBAPP"]),
       "orchestrator": Path(os.environ["PROJECT_AVI_ORCHESTRATOR"]),
       "mi_proyecto": Path(os.environ["PROJECT_MI_PROYECTO"]),
   }
   ```
3. Reinicia el bot. Úsalo con `/use mi_proyecto` en Telegram.

### Cambiar modelo

Edita `AIDER_MODEL` en `.env`. Cualquier modelo de OpenRouter funciona (`openrouter/<proveedor>/<modelo>`). Opciones baratas recomendadas:

- `openrouter/deepseek/deepseek-chat` — default, buen costo/calidad para código
- `openrouter/qwen/qwen-2.5-coder-32b-instruct` — alternativa open-source
- `openrouter/deepseek/deepseek-chat:free` — gratis con rate limits (20 req/min)

---

## Costo

Modelo default DeepSeek V3:

- Input: ~$0.27 / 1M tokens
- Output: ~$1.10 / 1M tokens
- Costo típico por mensaje al bot: **~$0.002 USD** (según tamaño del repo y tarea)

Un uso cotidiano de 50 mensajes/día cuesta aprox **$3 USD / mes**.

---

## Limitaciones conocidas

- **Windows**: el subprocess de Aider requiere `TERM=dumb` y `PYTHONIOENCODING=utf-8` para evitar problemas de codificación. Ya está configurado en `aider_runner.py`.
- **Multi-usuario**: actualmente el bot solo responde al `TELEGRAM_ALLOWED_USER_ID` del `.env`. Fase 4 lo extiende a lista de usuarios.
- **Tareas paralelas**: mensajes simultáneos al mismo proyecto se serializan (lock). Mensajes a proyectos distintos sí corren en paralelo.
- **Primer scan de Aider**: en repos grandes (800+ archivos) el primer mensaje tarda 30-60s mientras Aider escanea. Siguientes mensajes son más rápidos.

---

## Roadmap

**Fase 4 (pendiente):**
- Lista de usuarios autorizados en `.env`
- Tarea programada de Windows para autoarranque
- Reinicio automático ante crash
- Logs rotativos (`logs/bot.log`)
- Error handler global que manda errores a Telegram

**Futuro (Fase 5+):**
- Deploy a VPS con Docker
- Sesiones concurrentes por contenedor
- Cambio dinámico de modelo por comando
- Integración opcional con Claude Code en modo headless

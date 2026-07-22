# 📬 Triage de Inbox local, con IA y notificaciones a Teams

Un asistente que **ordena tu bandeja de Outlook por ti** usando un modelo de IA que
corre **100% en tu máquina** (nada de nube), y que te pregunta qué hacer con cada
correo mediante una **tarjeta interactiva en Microsoft Teams** —desde el celular o la compu.

> La gracia: el contenido de tus correos **nunca sale de tu equipo**. La IA lee y
> redacta en local (Ollama). A Teams solo viajan remitente + asunto + un extracto corto,
> lo mínimo para que tú decidas de un toque.

No es un producto pulido ni un SaaS: es un proyecto personal que terminó funcionando
sorprendentemente bien y lo comparto por si a alguien le sirve la idea o el código.
Está pensado para un escenario muy concreto (Outlook Desktop en Windows corporativo,
donde no puedes registrar apps ni abrir puertos), pero las piezas se reusan fácil.

---

## ¿Qué hace exactamente?

Por cada correo que llega a tu Inbox, el agente:

1. **Lo clasifica en local** (tipo, urgencia, si requiere respuesta) y genera un **resumen** de 2-3 frases.
2. **Decide a qué carpeta iría**, aprendiendo de cómo tú ya archivas tu correo:
   - por **remitente exacto**, luego por **dominio**, y si no, por **parecido semántico** (embeddings).
3. Te manda una **tarjeta a Teams** con botones para:
   - 📁 **Mover** a la carpeta sugerida (o buscar otra en una lista),
   - ✍️ **Redactar una respuesta** (la escribe la IA; o escribes tú a grandes rasgos y ella lo pule),
   - ↪️ **Reenviar** con una nota de resumen,
   - 🗑️ **Eliminar** (a la papelera, recuperable),
   - ⚑ **Enseñarle** que ese tipo de correo "requiere tu acción",
   - ⏭️ **Saltar**.
4. **Aprende de tu decisión.** La próxima vez que llegue algo parecido, sugiere mejor.
   Con suficientes confirmaciones del mismo patrón, puede **auto-archivar** (opcional, apagado por defecto).

Todo con **guardarraíles duros**: nunca envía correos solo (todo queda en Borradores),
nunca archiva en papelera/spam, y los **remitentes sensibles siempre te preguntan** (jamás auto).

---

## La idea clave: privacidad primero

El reto real era: *quiero una IA que lea mis correos, pero es una máquina corporativa
y el contenido no puede salir a ningún servicio en la nube.*

La solución:

```
  Outlook (Windows)                      Ollama (IA local)
        │  win32com                            ▲
        ▼                                      │ clasifica / redacta / embeddings
   triage_agent.py  ───────────────────────────┘   (todo en tu máquina, localhost)
        │
        │  escribe una tarjeta (solo: remitente + asunto + extracto de 600 chars)
        ▼
   OneDrive/TriageBridge/requests/*.json
        │  (se sincroniza)
        ▼
   Power Automate  ──►  "Publicar tarjeta y esperar respuesta"  ──►  📱 Teams (tú)
        ▲                                                                │ tocas un botón
        │  escribe tu respuesta                                          ▼
   OneDrive/TriageBridge/decisions/*.json  ◄─────────────────────────────
        │
        ▼
   triage_agent.py  ──►  aplica en Outlook (mover / borrador / etc.) + aprende
```

- **La IA (Ollama) corre en local** vía WSL. Lee cuerpos completos, clasifica y redacta.
  Nada de eso toca internet.
- **A tu tenant (Teams/OneDrive) solo llega lo mínimo**: remitente, asunto y un extracto
  corto para que reconozcas el correo. Los cuerpos completos y los borradores se quedan
  en tu máquina; los revisas en Outlook.
- **Cero infraestructura**: sin registrar una app en Azure AD, sin abrir puertos, sin
  endpoint público, sin conectores premium de Power Automate. Solo un archivo que aparece
  en OneDrive dispara un flujo estándar. Esto es lo que lo hace viable en una máquina
  corporativa bloqueada.

> **Nota honesta de privacidad:** "el contenido no sale" aplica al *cuerpo completo*.
> Sí decidí exponer conscientemente remitente + asunto + ~600 caracteres de extracto a mi
> propio tenant corporativo (Teams/OneDrive de la empresa) porque sin eso no puedo decidir
> desde el celular. Ajusta ese límite a tu gusto en `build_card()` (baja el extracto a 0 si quieres).

---

## Cómo aprende (la parte interesante)

No hay entrenamiento ni modelo que reentrenar. Es un sistema de 3 capas + memoria simple:

1. **`from:remitente@exacto`** — si siempre archivas a Fulano en la misma carpeta, gana esto.
2. **`domain:dominio.com`** — si no hay patrón por remitente, mira el dominio.
3. **Semántica (embeddings + votación k-NN)** — para notificaciones tipo `no-reply` donde
   la carpeta depende del *contenido*, no del remitente. Busca los 5 correos más parecidos
   que ya archivaste y, si 3+ coinciden en carpeta, la sugiere.

Cada vez que confirmas, el patrón suma (`confirmed +2`); cada vez que corriges, resta
(`rejected -2`), así una corrección tuya pesa el doble y el sistema se autocorrige rápido.
Un patrón solo se vuelve "limpio" (candidato a auto-archivar) si tiene ≥90% de acuerdo y
casi cero rechazos.

**Autonomía graduada:** `PREGUNTA` → `SUGIERE` → (opcional) `AUTO-ARCHIVA`. El auto-archivar
está **apagado** hasta que creas el archivo `~/.triage/autopilot.on`, y aun así solo actúa
en patrones limpios con 3+ confirmaciones tuyas, nunca en sensibles ni en correos que
requieren acción.

**Truco de arranque:** el modo `--bootstrap` lee tu historial ya archivado (los correos que
ya tienes en cada subcarpeta) y siembra los patrones + embeddings. Así el agente sugiere
bien **desde el día 1** en vez de aprender desde cero.

---

## Componentes

| Archivo | Qué es |
|---|---|
| `triage_agent.py` | Todo el agente: lee Outlook (COM), llama a Ollama, aprende, y corre el puente. |
| `config.example.json` | Config de ejemplo → cópiala a `config.json` con tus dominios y carpetas. |
| `triage-autostart.ps1` | Arranca Ollama + el bridge al iniciar sesión en Windows. |
| `docs/SETUP.md` | Guía paso a paso: WSL, Ollama, venv, bootstrap, arranque automático. |
| `docs/POWER-AUTOMATE.md` | Cómo armar el flujo de Power Automate (con todos los tropiezos ya resueltos). |

---

## Requisitos

- **Windows** con **Outlook Desktop** (clásico, el que expone COM) y tu cuenta ya configurada.
- **WSL2** (Ubuntu) para correr Ollama sin permisos de admin.
- **OneDrive** sincronizado localmente + acceso a **Teams** y **Power Automate** (planes estándar de M365).
- ~8-16 GB de RAM. Funciona en **CPU** (sin GPU); los modelos son chicos.

Modelos usados (ambos locales, se bajan con `ollama pull`):
- `llama3.2:3b` — clasifica y redacta (multilingüe es/en/pt, rápido en CPU).
- `bge-m3` — embeddings multilingües para la similitud semántica.

---

## Arranque rápido

```bash
# 1) Cerebro local (en WSL)
curl -fsSL https://ollama.com/install.sh | sh   # o instálalo user-local, ver docs/SETUP.md
ollama pull llama3.2:3b
ollama pull bge-m3
ollama serve    # queda escuchando en 127.0.0.1:11434

# 2) El agente (en Windows, en un venv de Python de Windows)
py -m venv triage-venv
triage-venv\Scripts\pip install -r requirements.txt

# 3) Configura
copy config.example.json config.json   # y edítalo con tus dominios/carpetas

# 4) Prueba SIN tocar nada (solo lee y muestra qué haría)
py triage_agent.py --dry-run --limit 10

# 5) Siembra el aprendizaje con tu historial
py triage_agent.py --bootstrap --per-folder 30

# 6) Arma el flujo de Power Automate (ver docs/POWER-AUTOMATE.md) y arranca el puente
py triage_agent.py --bridge --batch 5
```

Detalle completo en **[docs/SETUP.md](docs/SETUP.md)** y **[docs/POWER-AUTOMATE.md](docs/POWER-AUTOMATE.md)**.

---

## Seguridad y límites (léelo)

- **Nunca envía correo automáticamente.** Respuestas y reenvíos quedan en **Borradores**; tú los revisas y envías.
- **Nunca archiva a carpetas peligrosas** (papelera, spam, enviados…). "Eliminar" manda a
  Elementos eliminados (recuperable), y solo si tocas ese botón.
- **Remitentes sensibles siempre preguntan.** Configúralos en `config.json` (clientes, tu jefe, RRHH, legal…).
- **La IA es un modelo 3B.** El resumen es para darte contexto, no para actuar a ciegas:
  para cosas consecuentes (fechas, montos), verifica contra el correo real.
- Esto **automatiza tu propio buzón con tu propia sesión de Outlook**. No es una herramienta
  de administración de correo ajeno. Úsalo con tu cuenta y respeta las políticas de tu organización.

---

## ¿Por qué esta arquitectura tan rara?

Porque nació de chocar contra los muros de una máquina corporativa real:

- ❌ No se puede registrar una app en Azure AD → nada de Graph API con app-registration.
- ❌ No se puede abrir un puerto entrante (firewall/GPO) → nada de webhooks ni servidor local expuesto.
- ❌ Telegram estaba bloqueado por la red → se pivoteó a Teams.
- ❌ No hay Power Automate premium → solo triggers/acciones estándar.
- ✅ Pero OneDrive **sí** sincroniza archivos, y "cuando se crea un archivo" **sí** es un
  trigger estándar. Ese hueco es todo el puente.

Si tú *sí* tienes Graph o un endpoint público, puedes reemplazar el puente de OneDrive por
algo más directo —el cerebro (`decide`/`learn`/clasificación) es independiente del canal.

---

Hecho con curiosidad, café y muchos tropiezos. Si lo usas o lo mejoras, me encantaría saberlo. 🙌

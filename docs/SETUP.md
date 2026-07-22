# Guía de instalación paso a paso

Todo esto se puede hacer **sin permisos de administrador**, que es justo lo que se necesita
en una máquina corporativa bloqueada. Toma su tiempo la primera vez; después solo corre.

---

## 1. Ollama (el cerebro local) — dentro de WSL

Si tienes admin, el instalador normal sirve:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Si **no** tienes admin/sudo, instálalo user-local: baja el tarball, descomprímelo en
`~/.local`, y agrega `~/.local/bin` al PATH. Luego:

```bash
# Deja el servidor corriendo (escucha solo en localhost)
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_CONTEXT_LENGTH=8192      # nos basta; ahorra RAM
export OLLAMA_KEEP_ALIVE=30m           # mantiene el modelo en RAM entre correos (no recarga cada vez)
ollama serve &

# Baja los dos modelos
ollama pull llama3.2:3b   # ~2 GB — clasifica y redacta
ollama pull bge-m3        # ~1.2 GB — embeddings semánticos
```

Verifica que responde:

```bash
curl http://localhost:11434/api/version
```

> **Windows ↔ WSL:** Ollama escucha en `127.0.0.1:11434` **dentro de WSL**, y gracias al
> reenvío de localhost de WSL2 también es accesible desde Windows como `localhost:11434`.
> Por eso el agente (que corre en Windows) lo alcanza sin configurar nada de red.
> Sin GPU corre en CPU: clasificar un correo toma ~2-8s, redactar un poco más. Suficiente
> para triage incremental.

---

## 2. Python en Windows (el agente)

**Importante:** el agente usa `win32com` para hablar con Outlook, así que corre en el
**Python de Windows**, no en el de WSL.

```powershell
py -m venv triage-venv
triage-venv\Scripts\pip install -r requirements.txt
```

---

## 3. Configura tus dominios y carpetas

```powershell
copy config.example.json config.json
```

Edita `config.json`:

- `client_domains` — dominios de tus clientes (se tratan como sensibles).
- `internal_domains` — el dominio de tu empresa.
- `sensitive_substrings` — subcadenas que marcan un correo como sensible (nombre de tu jefe,
  `rrhh`, `legal`…). Cualquier remitente sensible **siempre te pregunta**, nunca se auto-archiva.
- El resto (umbrales, modelos) puedes dejarlo como está.

`config.json` está en `.gitignore` — no se sube al repo.

---

## 4. Prueba en seco (no cambia NADA)

```powershell
py triage_agent.py --folders               # lista las subcarpetas de tu Inbox (destinos válidos)
py triage_agent.py --dry-run --limit 10    # clasifica 10 correos y muestra qué haría
```

Si ves clasificaciones y decisiones razonables, vas bien. Nada se movió ni modificó.

> La **primera vez** que se abre Outlook por COM puede aparecer un aviso de "acceso
> programático". Si tu tenant lo permite, acéptalo. Si lo bloquea por política, este
> enfoque no funcionará en tu equipo.

---

## 5. Siembra el aprendizaje con tu historial (bootstrap)

Esto lee los correos que **ya archivaste** en cada subcarpeta y aprende tus patrones, para
que sugiera bien desde el principio:

```powershell
py triage_agent.py --bootstrap --per-folder 30
```

Es reanudable: si lo cortas, retoma donde iba. Con un buzón activo esto siembra cientos de
patrones y embeddings en minutos.

---

## 6. El puente con Teams

Arma el flujo de Power Automate siguiendo **[POWER-AUTOMATE.md](POWER-AUTOMATE.md)** y luego:

```powershell
py triage_agent.py --bridge --batch 5
```

El agente empezará a poner tarjetas en Teams (hasta 5 pendientes a la vez). Toca los botones
desde el celular o el escritorio y observa cómo aplica las acciones en Outlook.

---

## 7. Arranque automático (opcional)

En muchas máquinas corporativas **el Programador de Tareas está bloqueado** (`Acceso denegado`).
El truco que sí funciona sin admin es la **carpeta de Inicio**:

1. Ajusta las rutas en `triage-autostart.ps1` (dónde está el repo y el venv).
2. Abre la carpeta de Inicio: en el Explorador escribe `shell:startup` en la barra de direcciones.
3. Crea ahí un acceso directo que lance el `.ps1`. Como un `.ps1` no se autoejecuta al abrirlo,
   lo más limpio es un pequeño `.vbs` que llame a PowerShell oculto. Ejemplo de `TriageAgent.vbs`:

   ```vbscript
   Set sh = CreateObject("WScript.Shell")
   sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\Users\TU_USUARIO\local-inbox-triage\triage-autostart.ps1""", 0, False
   ```

   Guárdalo dentro de `shell:startup`. Al iniciar sesión levantará Ollama (en WSL) y el bridge.

> Mantén Outlook Desktop abierto para que el COM funcione.

---

## Estado y aprendizaje: dónde vive

Todo el estado se guarda en `~/.triage/` (en Windows: `C:\Users\TU_USUARIO\.triage\`).
**No se sube al repo** (está en `.gitignore`). Archivos principales:

| Archivo | Qué guarda |
|---|---|
| `patterns.json` | patrones remitente/dominio → carpeta (con observed/confirmed/rejected) |
| `vectors.jsonl` | embeddings de correos ya archivados (para la similitud semántica) |
| `decisions.jsonl` | bitácora de cada decisión tuya (auditoría / análisis futuro) |
| `folder_usage.json` | conteo de carpetas más usadas (para ordenarlas en la tarjeta) |
| `seen.json` / `pending.json` | qué ya se procesó / qué está esperando tu respuesta |
| `action_senders.json` | remitentes que enseñaste que "requieren tu acción" |
| `autopilot.on` | si existe, habilita el auto-archivado conservador |

Para **resetear** el aprendizaje, borra estos archivos (o toda la carpeta) y vuelve a hacer bootstrap.

---

## Problemas comunes

- **`Falta config.json`** → copiaste `config.example.json` a `config.json`? Debe estar junto a `triage_agent.py`.
- **No conecta a Ollama** → ¿está `ollama serve` corriendo? Prueba `curl http://localhost:11434/api/version` desde Windows.
- **Los remitentes internos no hacen match con tu dominio** → es normal: Exchange devuelve un
  "DN" en vez del correo. El código ya lo resuelve vía `GetExchangeUser`; si aun así falla,
  revisa que la cuenta esté conectada.
- **Clasificar tarda mucho** → sube `OLLAMA_KEEP_ALIVE` para que no recargue el modelo, y no
  corras otros modelos pesados a la vez (la RAM manda en CPU).

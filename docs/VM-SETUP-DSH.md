# Triaje de inbox con IA local — despliegue en la VM `vm-win11-ltsc`

Estado a fecha de esta sesión. Todo lo aquí descrito **existe y está verificado** en la VM,
salvo lo marcado explícitamente como pendiente.

---

## 1. Arquitectura final (con el desvío obligado por seguridad)

```
   Outlook Desktop (COM)                       <- tus correos, en local
        |
        v
   triage_agent.py  (venv: pywin32 + requests) <- SIN MODIFICAR
        |  config.json -> ollama_url = http://localhost:11434
        v
   llamacpp-ollama-shim.py  (:11434)           <- emula la API de Ollama
        |                    \
        |  /v1/chat/completions\  /api/embed
        v                       v
   llama-server :8080       llama-server :8081
   Qwen2.5-3B (chat)        bge-m3 (embeddings)
```

**Por qué no es Ollama:** el EDR de la VM marcó `OllamaSetup.exe` como
`Trojan:Win32/Tecabans.ST!cl` y lo eliminó. La VM tiene además **WDAC en modo enforced**,
CrowdStrike Falcon, Zscaler, Appgate SDP y Defender con **Tamper Protection activada**.
En lugar de desactivar protecciones corporativas, se cambió el runtime a **llama.cpp**
(portable, sin instalador) y se puso un **shim** que emula los dos endpoints de Ollama que
usa el agente. Resultado: **el repo queda intacto** y `config.json` no cambia.

---

## 2. Qué hay instalado en la VM

| Componente | Ruta | Notas |
|---|---|---|
| Python 3.12.10 (machine-wide) | `C:\Program Files\Python312` | pip 25.0.1, `py.exe` operativo |
| Repo del agente | `C:\Users\mmayet\local-inbox-triage` | con `triage-venv` (pywin32 312, requests 2.34.2) |
| Scripts del paso previo | `...\local-inbox-triage\tools\` | ver sección 4 |
| llama.cpp (build 11132) | `C:\Tools\llama.cpp` | backends AVX-512 (icelake, cascadelake, sapphirerapids) |
| Modelos GGUF | `C:\ProgramData\llm` | 9.1 GB, 5 ficheros |
| venv de laya | `C:\Users\mmayet\laya-venv` | separado, para no contaminar el del agente |

### Modelos descargados

| Modelo | Tamaño | Necesita RAM | Rol |
|---|---|---|---|
| `gemma-3-4b-it-Q4_K_M.gguf` | 2.32 GB | 2.90 GB | **chat activo** (7/8 aciertos medidos) |
| `Qwen2.5-3B-Instruct-Q4_K_M.gguf` | 1.80 GB | 2.25 GB | alternativa rapida (5/8 aciertos, 42 s) |
| `Qwen3-4B-Q4_K_M.gguf` | 2.33 GB | 2.91 GB | descartado: su modo "thinking" lo hace 3x mas lento |
| `Llama-3.2-3B-Instruct-Q4_K_M.gguf` | 1.88 GB | 2.35 GB | default del README del repo |
| `bge-m3-Q8_0.gguf` | 0.59 GB | 0.74 GB | **embeddings activos** (1024 dims) |

Los 5 caben holgadamente en los 16 GB de la VM.

---

## 3. Medición real (lo que pediste como paso previo)

Con `Qwen2.5-3B` clasificando un correo real, usando **el prompt exacto del agente**:

```
latencia        : 39.5 s   (prompt 164 tokens + salida 82 tokens)  ~2.1 tok/s
JSON valido     : SI
respuesta       : {"category":"finance","language":"es","urgency":"high",
                   "requires_reply":true,"resumen":"La factura de marzo fue cobrada dos veces..."}
clasificacion   : CORRECTA (factura duplicada de cliente -> finance / alta / requiere respuesta)
embeddings      : 1024 dimensiones (exacto para bge-m3)
```

### Comparativa de modelos (misma maquina, mismo correo, mismo prompt)

Medido con `tools/compare-models.ps1` (usa el puerto 8090 para no tocar el servicio
`LLMChat` que esta corriendo). Correo de prueba: factura duplicada de un cliente.

| Modelo | Carga | Latencia | tok/s | Tokens salida | Categoria elegida |
|---|---|---|---|---|---|
| **Qwen2.5-3B-Instruct** | 31.2 s | **86.3 s** | 1.34 | **116** | `finance` |
| gemma-3-4b-it | 30.9 s | 127.8 s | 1.39 | 177 | `client_ops` |
| Qwen3-4B | 85.1 s | **262.9 s** | 1.46 | **383** | `client_ops` |

**El hallazgo que importa:** los tres generan a **~1.4 tok/s** — la CPU (1 core fisico) es el
muro real, no la arquitectura del modelo. La diferencia de latencia se explica casi por
completo por **cuantos tokens emite cada uno**:

- Qwen2.5-3B responde conciso (116 tokens) -> 86 s. **Ganador medido.**
- Gemma-3-4B se extiende algo mas (177 tokens) -> 128 s.
- **Qwen3-4B genera 383 tokens**: su modo "thinking" por defecto produce una traza de
  razonamiento larga antes de responder, y eso lo hace **3x mas lento**. Si algun dia se
  quiere usar Qwen3 aqui, hay que **desactivar el thinking** (p.ej. `/no_think`), o usara
  un tercio del presupuesto de tiempo en razonar lo que un 3B resuelve directo.

### Evaluacion de calidad con 8 casos (la que decide)

`tools/eval-models.ps1` pasa 8 correos sinteticos (es/en/pt) con el prompt real del agente y
puntua acierto de categoria (admitiendo un conjunto de etiquetas validas por caso, porque la
taxonomia se solapa), validez del JSON y latencia.

| Modelo | Aciertos categoria | JSON valido | Latencia media |
|---|---|---|---|
| **gemma-3-4b-it** | **7/8** | 8/8 | 73 s |
| Qwen2.5-3B-Instruct | 5/8 | 8/8 | **41.7 s** |

**Esto corrige la conclusion de la comparativa de un solo correo.** Con n=1 parecia que
Qwen2.5-3B ganaba; con 8 casos **Gemma-3-4B es claramente mas preciso** (7/8 frente a 5/8),
aunque sea ~1.75x mas lento. Aplicando el criterio (1. JSON valido — empatan al 100%,
2. aciertos, 3. latencia), **gana Gemma-3-4B**.

Los fallos de Qwen2.5-3B no son aleatorios, tienen patron:

| Caso | Qwen2.5-3B | Gemma-3-4B | Esperado |
|---|---|---|---|
| aviso-password-pt | `hr_admin` ❌ | `notification` ✅ | notification |
| alerta-monitoreo-en | `client_ops` ❌ | `notification` ✅ | notification |
| coordinacion-interna-es | `client_ops` ❌ | `action_required` ✅ | internal_team o action_required |
| factura-cliente-es | `finance` ✅ | `action_required` ❌ | finance o client_ops |

Qwen2.5-3B tiene un sesgo hacia `client_ops`/`hr_admin` y **falla los avisos automaticos y la
coordinacion interna**, que son justo los casos mas frecuentes en una bandeja real.

**Accion tomada:** el modelo de chat activo es ahora `gemma-3-4b-it`. Verificado de punta a
punta por el shim con el caso que Qwen fallaba (alerta de monitorizacion) -> devuelve
`notification` correctamente en 39 s.
Revertir es inmediato: `copy C:\ProgramData\run-chat.ps1.bak C:\ProgramData\run-chat.ps1`
y reiniciar la tarea `LLMChat`.

Cautela: **8 casos sinteticos siguen siendo una muestra pequena**. Antes de fijar el modelo
definitivo, repetir con 20-30 correos REALES cuando Outlook este configurado.

**Interpretacion honesta:** la VM hace bien el trabajo, pero **~40-90 s por correo** (segun
carga concurrente). No son los
2-8 s que sugiere el README, porque el README asume varios nucleos y esta VM tiene
**1 core fisico / 2 hilos**. Para triaje incremental (lotes pequeños) es suficiente; para
procesar cientos de correos del bootstrap tardará horas.

El cuello de botella **ya no es la RAM** (el resize a 16 GB lo resolvió) sino el **vCPU**.
Si algun dia quieres ~2x de velocidad sin tocar el codigo: subir a `Standard_E4as_v5`
(4 vCPU / 32 GB).

---

## 4. El paso previo: validar qué modelo corre aquí

Tres scripts en `tools/`, de solo lectura:

| Script | Qué hace |
|---|---|
| `llm-fit.ps1` | Estima qué tamaño de modelo cabe (RAM libre, GPU, disco, presión de página). Es el puerto a Windows de tu `llm-fit.py`: **el original devuelve 0 GB en Windows** porque lee `/proc/meminfo` (Linux) y `sysctl` (macOS). |
| `validate-local-model.ps1` | Mide con `llama-bench` tokens/s reales de cada GGUF y emite veredicto. |
| `bench-one-email.ps1` | **El más útil**: mide latencia y validez del JSON al clasificar un correo real con el prompt del agente, arrancando y parando el servidor. |

Uso:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\mmayet\local-inbox-triage\tools\llm-fit.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ...\tools\validate-local-model.ps1 -PromptTokens 256 -GenTokens 64 -Reps 1
powershell -NoProfile -ExecutionPolicy Bypass -File ...\tools\bench-one-email.ps1 -Model "C:\ProgramData\llm\Qwen3-4B-Q4_K_M.gguf"
```

Para cambiar de modelo de chat basta editar `C:\ProgramData\run-chat.ps1` y reiniciar la tarea
`LLMChat`.

---

## 5. Servicios y arranque automático

Tres tareas programadas, creadas con `/sc onstart` y ejecutando como SYSTEM (sobreviven a
reinicios, igual que arreglamos antes con Tailscale):

| Tarea | Qué levanta | Puerto | Log |
|---|---|---|---|
| `LLMChat` | llama-server + Qwen2.5-3B | 8080 | `C:\ProgramData\llm-chat.log` |
| `LLMEmbed` | llama-server + bge-m3 | 8081 | `C:\ProgramData\llm-embed.log` |
| `LLMShim` | shim Ollama-compatible | **11434** | `C:\ProgramData\shim.log` |

Verificado (evidencia estatica): las tres tareas estan `Enabled`, corren como `SYSTEM` y su
`Schedule Type` es **`At system start up`**. La prueba empirica (reiniciar y comprobar que
levantan solas) queda pendiente de hacer con tu visto bueno, porque corta la sesion.

Scripts nuevos en `tools/`: `compare-models.ps1` (compara modelos con el mismo correo) y
`bench-one-email.ps1` (mide uno). **Ojo:** `bench-one-email.ps1` usa el puerto **8090**, no el
8080 — si usara el 8080 responderia el servicio ya cargado y mediria otro modelo sin avisar
(era un bug real que se corrigio).

Operación:

```powershell
schtasks /run /tn LLMChat     # arrancar
schtasks /end /tn LLMChat     # parar
Get-Content C:\ProgramData\llm-chat.log -Tail 20
```

**Tiempo de carga de modelo: ~3-4 minutos** la primera vez (el EDR reescanea el GGUF de 2 GB).
Intento de exclusión en Defender: **no es posible**, `IsTamperProtected = True` bloquea
cambios de preferencias. Es el precio de un equipo gestionado.

---

## 6. laya como complemento (tu tercera pregunta)

**Veredicto: sí complementa, pero no como reemplazo del LLM.**

laya (`convaiinnovations/laya`, PyPI `laya`, Apache-2.0) es un motor de decisión
**no autoregresivo**: responde preguntas tipadas en **un solo forward pass**:

| Primitiva | Salida | Dónde encaja en tu agente |
|---|---|---|
| `choice` | etiqueta + probabilidades | `categoria` (las 10 de `VALID_CATS`) |
| `score` | nivel ordinal | `urgencia` |
| `noul` | probabilidad calibrada | gate de sensibilidad / requiere-acción |

**Lo que aporta:** las decisiones dejan de costar una generación completa de tokens. Donde el
LLM tarda ~40 s, laya resuelve en un forward pass. Y su confianza es **calibrada**, que es
exactamente lo que necesita el gate `PREGUNTA → SUGIERE → AUTO-ARCHIVA` del agente.

**Lo que NO aporta:** no genera texto. El `resumen` y la redacción de borradores
(`generate_draft`, `generate_forward_note`) **siguen necesitando el LLM**.

**Avisos honestos (del propio README de laya, no míos):**
1. Los checkpoints base están **casi en azar** en su benchmark de decisiones tipadas
   (0.36 y 0.35 frente a 0.318 de azar). El 0.766 que anuncian requiere **fine-tuning**.
   "Es una base rápida para especializar, no un motor de decisión zero-shot".
2. Con texto latino (español/portugués) el router puede mandar al checkpoint inglés. En
   `laya-decisions.py` ya se usa `Router(default="multilingual")` para corregirlo.
3. El primitivo `noul` tiene un bug conocido (#156): puede seguir sus etiquetas en vez del
   texto. Por eso las preguntas booleanas de `laya-decisions.py` se hacen como `choice` A/B.

**El plan que cierra el círculo:** el agente ya escribe `~/.triage/decisions.jsonl` con tus
decisiones reales. **Ese es el dataset de fine-tuning.** Secuencia recomendada:
1. Operar unas semanas con el LLM (Qwen2.5-3B) → acumular decisiones.
2. Fine-tunear laya con ese JSONL (el repo de laya trae notebook para 2xT4 en Kaggle, ~4-5 h).
3. Mover la capa de decisión a laya y dejar el LLM solo para resumir/redactar.

Script: `tools/laya-decisions.py` (self-test con 4 casos es/en/pt, sin tocar tu buzón).

### Medición real del self-test (laya 0.3.10 sobre 1 core)

| Caso | Ruteo | Categoría | Confianza | Latencia |
|---|---|---|---|---|
| Cliente factura duplicada (es) | multilingual | `finance` ✅ | 0.76 | 75.8 s (1ª, incluye carga) |
| Newsletter inglesa (en) | english | `newsletter` ✅ | — | 54.5 s (incluye carga) |
| Notificación automática (pt) | multilingual | `newsletter` | — | **8.0 s** |
| RRHH interno (es) | multilingual | `notification` | 0.85 | **4.8 s** |

**Conclusiones con evidencia:**

1. **El ruteo multilingüe funciona**: mandó español y portugués al checkpoint
   `multilingual` ("Latin script but language looks like 'es', not English"), que era justo
   el riesgo documentado. Configurar `Router(default="multilingual")` era necesario.
2. **Es 5-8x más rápido que el LLM**: 4.8-8 s en caliente frente a los ~40 s de Qwen2.5-3B.
3. **PERO su confianza no es fiable sin fine-tuning.** Aparece este aviso del propio laya:
   `this checkpoint ships invalid temperatures ... Treat confidence from the affected entries
   as uncalibrated`. Y hubo una respuesta contradictoria: `sensible? A (confianza 0.00)`
   — eligió "sí es sensible" con confianza cero.
   **Consecuencia práctica: NO uses la confianza de laya para el auto-archivado hasta
   fine-tunearla.** Úsala para clasificar (donde acierta) y deja el gate de autonomía en
   manos del LLM o de un umbral muy conservador.

**Arquitectura recomendada resultante:** laya para clasificar (rápido y barato), el LLM solo
para `resumen` y redacción de borradores (donde laya no puede ayudar porque no genera texto).

### Head-to-head medido: laya vs LLM sobre los MISMOS 8 casos

`tools/eval-laya.py` repite exactamente los mismos correos y el mismo criterio de acierto que
`eval-models.ps1`, para que la comparación sea manzanas con manzanas.

| Modelo | Aciertos categoría | JSON válido | Latencia media |
|---|---|---|---|
| **laya** (router multilingüe) | **5/8** | 8/8* | **15.6 s** |
| gemma-3-4b-it | **7/8** | 8/8 | 73.0 s |
| Qwen2.5-3B-Instruct | 5/8 | 8/8 | 41.7 s |

\* laya **no puede** fallar el JSON: no genera texto, devuelve decisiones tipadas. Ese 8/8 es
por construcción, no por mérito.

**Lectura de los números:**

- laya **iguala en aciertos a Qwen2.5-3B (5/8) a 1/2.7 del tiempo** (15.6 s vs 41.7 s), y es
  **4.7x más rápida que Gemma**. Pero **no alcanza la precisión de Gemma** (5/8 vs 7/8).
- En caliente el checkpoint multilingüe tarda **~4 s** por correo; el inglés (ModernBERT-large,
  más grande) tarda 13-16 s. El primer caso incluye la carga (~63 s).
- **Sus respuestas de "sensible" no son fiables**: marcó `sensible=A` en un newsletter y en un
  correo personal, y `responde=A` en una alerta automática. Coherente con el aviso de
  temperaturas sin calibrar.

**Conclusión práctica (importante):** en ESTE agente, clasificar y resumir son **la misma
llamada** al LLM (`ollama_classify` devuelve categoría + urgencia + requiere_respuesta +
resumen + reasoning de una vez). Por tanto **cambiar la clasificación a laya no ahorra la
llamada al LLM** salvo que se separe el resumen en otra llamada — lo que probablemente
empeoraría el tiempo total.

Donde laya **sí aporta valor real y medible** es como **pre-filtro**: la mayoría de una bandeja
es newsletters y avisos automáticos. Decidir en ~4 s "esto no merece tratamiento completo" y
reservar los 73 s de Gemma para lo que sí importa es un ahorro enorme y de bajo riesgo.

Y **no uses la sensibilidad de laya como gate de seguridad**: en el agente ese gate ya es
determinista y fiable (viene de `domain_facts`, por dominio del remitente), no del modelo.

**Ruta para que laya pase de complemento a protagonista:** fine-tuning con
`~/.triage/decisions.jsonl` (tus decisiones reales). Sin ese paso, sus 5/8 y sus confianzas
sin calibrar no justifican darle la decisión final.

### Integracion implementada: pre-filtro de laya en el agente

Autorizado por el usuario. Cambios en el repo:

| Fichero | Cambio |
|---|---|
| `triage_laya.py` (nuevo, raiz del repo) | Modulo del pre-filtro. Carga perezosa del Router, thread-safe. |
| `triage_agent.py` (parcheado) | Nueva funcion `classify()` + los 2 puntos de llamada redirigidos. Backup en `triage_agent.py.bak`. |
| `config.json` | Claves `use_laya_prefilter`, `laya_skip_categories`, `laya_min_confidence`. |
| `tools/test-prefilter.py` (nuevo) | Validacion del pre-filtro sobre los 8 casos, SIN necesidad de Outlook. |
| `tools/patch-triage-agent.ps1` | El parche, idempotente y con backup. |

**Como funciona:** `classify(email, facts)` pregunta primero a laya. Solo evita la llamada al
LLM si se cumplen las CUATRO condiciones: (1) el remitente **no** es sensible segun las reglas
deterministas del agente, (2) laya clasifica en `newsletter`/`notification`, (3) confianza
>= 0.75, y (4) laya no cree que requiera respuesta. Si algo falla, cae al LLM sin romper nada.
Con `use_laya_prefilter: false` el comportamiento es exactamente el original.

**Resultado medido (`tools/test-prefilter.py`, 8 casos):**

| Caso | ¿Sensible? | ¿Pre-filtrado? | Categoria | Conf. |
|---|---|---|---|---|
| factura-cliente-es | **Si** | no (protegido) | — | — |
| newsletter-en | No | **SI (evita el LLM)** | `newsletter` ✅ | 0.95 |
| aviso-password-pt | No | no (conf 0.38 < 0.75) | — | — |
| rrhh-vacaciones-es | **Si** | no (protegido) | — | — |
| alerta-monitoreo-en | No | no (conf 0.20 < 0.75) | — | — |

- 1/8 evita el LLM, y **con la categoria correcta**.
- **Ningun remitente sensible se pre-filtra** — la seguridad sigue siendo determinista.
- Los casos donde laya fallaba (`aviso-password-pt`) tienen confianza baja y **caen al LLM**:
  el umbral los filtra solo.

**Honestidad sobre el ahorro:** en esa corrida el ahorro agregado fue de solo 14.6 s porque
la **primera** llamada incluye la carga del checkpoint (58 s). En caliente laya tarda
**1.5-13 s** frente a los **73 s** de Gemma, o sea **6-36x mas rapido** por correo saltado.
El ahorro real dependera de tu bandeja: si predominan newsletters y avisos, la mayoria
evitara el LLM. Si quieres mas cobertura, baja `laya_min_confidence` — pero a 0.5 o menos
`aviso-password-pt` (que laya clasifica MAL como `newsletter`) empezaria a colarse.

---

## 7. DESBLOQUEADO Y FUNCIONANDO SOBRE CORREO REAL

El bloqueo de Outlook **esta resuelto**, y la causa NO era el entorno corporativo (CyberArk,
Zscaler, WDAC...): era el **tipo** de Outlook.

```
olk.exe corriendo en la sesion 1   = Outlook NUEVO, que NO expone COM/MAPI
perfil de Outlook CLASICO          = no existia
```

**Solucion aplicada:** se abrio el **Outlook clasico** (`Office16\OUTLOOK.EXE`). Migro la
cuenta el solo (`\NewOutlookMigration`) y creo el perfil real:
`mitchell.mayet@dxc.com.ost` de 32 MB. **El tenant SI permite el acceso programatico**, que era
la prueba que podia tumbar el proyecto entero.

### Primera clasificacion de correo REAL (dry-run, solo lectura)

```
Destinos (subcarpetas de Inbox): 80 carpetas
Procesando 4 correos del Inbox...

[1] AzureResourceInventory_Report... -> notification | urg=low
[2] RE: Volumetria Cloud - Pedido    -> client_ops   | urg=low    | resp=True
[3] RFP FEMSA (KOF) - Azure (noSAP)  -> client_ops   | urg=medium | resp=True
[4] Re: Parches mensuales 2TS        -> client_ops   | urg=medium | resp=True
Todas: DECISION [ASK] (sin patron conocido)
```

4/4 clasificados sin error. `[ASK]` es lo correcto: aun no hay patrones aprendidos.

### Parches aplicados al agente (idempotentes y con backup)

| Parche | Motivo | Resultado |
|---|---|---|
| `patch-triage-agent.ps1` | Integrar el pre-filtro de laya | `classify()` + 2 puntos de llamada redirigidos, `py_compile` OK |
| `patch-timeouts.ps1` | **Causa de que fallara todo**: `timeout=120` fijo, pero Gemma tarda 73 s de media y hasta 143 s en 1 core -> `ReadTimeout` en TODOS los correos | `llm_timeout=600`, `llm_timeout_draft=900`, `embed_timeout=180` |
| shim actualizado | Su timeout interno (300 s) era MENOR que el del agente (600 s): se rendia antes y devolvia **502** | `CHAT_TIMEOUT=1800`, `EMBED_TIMEOUT=600` |

### Rendimiento real (medido)

- **3-5 minutos por correo** con Gemma-3-4B en 1 core fisico.
- Con `--bridge --batch 5`, cada ciclo puede tardar 15-25 min. Es el limite de 2 vCPU.

### Clave tecnica: como se ejecuta el agente en la sesion del usuario

El COM de Outlook **solo funciona en la sesion del usuario** que tiene el perfil; el agente
corre por Run Command como SYSTEM, donde `win32com` no alcanza Outlook. Solucion:
`run-in-user-session.ps1` registra una tarea con `LogonType=InteractiveToken` (sin contrasena)
a nombre del SID de `mmayet`, la lanza y recoge la salida. Es el mecanismo para `--dry-run`,
`--bootstrap` y `--bridge`.

Ojo: Python **bufferiza stdout** al redirigir a fichero, asi que el log aparece de golpe al
terminar, no en tiempo real.

## 7b. PENDIENTE (actualizado)

1. **Sembrar el aprendizaje**: `--bootstrap --per-folder N` lee el historial ya archivado y crea
   patrones + embeddings. Con 80 subcarpetas, `--per-folder 3` son ~240 correos (~15-25 min);
   `30` son ~2400 (~2 h). Sin esto todo sale `[ASK]`.
2. **Puente a Teams**: OneDrive y Power Automate ya estan configurados por el usuario. Falta
   seguir `docs/POWER-AUTOMATE.md` y lanzar `triage_agent.py --bridge --batch 5`.
3. **Que el pre-filtro de laya funcione de verdad**: `triage_laya.py` esta integrado pero `laya`
   NO esta en el venv del agente (`triage-venv`), asi que la importacion falla y el agente cae
   en silencio al LLM. Hay que instalarlo ahi.
4. **Arranque automatico del bridge** al iniciar sesion (`triage-autostart.ps1` del repo). En
   esta VM el Programador de Tareas SI funciona, a diferencia de lo que asume el SETUP.
5. **Rellenar `client_domains`** en `config.json`. Las carpetas reales dan la pista de los
   clientes: VALE, Ternium, JCI, Bimbo, Aeromexico, CODELCO, Transbank, Redbanc, Adient,
   Profuturo, Aguas Andinas, Praa digital, APM.
6. **Riesgo futuro a vigilar**: el **Outlook nuevo** puede volver a imponerse y romper el COM.
   En una maquina corporativa eso no lo controlas tu. Si ocurre, la maquina limpia que
   proponias pasa de idea a necesidad.

## 8. Notas de seguridad (leer)

- No se desactivó **ninguna** protección: ni Defender, ni WDAC, ni CrowdStrike, ni Zscaler.
- El intento de exclusión de Defender lo autorizaste, pero el propio Windows lo bloqueó
  (`Tamper Protection`). Queda documentado, no forzado.
- Si algún día quieres Ollama de verdad (más fiel al SETUP.md original), el camino es pedir a
  TI que lo permitan en el EDR/WDAC, no saltárselo.
- El tráfico de la VM sigue saliendo por Zscaler. Los puertos 8080/8081/11434 son **solo
  localhost**, no se exponen a la red.

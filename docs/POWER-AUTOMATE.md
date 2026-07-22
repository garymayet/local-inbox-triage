# El puente con Teams vía Power Automate

Esta es la parte más "manual" del proyecto, pero es la que hace posible todo sin registrar
apps ni abrir puertos. La idea:

1. El agente escribe un archivo JSON en `OneDrive/TriageBridge/requests/` (una tarjeta lista).
2. Un flujo de Power Automate detecta el archivo nuevo, publica la tarjeta en Teams y **espera tu respuesta**.
3. Cuando tocas un botón, el flujo escribe tu respuesta en `OneDrive/TriageBridge/decisions/`.
4. El agente lee esa decisión y la aplica en Outlook.

Todo con **conectores estándar** (nada premium): OneDrive for Business + Teams.

---

## Antes de empezar

- Deja que el agente cree las carpetas: corre `py triage_agent.py --bridge` una vez; creará
  `TriageBridge/requests` y `TriageBridge/decisions` dentro de tu OneDrive sincronizado.
- Espera a que **se sincronicen a la nube** (ícono verde). El flujo trabaja contra la copia
  en la nube, no la local.

---

## El flujo, paso a paso

Crea un flujo **automatizado** ("Automated cloud flow"):

### 1. Disparador: *OneDrive for Business — Cuando se crea un archivo*
- Carpeta: `TriageBridge/requests`.
- Si te da "Invalid fileId": borra y vuelve a agregar el trigger, y elige la carpeta con el
  **navegador de carpetas** (no la escribas a mano) una vez que ya se sincronizó a la nube.

### 2. *OneDrive — Obtener contenido de archivo*
- File: usa el token dinámico **Identifier** del disparador (no texto literal).

### 3. *Analizar JSON* (Parse JSON)
- Content: como el paso anterior devuelve binario (`application/octet-stream`), usa esta
  **expresión**:
  ```
  base64ToString(body('Obtener_contenido_de_archivo')?['$content'])
  ```
  (ajusta el nombre del paso al tuyo).
- Esquema: pega un JSON de ejemplo de `TriageBridge/requests/*.json` y usa "Generar a partir
  de una muestra". Lo esencial es que tenga `cid`, `subject`, `from` y `card`.

### 4. *Microsoft Teams — Publicar tarjeta adaptable y esperar una respuesta*
- ("Post adaptive card and wait for a response" — es **estándar**, funciona en móvil.)
- Publicar como: **Flow bot** → **Usuario** (tú).
- Message / Adaptive Card: usa la **expresión** (la tarjeta es un objeto, no un token simple):
  ```
  body('Analizar_JSON')?['card']
  ```
- **Recomendado:** ponle un **timeout** al paso (p. ej. `P1D`) para que las tarjetas sin
  responder expiren en vez de quedarse colgadas para siempre.

### 5. *OneDrive — Crear archivo*
- Carpeta: `TriageBridge/decisions`.
- Nombre de archivo: el `cid` del correo →
  ```
  body('Analizar_JSON')?['cid']
  ```
  (no importa si queda sin extensión `.json`; el agente procesa el archivo igual.)
- Contenido: el cuerpo de la respuesta de Teams:
  ```
  body('Publicar_tarjeta_adaptable_y_esperar_una_respuesta')
  ```
  Teams anida los datos del botón bajo `data` (con `cid`, `action`, `choice`, etc.);
  el agente ya lo entiende.

Guarda y activa el flujo.

---

## Tropiezos que ya están resueltos (guárdalos)

- **Trigger "Invalid fileId"** → borra y re-agrega el trigger; elige la carpeta con el navegador tras sincronizar.
- **"Get file content" da binario** → por eso el Parse JSON usa `base64ToString(...?['$content'])`.
- **La tarjeta no aparece como token** → es un objeto: usa la expresión `body('Analizar_JSON')?['card']`.
- **La respuesta de Teams viene anidada** bajo `d['data']` (choice/cid/action) → el agente ya lo maneja.
- **Tarjeta "colgada" / job que no termina** → casi siempre es el flujo leyendo un archivo a
  medio escribir/sincronizar. El agente ya escribe de forma **atómica** (`.tmp` + rename) para
  evitarlo; además pon un **timeout** en el paso de esperar respuesta.
- **Las tarjetas de bot en Teams no se pueden borrar** (limitación de Teams). Si cancelas jobs
  desde el historial del flujo, las tarjetas viejas quedan visibles pero son inofensivas:
  tocarlas es un no-op (el agente ya no tiene ese `cid` pendiente).

---

## ¿Cómo se ve el JSON de una solicitud?

Un archivo típico en `requests/` se ve así (recortado):

```json
{
  "cid": "AAAA1111...44chars",
  "subject": "Renovación de certificado - acción requerida",
  "from": "no-reply@proveedor.com",
  "card": {
    "type": "AdaptiveCard",
    "version": "1.4",
    "body": [ "... bloques de texto: remitente, asunto, resumen IA, extracto ..." ],
    "actions": [ "... botones: Mover / Redactar / Reenviar / Eliminar / Saltar ..." ]
  }
}
```

El `cid` es el EntryID de Outlook **sanitizado** (solo alfanumérico): es opaco para el flujo
y no lleva datos del correo. Sirve para que el agente sepa a qué correo aplicar tu decisión.

---

## Si tienes más permisos que yo

Este puente de OneDrive existe porque no podía usar nada más. Si tú puedes:

- **Graph API + registro de app** → podrías leer/mover correo directo desde el agente y
  notificar por un canal más directo.
- **Un endpoint público / bot de Teams real** → reemplazas OneDrive por webhooks bidireccionales.

En cualquier caso, el "cerebro" (clasificación, `decide()`, `learn()`) es independiente del
canal: solo cambia la capa de mensajería.

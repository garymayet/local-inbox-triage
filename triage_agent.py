# triage_agent.py  —  Orquestador de triage de inbox (corre en Windows).
#
# Arquitectura hibrida:
#   - COM DIRECTO a Outlook Desktop (leer / mover / borrador / reenviar).
#   - Ollama local (via localhost:11434) para clasificar + embeddings.
#   - Aprende patrones (remitente / dominio / semantica) y gana autonomia CONSERVADORA.
#   - Te pregunta por Microsoft Teams (tarjeta interactiva) via un puente OneDrive + Power Automate.
#
# Toda la configuracion (dominios, carpetas bloqueadas, modelos, umbrales) vive en config.json.
# Copia config.example.json a config.json y editalo antes de correr. Ver README.md.
#
# Modo de PRUEBA (solo lectura, NO cambia nada):
#     python triage_agent.py --dry-run --limit 10
#
# GUARDARRAILES: no existe accion de ENVIAR (todo queda en Borradores); mover solo a
# subcarpetas del Inbox; remitentes sensibles SIEMPRE preguntan (nunca auto-mover).
import os
import sys
import json
import argparse
import datetime
import html
import concurrent.futures
import time
import pythoncom
import win32com.client
import requests

# ---------------- CONFIG (desde config.json) ----------------
def _load_config():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "config.json")
    if not os.path.exists(path):
        sys.exit("Falta config.json.  Copia config.example.json a config.json y editalo\n"
                 "con TUS dominios, remitentes sensibles y carpetas.  Ver README.md.")
    with open(path, encoding="utf-8-sig") as f:   # utf-8-sig tolera el BOM que mete Windows
        return json.load(f)


CFG = _load_config()

OLLAMA = CFG.get("ollama_url", "http://localhost:11434")
BRAIN_MODEL = CFG.get("brain_model", "llama3.2:3b")   # clasifica + redacta
EMBED_MODEL = CFG.get("embed_model", "bge-m3")         # embeddings semanticos

# Dominios de tus CLIENTES: correo de estos dominios se trata como cliente (y sensible).
CLIENT_DOMAINS = [d.lower() for d in CFG.get("client_domains", [])]
# Dominio(s) de TU empresa: correo interno.
INTERNAL_DOMAINS = [d.lower() for d in CFG.get("internal_domains", [])]
# Remitente SENSIBLE = cualquier correo de un cliente + cualquier subcadena de esta lista
# (p.ej. "nombre.jefe", "rrhh", "legal"). Nunca se auto-mueve, siempre te pregunta.
SENSITIVE_SUBSTR = ([f"@{d}" for d in CLIENT_DOMAINS]
                    + [s.lower() for s in CFG.get("sensitive_substrings", [])])
# Carpetas a las que NUNCA se archiva (papelera, spam, enviados, etc.).
BLOCKED_FOLDERS = set(s.lower() for s in CFG.get("blocked_folders", [
    "deleted items", "trash", "junk email", "spambox", "unwanted",
    "outbox", "sent items", "drafts", "sync issues"]))

# Autonomia conservadora.
AUTO_MIN_CONFIRMATIONS = CFG.get("auto_min_confirmations", 3)
# Semantica: votacion k-NN (robusta contra falsos positivos de correos genericos).
SEM_K = CFG.get("sem_k", 5)              # vecinos a considerar
SEM_MIN_TOP = CFG.get("sem_min_top", 0.72)   # el vecino #1 debe superar esto
SEM_MIN_VOTES = CFG.get("sem_min_votes", 3)  # cuantos de los K deben coincidir en la carpeta
ACT_SIM_TOP = CFG.get("act_sim_top", 0.72)   # umbral para marcar "requiere accion" aprendido
BODY_MAX = CFG.get("body_max", 4000)         # cuanto cuerpo leemos como maximo
BRIDGE_FOLDER = CFG.get("bridge_folder", "TriageBridge")  # carpeta dentro de OneDrive

STATE_DIR = os.path.join(os.path.expanduser("~"), ".triage")
os.makedirs(STATE_DIR, exist_ok=True)
DECISIONS = os.path.join(STATE_DIR, "decisions.jsonl")
PATTERNS = os.path.join(STATE_DIR, "patterns.json")
VECS = os.path.join(STATE_DIR, "vectors.jsonl")            # historial semantico
AUTOPILOT_FILE = os.path.join(STATE_DIR, "autopilot.on")   # existe => autopilot ON
USAGE_FILE = os.path.join(STATE_DIR, "folder_usage.json")  # contador de carpetas mas usadas
ACTION_SENDERS = os.path.join(STATE_DIR, "action_senders.json")  # remitentes que requieren accion (aprendido)
ACTION_VECS = os.path.join(STATE_DIR, "action_vectors.jsonl")    # embeddings de correos "requiere accion"

OL_INBOX, OL_MAIL = 6, 43
PR_SMTP = "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"

# ---------------- COM en un solo hilo ----------------
# Outlook via COM es de apartamento: TODO el acceso COM tiene que pasar por UN mismo hilo
# dedicado (con CoInitialize). Por eso usamos un pool de 1 worker y la funcion com().
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_ns = _inbox = None


def _init():
    global _ns, _inbox
    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("Outlook.Application")
    _ns = app.GetNamespace("MAPI")
    _inbox = _ns.GetDefaultFolder(OL_INBOX)


_pool.submit(_init).result()


def com(fn, *a):
    return _pool.submit(fn, *a).result()


def _sender_smtp(item):
    # Los remitentes internos de Exchange devuelven un DN raro ("/O=.../CN=..."), no un
    # correo SMTP. Hay que resolverlo o las listas de dominios nunca hacen match.
    try:
        if item.SenderEmailType == "EX":
            try:
                ex = item.Sender.GetExchangeUser()
                if ex and ex.PrimarySmtpAddress:
                    return ex.PrimarySmtpAddress.lower()
            except Exception:
                pass
            try:
                return (item.PropertyAccessor.GetProperty(PR_SMTP) or "").lower()
            except Exception:
                return (item.SenderEmailAddress or "").lower()
        return (item.SenderEmailAddress or "").lower()
    except Exception:
        return ""


def _walk(folder, prefix, out, depth=0):
    try:
        subs = folder.Folders
    except Exception:
        return
    for f in subs:
        try:
            path = f"{prefix}/{f.Name}" if prefix else f.Name
            out.append((f.Name, path))
            if depth < 6:
                _walk(f, path, out, depth + 1)
        except Exception:
            continue


def _all_folders():
    """Recorre TODO el arbol de carpetas del buzon (incluye subcarpetas de Inbox)."""
    out = []
    _walk(_inbox.Parent, "", out)
    return out


def _list_inbox(limit, only_unread=False):
    """Correos DIRECTAMENTE en el Inbox (no subcarpetas), leidos y no leidos."""
    items = _inbox.Items
    if only_unread:
        items = items.Restrict("[Unread]=true")   # filtrar en el servidor, no en Python
    items.Sort("[ReceivedTime]", True)
    out, i = [], 0
    for it in items:
        if i >= limit:
            break
        try:
            if it.Class != OL_MAIL:   # ignorar citas, reportes de entrega, etc.
                continue
        except Exception:
            continue
        i += 1
        out.append({
            "entry_id": it.EntryID, "from": _sender_smtp(it),
            "from_display": it.SenderName or "", "subject": it.Subject or "",
            "received": str(it.ReceivedTime), "body": (it.Body or "")[:BODY_MAX],
            "unread": bool(it.UnRead),
        })
    return out


def _dest_folders():
    """Solo subcarpetas DENTRO de Inbox (destinos validos de movimiento)."""
    out = []
    _walk(_inbox, "Inbox", out)
    return out


def _find_by_path(path):
    """Resuelve una carpeta por ruta completa tipo 'Inbox/Clientes/ClienteA'.
    Como arranca SIEMPRE en Inbox, es imposible mover un correo fuera del Inbox.

    PATCH-FOLDER-PATH: hay carpetas cuyo NOMBRE contiene '/', p.ej.
    'Inbox/CloudOps/VALE/KT AWS/Azure'. Partir la ruta por '/' y buscar segmento a
    segmento NUNCA las encuentra: devolvia None en silencio, el bootstrap las daba por
    vacias y mover ahi fallaba. En cada nivel se empareja el nombre MAS LARGO que encaje
    como prefijo del resto de la ruta."""
    parts = [p for p in path.split("/") if p]
    rest = "/".join(parts[1:]) if parts and parts[0].lower() == "inbox" else "/".join(parts)
    cur = _inbox
    for _ in range(12):
        if not rest:
            return cur
        mejor = None
        for f in cur.Folders:
            n = f.Name
            if rest.lower() == n.lower() or rest.lower().startswith(n.lower() + "/"):
                if mejor is None or len(n) > len(mejor.Name):
                    mejor = f
        if mejor is None:
            return None
        if rest.lower() == mejor.Name.lower():
            return mejor
        rest = rest[len(mejor.Name) + 1:]
        cur = mejor
    return None

def _find_folder(name_lower):
    """Busca por nombre en TODO el arbol (primer match)."""
    stack = [_inbox.Parent]
    while stack:
        cur = stack.pop()
        try:
            for f in cur.Folders:
                if f.Name.lower() == name_lower:
                    return f
                stack.append(f)
        except Exception:
            continue
    return None


def _move(entry_id, folder_path):
    # _find_by_path arranca en Inbox => solo puede mover a subcarpetas de Inbox
    target = _find_by_path(folder_path)
    if not target:
        return {"error": f"carpeta '{folder_path}' no existe bajo Inbox"}
    if target.Name.lower() in BLOCKED_FOLDERS:
        return {"error": f"carpeta '{folder_path}' esta bloqueada"}
    _ns.GetItemFromID(entry_id).Move(target)
    return {"ok": True}


def _move_ret(entry_id, folder_path):
    # como _move pero devuelve el nuevo EntryID (para poder deshacer el auto-move)
    target = _find_by_path(folder_path)
    if not target or target.Name.lower() in BLOCKED_FOLDERS:
        return None
    moved = _ns.GetItemFromID(entry_id).Move(target)
    return moved.EntryID if moved else None


def _move_to_inbox(entry_id):
    _ns.GetItemFromID(entry_id).Move(_inbox)
    return {"ok": True}


def _draft(entry_id, body):
    item = _ns.GetItemFromID(entry_id)
    r = item.Reply()
    gen = html.escape(body or "").replace("\n", "<br>")
    # HTMLBody preserva el formato del original (los enlaces siguen siendo enlaces;
    # NO se expanden a URLs completas como pasa al escribir en .Body texto plano)
    r.HTMLBody = f"<div style='font-family:Calibri,sans-serif'>{gen}</div><br>" + (r.HTMLBody or "")
    r.Save()                              # Borradores. NUNCA .Send()
    return {"ok": True, "draft_id": r.EntryID}


def _delete(entry_id):
    # Delete() manda a "Elementos eliminados" (recuperable), NO borra permanente
    _ns.GetItemFromID(entry_id).Delete()
    return {"ok": True}


def _forward(entry_id, to, note):
    item = _ns.GetItemFromID(entry_id)
    f = item.Forward()
    if to:
        f.To = to
    gen = html.escape(note or "").replace("\n", "<br>")
    f.HTMLBody = f"<div style='font-family:Calibri,sans-serif'>{gen}</div><br>" + (f.HTMLBody or "")
    f.Save()                              # Borradores. NUNCA .Send()
    return {"ok": True, "draft_id": f.EntryID}


def _flag(entry_id, category):
    # Asigna una categoria de Outlook (crea la categoria si no existe) para ubicar el correo
    it = _ns.GetItemFromID(entry_id)
    cur = it.Categories or ""
    if category.lower() not in cur.lower():
        it.Categories = (cur + "," + category) if cur else category
        it.Save()
    return {"ok": True}


def _folder_recent(path, n):
    """Lee los n correos mas recientes de una subcarpeta (para el bootstrap)."""
    fld = _find_by_path(path)
    if not fld:
        return []
    try:
        items = fld.Items
        items.Sort("[ReceivedTime]", True)
    except Exception:
        return []
    out, i = [], 0
    for it in items:
        if i >= n:
            break
        try:
            if it.Class != OL_MAIL:
                continue
        except Exception:
            continue
        i += 1
        out.append({"from": _sender_smtp(it), "subject": it.Subject or "",
                    "body": (it.Body or "")[:800]})
    return out


# ---------------- Ollama (LLM local) ----------------
SYSTEM = """Eres un clasificador de correo corporativo. Respondes SOLO con JSON valido:
{"category":"newsletter|notification|client_ops|internal_team|hr_admin|finance|vendor|personal|action_required|ambiguous",
 "language":"es|en|pt","urgency":"low|medium|high","requires_reply":true|false,
 "resumen":"Resumen SIEMPRE EN ESPANOL (aunque el correo este en ingles o portugues), en un parrafo de 3 a 5 frases (~100-150 palabras): de que trata, puntos clave y que accion se pide. SOLO lo que aparece en el correo, sin inventar ni suponer",
 "reasoning":"1-2 frases"}
Se honesto; si no reconoces el patron usa "ambiguous". El 'resumen' SIEMPRE en espanol y nunca inventes datos, fechas ni montos."""


def ollama_classify(email, facts):
    # Le pasamos al LLM los HECHOS deterministas (cliente/interno/sensible) como pistas,
    # pero esos hechos MANDAN sobre el juicio del modelo (ver domain_facts + decide()).
    hint = []
    if facts["is_client"]:
        hint.append("HECHO: remitente es CLIENTE.")
    if facts["is_internal"]:
        hint.append("HECHO: remitente INTERNO (tu empresa).")
    if facts["is_sensitive"]:
        hint.append("HECHO: remitente SENSIBLE (revision humana).")
    body = (" ".join(hint) + "\n" if hint else "")
    body += f"from: {email['from']}\nsubject: {email['subject']}\nbody: {email['body'][:1500]}"
    r = requests.post(f"{OLLAMA}/v1/chat/completions", timeout=CFG.get("llm_timeout", 600), json={
        "model": BRAIN_MODEL, "temperature": 0.1, "stream": False,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": body}]})
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def ollama_embed(text):
    r = requests.post(f"{OLLAMA}/api/embed", timeout=CFG.get("embed_timeout", 180),
                      json={"model": EMBED_MODEL, "input": text[:2000]})
    r.raise_for_status()
    return r.json()["embeddings"][0]


# ---------------- Reglas + aprendizaje ----------------
VALID_CATS = {"newsletter", "notification", "client_ops", "internal_team", "hr_admin",
              "finance", "vendor", "personal", "action_required", "ambiguous"}


def classify(email, facts):
    """Clasifica un correo: pre-filtro local (laya) si esta habilitado en config.json;
    si no, el LLM. Si el pre-filtro falla por cualquier motivo, cae al LLM sin romper nada.
    Ver docs/VM-SETUP-DSH.md (seccion del pre-filtro)."""
    if CFG.get("use_laya_prefilter", False):
        try:
            import triage_laya
            pre = triage_laya.prefilter(email, facts, CFG)
            if pre:
                return pre
        except Exception:
            pass
    return ollama_classify(email, facts)


def clean_cat(c):
    # El modelo 3B a veces devuelve "newsletter|notification": nos quedamos con la primera valida.
    c = (c or "").strip().lower()
    if c in VALID_CATS:
        return c
    for sep in ("|", "/", ",", " "):
        first = c.split(sep)[0].strip()
        if first in VALID_CATS:
            return first
    return "ambiguous"


def domain_facts(smtp):
    smtp = (smtp or "").lower()
    dom = smtp.split("@")[-1] if "@" in smtp else ""
    return {"is_client": any(dom.endswith(d) for d in CLIENT_DOMAINS),
            "is_internal": any(dom.endswith(d) for d in INTERNAL_DOMAINS),
            "is_sensitive": any(s in smtp for s in SENSITIVE_SUBSTR),
            "domain": dom}


def load_patterns():
    try:
        with open(PATTERNS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_usage():
    try:
        return json.load(open(USAGE_FILE))
    except Exception:
        return {}


def bump_usage(folder):
    u = _load_usage()
    u[folder] = u.get(folder, 0) + 1
    json.dump(u, open(USAGE_FILE, "w"))


def order_folders(folders):
    """Carpetas mas usadas primero (por conteo), luego el resto en orden de arbol."""
    u = _load_usage()
    used = sorted([f for f in folders if u.get(f, 0) > 0], key=lambda f: -u[f])
    rest = [f for f in folders if u.get(f, 0) == 0]
    return used + rest


def _load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8-sig"))
    except Exception:
        return default


def learn_action(smtp, vec):
    """Aprende que un remitente (y semanticamente correos parecidos) requiere accion tuya."""
    a = _load_json(ACTION_SENDERS, {})
    if smtp:
        a[f"from:{smtp}"] = a.get(f"from:{smtp}", 0) + 1
        json.dump(a, open(ACTION_SENDERS, "w"))
    if vec is not None:
        with open(ACTION_VECS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"vec": vec}) + "\n")


def is_learned_action(smtp, vec):
    a = _load_json(ACTION_SENDERS, {})
    if smtp and a.get(f"from:{smtp}", 0) > 0:
        return True
    if vec is None:
        return False
    best = 0.0
    try:
        with open(ACTION_VECS, encoding="utf-8") as f:
            for line in f:
                sc = _cos(vec, json.loads(line)["vec"])
                if sc > best:
                    best = sc
    except FileNotFoundError:
        return False
    return best >= ACT_SIM_TOP


# patterns.json = { "domain:clientea.com": {"Inbox/Clientes/ClienteA": {"observed":28,"confirmed":0,"rejected":0}, ...}, ... }
def save_patterns(pats):
    with open(PATTERNS, "w", encoding="utf-8") as f:
        json.dump(pats, f, ensure_ascii=False, indent=1)


def pat_bump(pats, key, folder, field):
    e = pats.setdefault(key, {}).setdefault(folder, {"observed": 0, "confirmed": 0, "rejected": 0})
    e[field] += 1


def _score(counts):
    # confirmar suma doble; rechazar resta doble => una correccion tuya pesa mucho.
    return counts.get("observed", 0) + 2 * counts.get("confirmed", 0) - 2 * counts.get("rejected", 0)


def pattern_lookup(pats, email, facts):
    """Devuelve (folder, confirmaciones, total, limpio) del patron mas fuerte, con regla de mayoria.
    'from:' (remitente exacto) manda sobre 'domain:' (mas generico)."""
    for key in (f"from:{email['from']}", f"domain:{facts['domain']}"):
        entry = pats.get(key)
        if not entry:
            continue
        totals = {fld: _score(c) for fld, c in entry.items()}
        grand = sum(v for v in totals.values() if v > 0)
        if grand <= 0:
            continue
        best_fld = max(totals, key=totals.get)
        if totals[best_fld] <= 0:
            continue
        share = totals[best_fld] / grand
        if share >= 0.60 and (totals[best_fld] >= 2):     # mayoria clara
            c = entry[best_fld]
            # "limpio" = casi sin historial contradictorio; solo estos pueden auto-mover
            clean = share >= 0.90 and c.get("rejected", 0) <= 1
            return best_fld, c.get("confirmed", 0), grand, clean
    return None, 0, 0, False


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return s / (na * nb) if na and nb else 0.0


def semantic_lookup(vec):
    """Votacion k-NN: mira los SEM_K vecinos mas cercanos y sugiere una carpeta
    solo si domina (>= SEM_MIN_VOTES) y el vecino #1 supera SEM_MIN_TOP.
    (El voto por mayoria evita que un correo generico caiga en la carpeta de un vecino aislado.)"""
    scored = []
    try:
        with open(VECS, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                scored.append((_cos(vec, rec["vec"]), rec["folder"]))
    except FileNotFoundError:
        return None, 0.0
    if not scored:
        return None, 0.0
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:SEM_K]
    topscore = top[0][0]
    if topscore < SEM_MIN_TOP:
        return None, topscore
    votes = {}
    for sc, fld in top:
        votes[fld] = votes.get(fld, 0) + 1
    best_fld = max(votes, key=votes.get)
    if votes[best_fld] >= SEM_MIN_VOTES:
        return best_fld, topscore
    return None, topscore


def autopilot_on():
    return os.path.exists(AUTOPILOT_FILE)


def decide(email, cls, facts, pats, vec):
    """Determina el estado: ASK / SUGGEST / AUTO y la carpeta propuesta.
    3 capas: patron remitente -> patron dominio -> semantica; + reglas duras de seguridad."""
    needs_action = email.get("needs_action")
    if needs_action is None:
        needs_action = bool(cls.get("requires_reply")) or clean_cat(cls.get("category")) == "action_required"
    folder, confirmed, total, clean = pattern_lookup(pats, email, facts)
    src = f"patron remitente/dominio ({total} obs)"
    sem_folder, sem_score = semantic_lookup(vec) if vec is not None else (None, 0.0)
    if folder and not clean and sem_folder and sem_folder != folder:
        # Remitente MIXTO (historial con rechazos/carpetas variadas, ej. notificaciones
        # tipo no-reply cuya carpeta depende del CONTENIDO): la semantica manda.
        folder, confirmed = sem_folder, 0
        src = f"semantica (sim={sem_score:.2f}; remitente mixto)"
    elif not folder and sem_folder:
        folder, confirmed = sem_folder, 0
        src = f"semantica (sim={sem_score:.2f})"
    elif not folder:
        src = None

    if not folder:
        why = "sin patron conocido" + (" (sensible)" if facts["is_sensitive"] else "")
        return {"state": "ASK", "folder": None, "why": why}
    # hay carpeta sugerida
    if facts["is_sensitive"] or needs_action:
        tag = "SENSIBLE" if facts["is_sensitive"] else "REQUIERE ACCION"
        return {"state": "SUGGEST", "folder": folder, "why": f"{src} — {tag} (nunca auto)"}
    if clean and confirmed >= AUTO_MIN_CONFIRMATIONS and autopilot_on():
        return {"state": "AUTO", "folder": folder, "why": f"{src}, {confirmed} conf + autopilot"}
    return {"state": "SUGGEST", "folder": folder, "why": src}


# ---------------- Modos ----------------
def run_dry(limit):
    print(f"== DRY-RUN (solo lectura) == modelo={BRAIN_MODEL}  autopilot={'ON' if autopilot_on() else 'OFF'}")
    dests = com(_dest_folders)
    print(f"Destinos (subcarpetas de Inbox): {len(dests)} carpetas\n")
    emails = com(_list_inbox, limit)
    nun = sum(1 for e in emails if e.get("unread"))
    print(f"Procesando {len(emails)} correos del Inbox ({nun} no leidos)...\n" + "=" * 74)
    pats = load_patterns()
    for i, e in enumerate(emails, 1):
        try:
            facts = domain_facts(e["from"])
            cls = classify(e, facts)
            cat = clean_cat(cls.get("category"))
            vec = ollama_embed(f"{e['subject']} {e['body'][:800]}")
            d = decide(e, cls, facts, pats, vec)
            tag = "•no leido" if e.get("unread") else "leido"
            print(f"\n[{i}] ({tag}) {e['from_display']} <{e['from']}>")
            print(f"    asunto: {e['subject'][:66]}")
            print(f"    -> {cat} | urg={cls.get('urgency')} | "
                  f"resp={cls.get('requires_reply')} | sensible={facts['is_sensitive']}")
            arrow = f"MOVER a '{d['folder']}'" if d["folder"] else "preguntar carpeta / redactar"
            print(f"    DECISION: [{d['state']}] {arrow}  ({d['why']})")
        except Exception as ex:
            print(f"\n[{i}] ERROR: {ex.__class__.__name__}: {ex}")
    print("\n" + "=" * 74 + "\nDRY-RUN terminado. Nada fue movido ni modificado.")


def run_bootstrap(per_folder):
    """Mina las subcarpetas de Inbox: aprende remitente/dominio->carpeta + embeddings.
    Asi el agente SUGIERE bien desde el dia 1 en vez de aprender desde cero.
    Reanudable (guarda progreso por carpeta)."""
    dests = com(_dest_folders)
    pats = load_patterns()
    done_file = os.path.join(STATE_DIR, "bootstrap_done.json")
    try:
        done = set(json.load(open(done_file)))
    except Exception:
        done = set()
    print(f"== BOOTSTRAP == {len(dests)} subcarpetas, {per_folder} correos c/u, embed={EMBED_MODEL}")
    total = 0
    with open(VECS, "a", encoding="utf-8") as vecf:
        for idx, (name, path) in enumerate(dests, 1):
            if path in done:
                print(f"[{idx}/{len(dests)}] {path}  (skip, ya indexada)")
                continue
            emails = com(_folder_recent, path, per_folder)
            nvec = 0
            for e in emails:
                smtp = e["from"]
                dom = smtp.split("@")[-1] if "@" in smtp else ""
                if smtp:
                    pat_bump(pats, f"from:{smtp}", path, "observed")
                if dom:
                    pat_bump(pats, f"domain:{dom}", path, "observed")
                try:
                    vec = ollama_embed(f"{e['subject']} {e['body']}")
                    vecf.write(json.dumps({"folder": path, "vec": vec}) + "\n")
                    nvec += 1
                except Exception:
                    pass
            total += len(emails)
            done.add(path)
            save_patterns(pats)
            json.dump(list(done), open(done_file, "w"))
            vecf.flush()
            print(f"[{idx}/{len(dests)}] {path}: {len(emails)} correos, {nvec} vectores  (acum {total})")
    print(f"\nBOOTSTRAP listo: {total} correos, {len(pats)} claves de patron, embeddings en {VECS}")


# ---------------- Puente OneDrive <-> Power Automate <-> Teams ----------------
# El agente escribe una "solicitud" (tarjeta adaptativa) en OneDrive/TriageBridge/requests/.
# Un flujo de Power Automate la detecta, la publica en Teams y espera tu respuesta, y escribe
# tu decision en OneDrive/TriageBridge/decisions/. El agente la aplica (mover/borrador/etc.).
# Ventaja: cero endpoint publico, cero registro de app, cero conector premium.
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")
PENDING_FILE = os.path.join(STATE_DIR, "pending.json")


def _onedrive_root():
    for k in ("OneDriveCommercial", "OneDrive", "OneDriveConsumer"):
        v = os.environ.get(k)
        if v and os.path.isdir(v):
            return v
    home = os.path.expanduser("~")
    for n in sorted(os.listdir(home)):
        if n.lower().startswith("onedrive") and os.path.isdir(os.path.join(home, n)):
            return os.path.join(home, n)
    return None


def _bridge_dirs():
    root = _onedrive_root()
    if not root:
        raise RuntimeError("No encontre carpeta OneDrive sincronizada")
    req = os.path.join(root, BRIDGE_FOLDER, "requests")
    dec = os.path.join(root, BRIDGE_FOLDER, "decisions")
    os.makedirs(req, exist_ok=True)
    os.makedirs(dec, exist_ok=True)
    return req, dec


def load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except Exception:
        return set()


def save_seen(s):
    json.dump(list(s), open(SEEN_FILE, "w"))


def load_pending():
    try:
        return json.load(open(PENDING_FILE))
    except Exception:
        return {}


def save_pending(p):
    json.dump(p, open(PENDING_FILE, "w"))


def _get_email(entry_id):
    it = _ns.GetItemFromID(entry_id)
    return {"from_display": it.SenderName or "", "subject": it.Subject or "",
            "body": (it.Body or "")[:BODY_MAX]}


def _in_inbox(entry_id):
    """True solo si el correo sigue en el Inbox (no movido/borrado a mano).

    PATCH-IN-INBOX: devuelve None si NO SE PUDO COMPROBAR (excepcion transitoria de COM:
    Outlook ocupado, RPC_E_CALL_REJECTED, marshalling, sincronizacion). Antes cualquier
    excepcion devolvia False y _bridge_tick lo leia como "el usuario ya lo movio", de modo
    que CANCELABA TARJETAS VIVAS en silencio. Distinguir los dos casos es obligatorio."""
    try:
        it = _ns.GetItemFromID(entry_id)
        return it.Parent.EntryID == _inbox.EntryID
    except Exception:
        return None

def generate_draft(email, user_text=""):
    if (user_text or "").strip():
        # Pulir el borrador del usuario: corregir sin cambiar el significado
        prompt = ("Mejora el siguiente borrador de respuesta: corrige ortografia, gramatica y "
                  "redaccion, y dale estructura, orden y claridad. Manten el MISMO idioma y un tono "
                  "profesional. NO cambies el significado ni agregues informacion que el usuario no puso. "
                  "Devuelve SOLO el cuerpo mejorado, sin firma.\n\n"
                  f"(Correo al que responde - Asunto: {email.get('subject','')})\n\n"
                  f"Borrador del usuario:\n{user_text}")
    else:
        prompt = (f"Correo recibido:\nDe: {email.get('from_display','')}\n"
                  f"Asunto: {email.get('subject','')}\nCuerpo:\n{email.get('body','')[:1500]}\n\n"
                  "Redacta SOLO el cuerpo de una respuesta profesional, breve y cordial, "
                  "en el MISMO idioma del correo. Sin firma final.")
    r = requests.post(f"{OLLAMA}/v1/chat/completions", timeout=CFG.get("llm_timeout_draft", 900), json={
        "model": BRAIN_MODEL, "temperature": 0.3, "stream": False,
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def generate_forward_note(email):
    prompt = (f"Correo a reenviar:\nDe: {email.get('from_display','')}\n"
              f"Asunto: {email.get('subject','')}\nCuerpo:\n{email.get('body','')[:1500]}\n\n"
              "Escribe SOLO una nota breve y profesional (en espanol) para REENVIAR este correo "
              "al administrador o cliente responsable, resumiendo que accion se requiere de su parte. "
              "Sin firma final.")
    r = requests.post(f"{OLLAMA}/v1/chat/completions", timeout=CFG.get("llm_timeout_draft", 900), json={
        "model": BRAIN_MODEL, "temperature": 0.3, "stream": False,
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def learn(smtp, dom, vec, folder, suggested):
    pats = load_patterns()
    # No aprender a nivel-dominio para dominios internos: el correo interno se archiva
    # por TEMA, no por remitente -> domain:tuempresa.com seria ruido (folders muy variados).
    # El nivel from: (remitente exacto) y la semantica siguen cubriendo a los internos.
    if dom and any(dom.endswith(d) for d in INTERNAL_DOMAINS):
        dom = None
    for key in ([f"from:{smtp}"] if smtp else []) + ([f"domain:{dom}"] if dom else []):
        pat_bump(pats, key, folder, "confirmed")
        if suggested and suggested != folder:
            pat_bump(pats, key, suggested, "rejected")   # correccion tuya: penaliza lo sugerido
    save_patterns(pats)
    if vec is not None:
        with open(VECS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"folder": folder, "vec": vec}) + "\n")


def _log(rec):
    rec = dict(rec)
    rec["ts"] = datetime.datetime.now().isoformat()
    with open(DECISIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _safe_id(entry_id):
    # cid = EntryID de Outlook sanitizado (opaco para el flujo; no lleva datos del correo)
    return "".join(c for c in entry_id if c.isalnum())[-44:]


def build_card(email, cat, facts, decision, folders, cid):
    sug = decision.get("folder")
    body = [
        {"type": "TextBlock", "text": "Triage de correo", "weight": "bolder", "size": "medium"},
        {"type": "TextBlock", "text": f"De: {email['from_display']} <{email['from']}>",
         "wrap": True, "spacing": "small"},
        {"type": "TextBlock", "text": f"Asunto: {email['subject']}", "wrap": True, "weight": "bolder"},
        {"type": "TextBlock", "isSubtle": True, "spacing": "small", "wrap": True,
         "text": f"Tipo: {cat} - urgencia {email.get('urg','?')}"
                 + (" - SENSIBLE" if facts["is_sensitive"] else "")},
    ]
    if email.get("needs_action"):
        body.append({"type": "TextBlock", "text": "⚠ REQUIERE TU ACCION (posible respuesta)",
                     "color": "attention", "weight": "bolder", "wrap": True})
    if email.get("resumen"):
        body.append({"type": "TextBlock", "color": "accent", "wrap": True,
                     "text": f"🧠 Resumen (IA, verifica con el extracto): {email['resumen']}"})
    prev = " ".join((email.get("body") or "").split())[:600]
    if prev:
        body.append({"type": "TextBlock", "text": prev, "wrap": True, "isSubtle": True,
                     "spacing": "medium", "maxLines": 8})
    if sug:
        body.append({"type": "TextBlock", "text": f"Sugerencia: {sug}", "color": "good", "wrap": True})
    body.append({"type": "Input.ChoiceSet", "id": "choice",
                 "label": "O mover a otra (escribe para buscar; mas usadas primero):",
                 "style": "filtered", "isMultiSelect": False,
                 "value": sug or (folders[0] if folders else ""),
                 "choices": [{"title": f, "value": f} for f in folders]})
    body.append({"type": "Input.Text", "id": "reply_text", "isMultiline": True,
                 "label": "Tu respuesta (opcional; la pulo yo). Vacio = redacto desde cero:",
                 "placeholder": "Escribe a grandes rasgos lo que quieres responder..."})
    body.append({"type": "Input.Text", "id": "fwd_to", "label": "Reenviar a (correo, opcional):",
                 "placeholder": "admin@cliente.com"})
    actions = []
    if sug:
        actions.append({"type": "Action.Submit", "title": f"Mover a {sug.split('/')[-1]}",
                        "data": {"cid": cid, "action": "move", "folder": sug}})
    actions += [
        {"type": "Action.Submit", "title": "Mover a la elegida (lista)", "data": {"cid": cid, "action": "move_choice"}},
        {"type": "Action.Submit", "title": "Redactar respuesta", "data": {"cid": cid, "action": "draft"}},
        {"type": "Action.Submit", "title": "↪ Reenviar (borrador)", "data": {"cid": cid, "action": "forward"}},
        {"type": "Action.Submit", "title": "⚑ Requiere mi accion (ensenar)", "data": {"cid": cid, "action": "mark_action"}},
        {"type": "Action.Submit", "title": "🗑 Eliminar (a papelera)", "data": {"cid": cid, "action": "delete"}},
        {"type": "Action.Submit", "title": "Saltar", "data": {"cid": cid, "action": "skip"}},
    ]
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": body, "actions": actions}


def build_notify_card(email, folder, cid, undo_id):
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": "🤖 Movido automaticamente", "weight": "bolder", "size": "medium"},
                {"type": "TextBlock", "wrap": True, "text": f"De: {email['from_display']} <{email['from']}>"},
                {"type": "TextBlock", "wrap": True, "weight": "bolder", "text": f"Asunto: {email['subject']}"},
                {"type": "TextBlock", "wrap": True, "color": "good", "text": f"-> {folder} (patron aprendido)"},
            ],
            "actions": [{"type": "Action.Submit", "title": "↩ Deshacer (regresar a Inbox)",
                         "data": {"cid": cid, "action": "undo", "undo_id": undo_id}}]}


def _bridge_tick(req_dir, dec_dir, batch):
    seen = load_seen()
    pending = load_pending()
    # 0) limpiar notificaciones de auto-move viejas (>5 min) que nadie deshizo
    _now = datetime.datetime.now().timestamp()
    for _fn in list(os.listdir(req_dir)):
        if _fn.startswith("notify-"):
            _p = os.path.join(req_dir, _fn)
            try:
                if _now - os.path.getmtime(_p) > 300:
                    _rm(_p)
            except Exception:
                pass
    # 1) aplicar decisiones recibidas
    for fn in list(os.listdir(dec_dir)):
        path = os.path.join(dec_dir, fn)
        if not os.path.isfile(path):
            continue
        try:
            d = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            continue
        # Teams anida el submit bajo "data"; tolera ambos formatos
        data = d.get("data") if isinstance(d.get("data"), dict) else d
        cid = data.get("cid") or d.get("cid") or fn.split(".")[0]
        action = data.get("action") or d.get("action")
        if action == "undo":
            uid = data.get("undo_id") or d.get("undo_id")
            if uid:
                try:
                    com(_move_to_inbox, uid)
                    _log({"action": "undo", "cid": cid, "via": "teams"})
                    print("[deshecho] regresado a Inbox")
                except Exception as ex:
                    print(f"[undo error] {ex}")
            _rm(path)
            continue
        meta = pending.get(cid)
        if not meta:
            _rm(path)
            continue
        folder = (data.get("folder") or data.get("choice")
                  or d.get("folder") or d.get("choice") or meta.get("suggested"))
        eid = meta["entry_id"]
        try:
            if action in ("move", "move_choice") and folder:
                r = com(_move, eid, folder)
                if r.get("ok"):
                    learn(meta.get("from"), meta.get("dom"), meta.get("vec"), folder, meta.get("suggested"))
                    bump_usage(folder)
                    _log({"action": "move", "folder": folder, "cid": cid, "via": "teams"})
                    print(f"[aplicado] movido a {folder}")
                else:
                    print(f"[error move] {r}")
            elif action == "draft":
                com(_draft, eid, generate_draft(com(_get_email, eid), data.get("reply_text", "")))
                _log({"action": "draft", "cid": cid, "via": "teams",
                      "modo": "pulir" if (data.get("reply_text") or "").strip() else "desde-cero"})
                print("[aplicado] borrador creado")
            elif action == "forward":
                to = data.get("fwd_to") or ""
                com(_forward, eid, to, generate_forward_note(com(_get_email, eid)))
                _log({"action": "forward", "to": to, "cid": cid, "via": "teams"})
                print(f"[aplicado] borrador de reenvio creado (to: {to or 'sin destinatario'})")
            elif action == "delete":
                com(_delete, eid)
                _log({"action": "delete", "cid": cid, "via": "teams"})
                print("[aplicado] eliminado (a Elementos eliminados)")
            elif action == "mark_action":
                learn_action(meta.get("from"), meta.get("vec"))
                com(_flag, eid, "Requiere accion")
                _log({"action": "mark_action", "from": meta.get("from"), "cid": cid, "via": "teams"})
                print(f"[aprendido] requiere accion: {meta.get('from')}")
            else:
                com(_flag, eid, "Pendiente")
                _log({"action": "skip", "cid": cid, "via": "teams"})
        except Exception as ex:
            print(f"[apply error {cid}] {ex.__class__.__name__}: {ex}")
        seen.add(eid)
        pending.pop(cid, None)
        _rm(os.path.join(req_dir, meta.get("reqfile", cid + ".json")))
        _rm(path)
    save_seen(seen)
    save_pending(pending)
    # 1b) reconciliar: soltar pendientes que el usuario ya movio/borro a mano en Outlook
    changed = False
    for c, m in list(pending.items()):
        # PATCH-IN-INBOX: solo reconciliar con certeza (None = no se pudo comprobar)
        if com(_in_inbox, m["entry_id"]) is False:
            _rm(os.path.join(req_dir, m.get("reqfile", "")))
            seen.add(m["entry_id"])
            pending.pop(c, None)
            changed = True
            print(f"[reconciliar] {c[:16]}... ya no esta en Inbox (accion manual) -> slot liberado")
    if changed:
        save_pending(pending)
        save_seen(seen)
    # 2) generar nuevas solicitudes hasta 'batch' pendientes
    slots = batch - len(pending)
    if slots <= 0:
        return
    pats = load_patterns()
    folders = order_folders([p for _, p in com(_dest_folders)])   # recargar + mas usadas primero
    made = 0
    for e in com(_list_inbox, 60):
        if made >= slots:
            break
        eid = e["entry_id"]
        cid = _safe_id(eid)
        if eid in seen or cid in pending:
            continue
        facts = domain_facts(e["from"])
        try:
            cls = classify(e, facts)
        except Exception:
            cls = {}
        e["urg"] = cls.get("urgency")
        e["req_reply"] = bool(cls.get("requires_reply"))
        e["resumen"] = cls.get("resumen")
        cat = clean_cat(cls.get("category"))
        try:
            vec = ollama_embed(f"{e['subject']} {e['body'][:800]}")
        except Exception:
            vec = None
        e["needs_action"] = (e["req_reply"] or cat == "action_required"
                             or is_learned_action(e["from"], vec))
        dcn = decide(e, cls, facts, pats, vec)
        if dcn["state"] == "AUTO":
            new_id = com(_move_ret, eid, dcn["folder"])
            if new_id:
                bump_usage(dcn["folder"])
                learn(e["from"], facts["domain"], vec, dcn["folder"], dcn["folder"])
                ncard = build_notify_card(e, dcn["folder"], cid, new_id)
                nreq = f"notify-{cid}-{datetime.datetime.now().strftime('%H%M%S%f')}.json"
                _write_request(req_dir, nreq,
                               {"cid": cid, "subject": e["subject"], "from": e["from"], "card": ncard})
                _log({"action": "auto_move", "folder": dcn["folder"], "cid": cid})
                print(f"[auto] movido a {dcn['folder']} (notificado; deshacer disponible)")
                made += 1
            continue
        card = build_card(e, cat, facts, dcn, folders, cid)
        reqname = f"{cid}-{datetime.datetime.now().strftime('%H%M%S%f')}.json"
        _write_request(req_dir, reqname,
                       {"cid": cid, "subject": e["subject"], "from": e["from"], "card": card})
        pending[cid] = {"entry_id": eid, "from": e["from"], "dom": facts["domain"],
                        "vec": vec, "suggested": dcn.get("folder"), "reqfile": reqname}
        made += 1
    save_pending(pending)
    if made:
        print(f"[tick] {made} solicitudes nuevas ({len(pending)} pendientes)")


def _rm(p):
    try:
        os.remove(p)
    except Exception:
        pass


def _write_request(req_dir, name, payload):
    # Escribe FUERA de la carpeta vigilada y luego renombra (atomico en el mismo
    # volumen C:), para que el flujo nunca lea un archivo a medio escribir/sincronizar.
    tmp = os.path.join(STATE_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(req_dir, name))


def run_bridge(interval, batch):
    req_dir, dec_dir = _bridge_dirs()
    print(f"== BRIDGE ==\n  requests  -> {req_dir}\n  decisions <- {dec_dir}")
    print(f"Ciclo cada {interval}s, hasta {batch} pendientes. Ctrl+C para parar.")
    while True:
        try:
            _bridge_tick(req_dir, dec_dir, batch)
        except Exception as ex:
            print(f"[tick error] {ex.__class__.__name__}: {ex}")
        time.sleep(interval)


def run_folders():
    dests = com(_dest_folders)
    print(f"Subcarpetas de Inbox ({len(dests)} destinos validos):")
    for _, p in dests:
        print(f"  {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Triage de inbox local con IA + Teams.")
    ap.add_argument("--dry-run", action="store_true", help="solo lectura: clasifica y muestra decisiones, no cambia nada")
    ap.add_argument("--folders", action="store_true", help="solo listar subcarpetas de Inbox")
    ap.add_argument("--bootstrap", action="store_true", help="minar historial -> patrones + embeddings")
    ap.add_argument("--bridge", action="store_true", help="loop: puente OneDrive<->Power Automate/Teams")
    ap.add_argument("--interval", type=int, default=20, help="segundos entre ciclos del bridge")
    ap.add_argument("--batch", type=int, default=5, help="max solicitudes pendientes a la vez")
    ap.add_argument("--per-folder", type=int, default=30, help="correos por carpeta en bootstrap")
    ap.add_argument("--limit", type=int, default=10, help="cuantos correos leer en --dry-run")
    args = ap.parse_args()
    if args.bridge:
        run_bridge(args.interval, args.batch)
    elif args.bootstrap:
        run_bootstrap(args.per_folder)
    elif args.folders:
        run_folders()
    elif args.dry_run:
        run_dry(args.limit)
    else:
        print("Usa --dry-run (prueba), --bootstrap (indexar), o --bridge (operacion con Teams).")

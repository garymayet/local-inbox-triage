"""
triage_laya.py  -  Pre-filtro local para triage_agent.py usando laya.

QUE HACE
--------
Antes de gastar una llamada completa al LLM (~73 s medidos con Gemma-3-4B en esta VM),
pregunta a laya -un motor de decision no autoregresivo, ~4-14 s- si el correo es de bajo
valor (newsletter / aviso automatico). Si laya esta razonablemente seguro, devuelve una
clasificacion minima y el agente NO llama al LLM para ese correo.

POR QUE ES SEGURO
-----------------
Solo actua cuando se cumplen TODAS estas condiciones:
  1. El remitente NO es sensible segun las reglas DETERMINISTAS del agente (domain_facts:
     dominios de cliente + sensitive_substrings). La sensibilidad nunca se delega a laya.
  2. laya clasifica en una de las categorias de bajo valor configuradas
     (por defecto newsletter / notification).
  3. La confianza de laya supera `laya_min_confidence` (por defecto 0.75).
  4. laya NO cree que el correo requiera respuesta/accion.

En cualquier otro caso devuelve None y el agente sigue por el camino de siempre (LLM).
Si laya no esta instalado o falla, tambien devuelve None: degrada sin romper nada.

MEDICION QUE LO JUSTIFICA (mismos 8 casos, ver docs/VM-SETUP-DSH.md)
--------------------------------------------------------------------
  laya  : 5/8 aciertos a 15.6 s de media   (multilingue ~4 s en caliente)
  Gemma : 7/8 aciertos a 73.0 s de media
La mayoria de una bandeja real son newsletters y avisos: resolverlos en ~4-14 s en vez de
73 s es el ahorro. La decision FINAL sobre correo que importa sigue siendo del LLM.

LIMITE HONESTO
--------------
1. NO puede redactar: `resumen` y borradores siguen siendo del LLM. Por eso los correos
   pre-filtrados se quedan sin resumen IA (la tarjeta muestra el extracto de 600 chars).
2. Sus respuestas de "sensible" NO son fiables (marco sensible un newsletter en las
   pruebas), por eso aqui no se usan para nada.
3. Sus confianzas vienen sin calibrar en el checkpoint base. De ahi el umbral alto (0.75)
   y que el pre-filtro solo ACTUE en categorias de bajo riesgo, nunca en las que importan.
"""

import threading

_router = None
_router_lock = threading.Lock()
_ultimo_error = None

# Mismas 10 categorias que VALID_CATS de triage_agent.py
CATEGORIAS = {
    "newsletter": "boletines, marketing, novedades de productos, suscripciones",
    "notification": "avisos automaticos: sistemas, alertas, confirmaciones, no-reply",
    "client_ops": "temas operativos de un cliente: pedidos, incidencias, solicitudes",
    "internal_team": "comunicacion interna del equipo: coordinacion, proyectos, avisos",
    "hr_admin": "recursos humanos y administracion: nomina, vacaciones, politicas",
    "finance": "facturacion, pagos, presupuestos, cobros, impuestos",
    "vendor": "proveedores y terceros: cotizaciones, licencias, contratos",
    "personal": "correo personal, no laboral",
    "action_required": "requiere una accion o respuesta concreta de mi parte",
    "ambiguous": "no encaja en ninguna categoria anterior con claridad",
}

# Preguntas minimas: solo lo que el pre-filtro necesita. Menos preguntas = menos tiempo.
_PREGUNTAS = {
    "categoria": {
        "type": "choice",
        "instructions": "Que tipo de correo corporativo es este?",
        "criteria": CATEGORIAS,
    },
    "requiere_respuesta": {
        "type": "choice",
        "instructions": "Este correo requiere que yo responda o actue?",
        "criteria": {"A": "si, requiere respuesta o accion mia", "B": "no, es informativo"},
    },
}

_VALID_CATS = set(CATEGORIAS.keys())


def _get_router():
    """Carga perezosa y unica del Router de laya (thread-safe)."""
    global _router
    if _router is not None:
        return _router
    with _router_lock:
        if _router is None:
            from laya import Router
            # default='multilingual': corrige el sesgo del router con texto latino no ingles
            _router = Router(default="multilingual")
    return _router


def disponible():
    """True si laya se puede importar (sin cargar checkpoints)."""
    try:
        import laya  # noqa: F401
        return True
    except Exception:
        return False


def ultimo_error():
    return _ultimo_error


def prefilter(email, facts, cfg):
    """
    Devuelve un dict de clasificacion (mismo formato que ollama_classify) si laya puede
    resolver el correo sin LLM; None si hay que llamar al LLM.
    """
    global _ultimo_error

    # 1) La sensibilidad es una regla DURA y determinista: nunca la decidimos con laya.
    if facts.get("is_sensitive"):
        return None

    skip_cats = [str(c).lower() for c in cfg.get("laya_skip_categories", ["newsletter", "notification"])]
    min_conf = float(cfg.get("laya_min_confidence", 0.75))

    estado = {
        "from": email.get("from", ""),
        "subject": email.get("subject", ""),
        "body": (email.get("body") or "")[:1500],
    }
    try:
        router = _get_router()
        res = router.predict(estado, _PREGUNTAS)
    except Exception as ex:
        _ultimo_error = f"{ex.__class__.__name__}: {ex}"
        return None

    ans = res.get("answers", {}) or {}
    cat = str(ans.get("categoria", {}).get("choice") or "").lower()
    try:
        conf = float(ans.get("categoria", {}).get("confidence") or 0.0)
    except Exception:
        conf = 0.0
    rr = ans.get("requiere_respuesta", {}).get("choice")

    # 2) solo categorias de bajo valor
    if cat not in skip_cats or cat not in _VALID_CATS:
        return None
    # 3) confianza suficiente
    if conf < min_conf:
        return None
    # 4) que laya no crea que hay que actuar
    if rr == "A":
        return None

    ruteo = (res.get("routing", {}) or {}).get("model", "")
    return {
        "category": cat,
        "language": {"english": "en"}.get(ruteo, "es"),
        "urgency": "low",
        "requires_reply": False,
        # Sin resumen a proposito: laya no genera texto. La tarjeta mostrara el extracto.
        "resumen": "",
        "reasoning": f"prefiltro local (laya/{ruteo or '?'}, conf {conf:.2f}) - sin llamada al LLM",
        "_prefiltered": True,
        "_prefilter_conf": conf,
    }

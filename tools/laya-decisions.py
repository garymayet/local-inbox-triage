#!/usr/bin/env python3
"""
laya-decisions.py  -  Capa de decision "System 1" para el triaje, con laya.

PAPEL DE LAYA EN ESTA AUTOMATIZACION (leer antes de usarlo)
-----------------------------------------------------------
laya NO es un LLM: no genera texto. Responde preguntas tipadas (choice / score /
noul) sobre un texto en UN solo forward pass, en 100+ idiomas, con confianza
calibrada. Eso encaja exactamente con la parte de DECISION del triaje:

    triage_agent.py hace hoy:            con laya se convierte en:
    ---------------------------------    ----------------------------------
    ollama_classify() -> categoria       choice   (10 categorias)   ~1 forward pass
                      -> urgencia        score    (3 niveles)
                      -> requires_reply  choice A/B (2 opciones)
                      -> resumen         *** SIGUE NECESITANDO EL LLM ***
    ollama_embed()                        (laya tambien puede dar embeddings)

Es decir: laya ACELERA y ABARATA las decisiones, pero el resumen y la redaccion
de borradores siguen necesitando un modelo generativo. Es un complemento, no un
reemplazo.

AVISO HONESTO (del README de laya, no mio)
------------------------------------------
* Los checkpoints base estan CASI EN AZAR en el benchmark de decisiones tipadas
  (0.36 y 0.35 contra 0.318 de azar). El 0.766 que publicitan se logra solo tras
  fine-tuning con datos propios. Trata a laya como "una base rapida para
  especializar, no un motor de decision zero-shot".
* Texto en espanol/portugues con solo ASCII puede caer en el checkpoint ingles.
  Por eso aqui se usa Router(default="multilingual").
* `noul` puede seguir sus etiquetas en vez del texto (bug #156). Por eso las
  preguntas booleanas de aqui se hacen como `choice` de 2 opciones con claves
  neutras, tal como recomienda el propio README de laya.
* El fine-tuning es donde esta el valor: este repositorio ya genera
  ~/.triage/decisions.jsonl con tus decisiones reales -> ese es el dataset.

USO
---
    pip install laya                      # + torch (CPU basta)
    python laya-decisions.py --self-test  # casos sinteticos es/en/pt, sin correo real
    python laya-decisions.py --json correo.json
    python laya-decisions.py --serve      # servidor HTTP compatible (laya[serve])

`correo.json` = {"from": "...", "subject": "...", "body": "..."}
"""

import argparse
import json
import sys
import time

# --- Las mismas categorias que VALID_CATS de triage_agent.py, para que sea drop-in ---
CATEGORIAS = {
    "newsletter":     "boletines, marketing, novedades de productos, suscripciones",
    "notification":   "avisos automaticos: sistemas, alertas, confirmaciones, no-reply",
    "client_ops":     "temas operativos de un cliente: pedidos, incidencias, solicitudes",
    "internal_team":  "comunicacion interna del equipo: coordinacion, proyectos, avisos",
    "hr_admin":       "recursos humanos y administracion: nomina, vacaciones, politicas",
    "finance":        "facturacion, pagos, presupuestos, cobros, impuestos",
    "vendor":         "proveedores y terceros: cotizaciones, licencias, contratos",
    "personal":       "correo personal, no laboral",
    "action_required": "requiere una accion o respuesta concreta de mi parte",
    "ambiguous":      "no encaja en ninguna categoria anterior con claridad",
}


def preguntas():
    """Preguntas tipadas equivalentes a la clasificacion del agente."""
    return {
        "categoria": {
            "type": "choice",
            "instructions": "Que tipo de correo corporativo es este?",
            "criteria": CATEGORIAS,
        },
        "urgencia": {
            "type": "score",
            "instructions": "Que urgencia tiene este correo?",
            "criteria": ["no urgente", "necesita atencion pronto",
                         "critico o con fecha limite que bloquea"],
        },
        # Booleanas como choice A/B (evita el bug conocido de `noul`)
        "requiere_respuesta": {
            "type": "choice",
            "instructions": "Este correo requiere que yo responda o actue?",
            "criteria": {"A": "si, requiere respuesta o accion mia",
                         "B": "no, es solo informativo"},
        },
        "es_sensible": {
            "type": "choice",
            "instructions": ("El remitente o el contenido es sensible (cliente, jefe, "
                             "recursos humanos, legal) y exige revision humana?"),
            "criteria": {"A": "si, es sensible", "B": "no, es rutinario"},
        },
    }


def cargar(device=None):
    """Carga laya con el router (elige checkpoint por idioma/escritura)."""
    try:
        from laya import Router
    except ImportError:
        sys.exit("Falta laya. Instalalo con:  pip install laya")
    # default='multilingual' corrige el sesgo del router con texto latino no ingles
    kwargs = {"default": "multilingual"}
    if device:
        kwargs["device"] = device
    return Router(**kwargs)


def evaluar(router, estado, etiqueta=""):
    q = preguntas()
    t0 = time.perf_counter()
    res = router.predict(estado, q)
    dt = (time.perf_counter() - t0) * 1000.0
    return res, dt


def imprimir(res, dt, etiqueta=""):
    print("=" * 78)
    if etiqueta:
        print(f"CASO: {etiqueta}")
    r = res.get("routing", {})
    if r:
        print(f"  ruteo      : {r.get('model')}  ({r.get('reason','')[:70]})")
    a = res.get("answers", {})
    cat = a.get("categoria", {})
    urg = a.get("urgencia", {})
    rr = a.get("requiere_respuesta", {})
    sens = a.get("es_sensible", {})
    print(f"  categoria  : {cat.get('choice')}   (confianza {cat.get('confidence', 0):.2f})")
    print(f"  urgencia   : {urg.get('score')} / 2   (confianza {urg.get('confidence', 0):.2f})")
    print(f"  responde?  : {rr.get('choice')}   (confianza {rr.get('confidence', 0):.2f})")
    print(f"  sensible?  : {sens.get('choice')}   (confianza {sens.get('confidence', 0):.2f})")
    print(f"  latencia   : {dt:.0f} ms  (1 solo forward pass, sin generar texto)")
    # Gate de autonomia sugerido: replica la logica conservadora del agente
    c = cat.get("confidence", 0)
    if c >= 0.85 and sens.get("choice") == "B":
        gate = "AUTO permitido (alta confianza y no sensible)"
    elif c >= 0.60:
        gate = "SUGERIR (confianza media)"
    else:
        gate = "PREGUNTAR (confianza baja)"
    print(f"  GATE       : {gate}")
    return {"categoria": cat.get("choice"), "confianza": c,
            "urgencia": urg.get("score"), "requiere_respuesta": rr.get("choice"),
            "sensible": sens.get("choice"), "latencia_ms": round(dt)}


def self_test(router):
    """Casos sinteticos en espanol, ingles y portugues (sin tocar tu buzon)."""
    casos = [
        ("Cliente factura duplicada (es)", {
            "from": "pagos@clientea.com", "subject": "Cargo duplicado factura #4411",
            "body": ("Buenos dias, nos han cobrado dos veces la factura de marzo. "
                     "Necesitamos la devolucion del cargo duplicado hoy o tendremos que "
                     "escalar el caso. Saludos.")}),
        ("Newsletter inglesa (en)", {
            "from": "news@vendor.com", "subject": "March product newsletter",
            "body": "Here are this month's updates, new features and upcoming webinars. "
                    "You can unsubscribe at any time."}),
        ("Notificacion automatica PT (pt)", {
            "from": "no-reply@sistema.com", "subject": "Sua senha foi alterada",
            "body": "Informamos que sua senha foi alterada com sucesso. Se nao foi voce, "
                    "entre em contato imediatamente."}),
        ("RRHH interno (es)", {
            "from": "rrhh@tuempresa.com", "subject": "Vacaciones pendientes de aprobar",
            "body": "Recuerda que tienes 5 dias de vacaciones pendientes de solicitar antes "
                    "del 31 de diciembre."}),
    ]
    filas = []
    for etiqueta, estado in casos:
        res, dt = evaluar(router, estado)
        filas.append(imprimir(res, dt, etiqueta))
    print("=" * 78)
    print("RESUMEN DEL SELF-TEST")
    print(f"{'caso':<32}{'categoria':<18}{'urg':<5}{'resp':<6}{'sens':<6}{'ms':>6}")
    for (etiqueta, _), f in zip(casos, filas):
        print(f"{etiqueta[:31]:<32}{str(f['categoria'])[:17]:<18}{str(f['urgencia']):<5}"
              f"{str(f['requiere_respuesta']):<6}{str(f['sensible']):<6}{f['latencia_ms']:>6}")
    print()
    print("RECUERDA: esto mide la DECISION. Valida la calidad contra tus propios correos")
    print("y, si quieres fiabilidad de produccion, haz fine-tuning con:")
    print("    ~/.triage/decisions.jsonl   (lo genera triage_agent.py al operar)")
    return filas


def main():
    ap = argparse.ArgumentParser(description="Decisiones tipadas del triaje con laya")
    ap.add_argument("--self-test", action="store_true", help="casos sinteticos es/en/pt")
    ap.add_argument("--json", help="fichero con {from, subject, body}")
    ap.add_argument("--device", help="cpu o cuda (por defecto, lo que detecte)")
    ap.add_argument("--serve", action="store_true",
                    help="arranca laya-serve (requiere pip install 'laya[serve]')")
    args = ap.parse_args()

    if args.serve:
        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "laya.serve"], check=False)
        except Exception as e:
            sys.exit(f"No pude arrancar laya-serve: {e}  (pip install 'laya[serve]')")
        return

    router = cargar(args.device)

    if args.json:
        with open(args.json, encoding="utf-8-sig") as f:
            estado = json.load(f)
        res, dt = evaluar(router, estado)
        imprimir(res, dt, estado.get("subject", "correo"))
        return

    self_test(router)


if __name__ == "__main__":
    main()

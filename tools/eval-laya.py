#!/usr/bin/env python3
"""
eval-laya.py  -  Head-to-head de laya contra el LLM local sobre los MISMOS 8 casos.

Responde con numeros a "¿laya complementa la automatizacion?": usa exactamente los mismos
correos sinteticos y el mismo criterio de acierto que tools/eval-models.ps1 (que mide el LLM),
para que la comparacion sea manzanas con manzanas.

DIFERENCIA CLAVE frente al LLM: laya no genera texto, devuelve decisiones tipadas. Por tanto
NO PUEDE fallar el JSON (no hay texto que parsear, no hay alucinacion de formato). En el LLM
ese criterio se midio como 8/8; aqui es 8/8 por construccion, no por merito del modelo.

Uso:
    C:\\Users\\mmayet\\laya-venv\\Scripts\\python.exe eval-laya.py
"""
import sys
import time

# --- Las 10 categorias del agente (VALID_CATS de triage_agent.py) ---
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

PREGUNTAS = {
    "categoria": {
        "type": "choice",
        "instructions": "Que tipo de correo corporativo es este?",
        "criteria": CATEGORIAS,
    },
    "urgencia": {
        "type": "score",
        "instructions": "Que urgencia tiene este correo?",
        "criteria": ["no urgente", "necesita atencion pronto", "critico o con fecha limite"],
    },
    "requiere_respuesta": {
        "type": "choice",
        "instructions": "Este correo requiere que yo responda o actue?",
        "criteria": {"A": "si, requiere respuesta o accion mia", "B": "no, es informativo"},
    },
    "es_sensible": {
        "type": "choice",
        "instructions": "El remitente o el contenido es sensible y exige revision humana?",
        "criteria": {"A": "si, es sensible", "B": "no, es rutinario"},
    },
}

# --- MISMOS casos y MISMOS criterios de acierto que eval-models.ps1 ---
CASOS = [
    {"id": "factura-cliente-es", "from": "pagos@clientea.com", "subject": "Cargo duplicado factura #4411",
     "body": "Buenos dias, la factura de marzo fue cobrada dos veces. Necesitamos la devolucion del cargo duplicado antes del viernes o escalaremos el caso. Saludos.",
     "ok": ["finance", "client_ops"]},
    {"id": "newsletter-en", "from": "news@vendor.com", "subject": "March product newsletter",
     "body": "Here are this month's updates, new features and upcoming webinars. You can unsubscribe at any time.",
     "ok": ["newsletter"]},
    {"id": "aviso-password-pt", "from": "no-reply@sistema.com", "subject": "Sua senha foi alterada",
     "body": "Informamos que sua senha foi alterada com sucesso. Se nao foi voce, entre em contato imediatamente.",
     "ok": ["notification"]},
    {"id": "rrhh-vacaciones-es", "from": "rrhh@dxc.com", "subject": "Vacaciones pendientes de aprobar",
     "body": "Recuerda que tienes 5 dias de vacaciones pendientes de solicitar antes del 31 de diciembre.",
     "ok": ["hr_admin", "notification"]},
    {"id": "cotizacion-proveedor-es", "from": "ventas@proveedor.com", "subject": "Cotizacion licencias anuales",
     "body": "Adjunto la cotizacion de las 25 licencias anuales que solicito. El precio unitario baja si confirmamos antes de fin de mes.",
     "ok": ["vendor", "finance"]},
    {"id": "alerta-monitoreo-en", "from": "alerts@monitoring.internal", "subject": "ALERT: CPU above 95% for 15 min",
     "body": "Automated alert: host srv-app-07 CPU utilization has exceeded 95% for 15 minutes. No action required if already known.",
     "ok": ["notification"]},
    {"id": "coordinacion-interna-es", "from": "companero@dxc.com", "subject": "Revision del sprint el jueves",
     "body": "Hola, movemos la revision del sprint al jueves a las 10? Necesito que confirmes si puedes asistir para cerrar el alcance.",
     "ok": ["internal_team", "action_required"]},
    {"id": "personal-en", "from": "john.friend@gmail.com", "subject": "BBQ this weekend?",
     "body": "Hey! We are doing a barbecue on Saturday, want to come over? Let me know so I can buy enough food.",
     "ok": ["personal"]},
]

# Resultados MEDIDOS del LLM (eval-models.ps1) para la comparacion final
LLM_MEDIDO = [
    {"modelo": "gemma-3-4b-it", "aciertos": 7, "json": 8, "lat": 73.0},
    {"modelo": "Qwen2.5-3B-Instruct", "aciertos": 5, "json": 8, "lat": 41.7},
]


def main():
    try:
        from laya import Router
    except ImportError:
        sys.exit("Falta laya. Usa el venv: C:\\Users\\mmayet\\laya-venv\\Scripts\\python.exe")

    print("===================================================================")
    print(" EVALUACION DE LAYA - mismos 8 casos que el LLM")
    print("===================================================================")
    t0 = time.perf_counter()
    # default='multilingual' corrige el sesgo del router con texto latino no ingles
    router = Router(default="multilingual")
    print("  Router creado (carga perezosa de checkpoints; el primero tarda unos segundos)")
    print("")

    filas = []
    aciertos = 0
    for c in CASOS:
        estado = {"from": c["from"], "subject": c["subject"], "body": c["body"]}
        t1 = time.perf_counter()
        try:
            res = router.predict(estado, PREGUNTAS)
            dt = time.perf_counter() - t1
            ans = res.get("answers", {})
            cat = ans.get("categoria", {}).get("choice", "?")
            conf = ans.get("categoria", {}).get("confidence", 0.0)
            urg = ans.get("urgencia", {}).get("score")
            rr = ans.get("requiere_respuesta", {}).get("choice")
            sens = ans.get("es_sensible", {}).get("choice")
            ruta = res.get("routing", {}).get("model", "?")
        except Exception as e:
            dt = time.perf_counter() - t1
            cat, conf, urg, rr, sens, ruta = "ERROR", 0.0, None, None, None, str(e)[:40]
        ok = cat in c["ok"]
        if ok:
            aciertos += 1
        filas.append((c["id"], cat, "/".join(c["ok"]), ok, dt, conf, urg, rr, sens, ruta))
        print(f"  {c['id']:<24} -> {cat:<16} (esperado {'/'.join(c['ok']):<26}) "
              f"{'OK' if ok else 'FALLO':<6} {dt:6.2f}s  conf={conf:.2f}")

    lats = [f[4] for f in filas if f[1] != "ERROR"]
    media = sum(lats) / len(lats) if lats else 0.0

    print("")
    print("===================================================================")
    print(" RESULTADO: LAYA vs LLM LOCAL (mismos casos, mismo criterio)")
    print("===================================================================")
    print(f"{'Modelo':<30}{'Aciertos cat':>14}{'JSON valido':>14}{'Latencia media':>16}")
    print("-" * 78)

    def fila(nombre, aciertos_n, json_n, lat):
        a = f"{aciertos_n}/{len(CASOS)}"
        j = f"{json_n}/{len(CASOS)}"
        l = f"{lat} s"
        print(nombre.ljust(30) + a.rjust(14) + j.rjust(14) + l.rjust(16))

    fila("laya (router multilingue)", aciertos, len(CASOS), f"{media:.1f}")
    for m in LLM_MEDIDO:
        fila(m["modelo"], m["aciertos"], m["json"], m["lat"])
    print("")
    print("  * laya NO PUEDE fallar el JSON: no genera texto, devuelve decisiones tipadas.")
    print("    Ese 8/8 es por construccion, no por merito del modelo.")
    print("")
    print("  Detalle de urgencia / responde / sensible devuelto por laya:")
    for f in filas:
        print(f"    {f[0]:<24} urg={f[6]}  responde={f[7]}  sensible={f[8]}  (ruteo: {f[9]})")
    print("")
    print("  CONCLUSION PRACTICA:")
    if aciertos >= 7:
        print("    laya iguala o supera la calidad de categoria del mejor LLM, a una fraccion")
        print("    del coste. Es un candidato SERIO para sustituir la CLASIFICACION.")
    elif aciertos >= 5:
        print("    laya clasifica razonablemente pero por debajo del mejor LLM medido:")
        print("    usala como pre-filtro barato y deja la decision final al LLM.")
    else:
        print("    laya NO alcanza la calidad necesaria en esta taxonomia sin fine-tuning.")
    print("    En cualquier caso NO puede redactar: resumen y borradores siguen siendo del LLM.")


if __name__ == "__main__":
    main()

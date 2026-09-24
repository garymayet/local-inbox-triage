#!/usr/bin/env python3
"""
test-prefilter.py  -  Mide el pre-filtro de laya sobre los MISMOS 8 casos.

No necesita Outlook ni el LLM: solo mide cuanto trabajo caro se ahorra y con que acierto.
Es la validacion del cambio en triage_agent.py (funcion classify + triage_laya.prefilter).

Uso:
    C:\\Users\\mmayet\\laya-venv\\Scripts\\python.exe test-prefilter.py
"""
import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
# triage_laya.py vive en la RAIZ del repo; este test, en tools/
sys.path.insert(0, os.path.dirname(_here))
sys.path.insert(0, _here)

import triage_laya  # noqa: E402

# Config equivalente a la del agente para la prueba
CFG = {
    "client_domains": ["clientea.com"],
    "sensitive_substrings": ["rrhh", "legal", "juridico"],
    "use_laya_prefilter": True,
    "laya_skip_categories": ["newsletter", "notification"],
    "laya_min_confidence": 0.75,
}

# Mismos 8 casos que eval-models.ps1 / eval-laya.py
CASOS = [
    {"id": "factura-cliente-es", "from": "pagos@clientea.com", "subject": "Cargo duplicado factura #4411",
     "body": "Buenos dias, la factura de marzo fue cobrada dos veces. Necesitamos la devolucion del cargo duplicado antes del viernes o escalaremos el caso.",
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
     "body": "Adjunto la cotizacion de las 25 licencias anuales. El precio unitario baja si confirmamos antes de fin de mes.",
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

# Latencia MEDIDA del LLM en esta VM (eval-models.ps1) para estimar el ahorro
LLM_LATENCIA = 73.0


def facts_agente(smtp, cfg):
    """Replica domain_facts() de triage_agent.py (sensibilidad DETERMINISTA)."""
    smtp = (smtp or "").lower()
    dom = smtp.split("@")[-1] if "@" in smtp else ""
    sens = [f"@{d}" for d in cfg["client_domains"]] + [s.lower() for s in cfg["sensitive_substrings"]]
    return {
        "is_sensitive": any(s in smtp for s in sens),
        "domain": dom,
    }


def main():
    if not triage_laya.disponible():
        sys.exit("laya no instalado en este interprete. Usa el venv laya-venv.")

    print("=" * 78)
    print(" PRE-FILTRO DE LAYA EN triage_agent.py - validacion sobre 8 casos")
    print("=" * 78)
    print(f"  umbral de confianza : {CFG['laya_min_confidence']}")
    print(f"  categorias que evitan el LLM : {', '.join(CFG['laya_skip_categories'])}")
    print(f"  latencia LLM de referencia   : {LLM_LATENCIA} s/correo (medida con Gemma-3-4B)")
    print("")

    filas = []
    for c in CASOS:
        email = {"from": c["from"], "subject": c["subject"], "body": c["body"]}
        f = facts_agente(c["from"], CFG)
        t0 = time.perf_counter()
        pre = triage_laya.prefilter(email, f, CFG)
        dt = time.perf_counter() - t0
        filas.append({"id": c["id"], "ok": c["ok"], "facts": f, "pre": pre, "dt": dt})

    print(f"{'caso':<24}{'sensible':<10}{'pre-filtrado':<14}{'categoria':<16}{'conf':<7}{'s':>7}")
    print("-" * 78)
    for f in filas:
        pre = f["pre"]
        if pre:
            print(f"{f['id']:<24}{str(f['facts']['is_sensitive']):<10}{'SI (ahorra LLM)':<14}"
                  f"{pre['category']:<16}{pre.get('_prefilter_conf', 0):<7.2f}{f['dt']:>7.2f}")
        else:
            print(f"{f['id']:<24}{str(f['facts']['is_sensitive']):<10}{'no':<14}{'-':<16}{'-':<7}{f['dt']:>7.2f}")

    pref = [f for f in filas if f["pre"]]
    aciertos = sum(1 for f in pref if f["pre"]["category"] in f["ok"])
    incorrectos = [f["id"] for f in pref if f["pre"]["category"] not in f["ok"]]
    tiempo_laya = sum(f["dt"] for f in pref)
    ahorro = len(pref) * LLM_LATENCIA - tiempo_laya

    print("")
    print("=" * 78)
    print(" RESULTADO")
    print("=" * 78)
    print(f"  correos que EVITAN la llamada al LLM : {len(pref)}/{len(filas)}")
    print(f"  de esos, categoria correcta          : {aciertos}/{len(pref)}")
    if incorrectos:
        print(f"  categoria incorrecta en              : {', '.join(incorrectos)}")
    print(f"  tiempo empleado por laya             : {tiempo_laya:.1f} s")
    print(f"  tiempo que habria costado el LLM     : {len(pref) * LLM_LATENCIA:.1f} s")
    print(f"  AHORRO estimado en esos correos      : {ahorro:.1f} s  ({ahorro / 60:.1f} min)")
    if len(pref) > 0:
        print(f"  factor de aceleracion en los saltados: {LLM_LATENCIA / max(tiempo_laya / len(pref), 0.01):.1f}x")
    print("")
    print("  Seguridad: ningun correo con remitente sensible se pre-filtra (columna 'sensible').")
    print("  Los correos pre-filtrados salen SIN resumen IA, a proposito: laya no genera texto.")
    print("  Si aparece una categoria incorrecta arriba, sube laya_min_confidence en config.json.")


if __name__ == "__main__":
    main()

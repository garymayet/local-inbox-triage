"""Fija 'client_domains' en config.json, con copia de seguridad. Idempotente.

Por que importa: en triage_agent.py, CLIENT_DOMAINS alimenta SENSITIVE_SUBSTR (@dominio),
asi que TODO correo de un cliente queda marcado como sensible y NUNCA se auto-mueve:
siempre pregunta. Un dominio de mas solo hace que pregunte de mas; uno de menos deja que
el agente archive solo correo de un cliente.

La lista por defecto NO es una suposicion: sale de medir el buzon con
tools/scan_client_domains.py (Inbox reciente + carpetas), 24-sep-2026.

Uso:
    python tools/set-client-domains.py                      # usa la lista medida
    python tools/set-client-domains.py kof.com vale.com ... # lista explicita
"""
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(REPO, "config.json")
BAK = CFG + ".bak-clientdomains"

# Medidos con tools/scan_client_domains.py: dominio -> evidencia
MEDIDOS = [
    "kof.com",            # 49 correos (Coca-Cola FEMSA)
    "profuturo.com.mx",   # 32
    "vale.com",           # 31
    "nadro.com.mx",       # 28
    "aeromexico.com",     #  9
    "davivienda.com",     #  5
]


def main():
    dominios = sorted({d.strip().lower() for d in sys.argv[1:] if d.strip()}) or MEDIDOS
    if not os.path.isfile(CFG):
        print(f"ERROR: no existe {CFG}")
        return 1
    # utf-8-sig: config.json en esta VM tiene BOM (lo metio Windows en algun momento).
    # El agente ya lo tolera (triage_agent.py linea 36); este script debe hacer lo mismo.
    with open(CFG, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    antes = list(cfg.get("client_domains", []))
    if sorted(antes) == dominios:
        print(f"ya estaba igual (idempotente): {antes}")
        return 0

    shutil.copy2(CFG, BAK)
    cfg["client_domains"] = dominios
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    with open(CFG, encoding="utf-8") as fh:
        leido = json.load(fh)
    print(f"backup   : {BAK}")
    print(f"antes    : {antes}")
    print(f"ahora    : {leido['client_domains']}")
    print(f"internal : {leido.get('internal_domains')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

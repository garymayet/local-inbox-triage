"""SOLO LECTURA. Deriva 'client_domains' de lo que el bootstrap aprendio de verdad.

Por que importa: en triage_agent.py, CLIENT_DOMAINS alimenta SENSITIVE_SUBSTR
('@dominio'), de modo que TODO correo de un cliente se marca como sensible y NUNCA se
auto-mueve: siempre te pregunta. Si falta el dominio de un cliente, el agente puede
archivar solo correo de cliente. Por eso la lista se deriva del buzon (patterns.json)
en vez de adivinar dominios a partir del nombre de la carpeta.

No necesita Outlook ni el LLM: solo lee ~/.triage/patterns.json.
"""
import collections
import json
import os
import sys

# Permite indicar el STATE_DIR: al correr como SYSTEM, ~ es el perfil de SYSTEM
# (C:\Windows\system32\config\systemprofile) y no el del usuario del agente.
STATE = (sys.argv[1] if len(sys.argv) > 1
         else os.path.join(os.path.expanduser("~"), ".triage"))
PATS = os.path.join(STATE, "patterns.json")

# Proveedores / infraestructura / RRHH: aparecen mucho pero NO son el cliente.
RUIDO = (
    "microsoft.com", "office365.com", "microsoftonline.com", "sharepointonline.com",
    "messaging.microsoft.com", "teams.mail.microsoft", "windows.com",
    "amazon.com", "amazonaws.com", "aws.com", "signin.aws", "verify.signin.aws",
    "google.com", "googlemail.com", "github.com", "atlassian.net", "atlassian.com",
    "servicenow.com", "service-now.com", "salesforce.com", "zoom.us", "cisco.com",
    "vmware.com", "oracle.com", "oraclecloud.com", "sap.com", "ibm.com", "dell.com",
    "hp.com", "adobe.com", "slack.com", "linkedin.com", "indeed.com", "workday.com",
    "myworkday.com", "adp.com", "deltek.com", "udemymail.com", "udemy.com",
    "concursolutions.com", "ariba.com", "workhuman.com", "cloudcheckr.com",
    "dhl.com", "valeglobal.net",
)
INTERNOS = ("dxc.com",)


def main():
    if not os.path.isfile(PATS):
        print(f"no existe {PATS}: ejecuta antes el bootstrap (--bootstrap)")
        return 1
    with open(PATS, encoding="utf-8") as fh:
        pats = json.load(fh)

    filas = []
    for key, carpetas in pats.items():
        if not key.startswith("domain:"):
            continue
        dom = key.split(":", 1)[1].lower()
        if not dom:
            continue
        total = sum(c.get("observed", 0) for c in carpetas.values())
        # carpeta donde mas aparece este dominio
        carp = max(carpetas.items(), key=lambda kv: sum(kv[1].values()))
        filas.append((dom, total, carp[0], sum(carp[1].values())))

    filas.sort(key=lambda f: -f[1])

    print(f"claves 'domain:' en patterns.json : {sum(1 for k in pats if k.startswith('domain:'))}")
    print(f"claves 'from:'   en patterns.json : {sum(1 for k in pats if k.startswith('from:'))}")
    print()
    print(f"{'DOMINIO':44s} {'TOT':>5s}  CARPETA PRINCIPAL")
    print("-" * 110)
    for dom, total, carp, k in filas:
        print(f"{dom:44s} {total:5d}  {carp}  ({k})")

    cand = [(d, t, c) for d, t, c, _ in filas
            if not any(d.endswith(i) for i in INTERNOS)
            and not any(r == d or d.endswith("." + r) for r in RUIDO)
            and t >= 2]
    print()
    print("=" * 110)
    print("CANDIDATOS a client_domains (externos, fuera de ruido de infra, >=2 correos)")
    print("Revisa esta lista ANTES de pegarla: un dominio de mas solo hace que pregunte de mas;")
    print("uno de menos permite que el agente archive solo correo de un cliente.")
    print("=" * 110)
    for d, t, c in sorted(cand):
        print(f'    "{d}",   # {t} correos, sobre todo en {c}')
    print()
    print("--- listo para pegar en config.json ---")
    print(json.dumps({"client_domains": sorted(d for d, _, _ in cand)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

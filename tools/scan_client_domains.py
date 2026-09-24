"""SOLO LECTURA. Busca dominios de CLIENTE reales en el buzon.

Por que hace falta: el bootstrap solo mina correo YA ARCHIVADO en carpetas, asi que se le
escapan los clientes cuyo correo esta en el Inbox (p.ej. el hilo de WIZ de aeromexico.com)
o en carpetas poco pobladas. Esta sonda mira el Inbox reciente + las carpetas de CloudOps y
saca los dominios EXTERNOS con su recuento, que es lo que alimenta 'client_domains'.

Rapido a proposito: NO resuelve remitentes internos de Exchange (eso cuesta una llamada a
AD por correo y no aporta nada aqui, porque un cliente siempre es externo).
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402

INBOX_LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 400
CARPETA_LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 80

RUIDO = (
    "microsoft.com", "office365.com", "microsoftonline.com", "sharepointonline.com",
    "messaging.microsoft.com", "teams.mail.microsoft", "windows.com", "azure.com",
    "amazon.com", "amazonaws.com", "aws.com", "signin.aws", "verify.signin.aws",
    "google.com", "googlemail.com", "github.com", "atlassian.net", "atlassian.com",
    "servicenow.com", "service-now.com", "salesforce.com", "zoom.us", "cisco.com",
    "vmware.com", "oracle.com", "oraclecloud.com", "sap.com", "ibm.com", "dell.com",
    "hp.com", "adobe.com", "slack.com", "linkedin.com", "indeed.com", "workday.com",
    "myworkday.com", "adp.com", "deltek.com", "udemymail.com", "udemy.com",
    "concursolutions.com", "ariba.com", "workhuman.com", "cloudcheckr.com",
    "dhl.com", "valeglobal.net", "pagerduty.com", "datadoghq.com", "splunk.com",
    "newrelic.com", "grafana.com", "elastic.co", "mongodb.com", "redhat.com",
    "docker.com", "cloudflare.com", "akamai.com", "zscaler.com", "crowdstrike.com",
    "duosecurity.com", "okta.com", "onmicrosoft.com", "noreply", "no-reply",
)
INTERNOS = ("dxc.com",)


def _dominio(item):
    try:
        if item.SenderEmailType == "EX":
            return None
        a = (item.SenderEmailAddress or "").lower()
        return a.split("@")[-1].strip("> ").strip() if "@" in a else None
    except Exception:
        return None


def _scan(path, limit):
    f = ta._find_by_path(path) if path else ta._inbox
    if f is None:
        return collections.Counter(), 0
    try:
        items = f.Items
        items.Sort("[ReceivedTime]", True)
    except Exception:
        return collections.Counter(), 0
    c = collections.Counter()
    n = 0
    for it in items:
        if n >= limit:
            break
        try:
            if it.Class != getattr(ta, "OL_MAIL", 43):
                continue
        except Exception:
            continue
        n += 1
        d = _dominio(it)
        if d:
            c[d] += 1
    return c, n


def main():
    dests = ta.com(ta._dest_folders)
    cloudops = [p for _, p in dests if "cloudops" in p.lower()]
    objetivos = [None] + [p for _, p in dests]

    agg = {}
    leidos = 0
    for path in objetivos:
        limite = INBOX_LIMIT if path is None else CARPETA_LIMIT
        c, n = ta.com(_scan, path, limite)
        leidos += n
        etiqueta = path or "<Inbox>"
        for d, k in c.items():
            if any(d.endswith(i) for i in INTERNOS):
                continue
            e = agg.setdefault(d, {"total": 0, "carpetas": collections.Counter()})
            e["total"] += k
            e["carpetas"][etiqueta] += k

    print(f"correos leidos : {leidos}   (Inbox hasta {INBOX_LIMIT}, cada carpeta hasta {CARPETA_LIMIT})")
    print(f"carpetas CloudOps: {len(cloudops)}")
    print(f"dominios externos: {len(agg)}")
    print()
    print(f"{'DOMINIO':44s} {'TOT':>5s}  CARPETA PRINCIPAL")
    print("-" * 108)
    for d, e in sorted(agg.items(), key=lambda kv: -kv[1]["total"])[:60]:
        carp, k = e["carpetas"].most_common(1)[0]
        print(f"{d:44s} {e['total']:5d}  {carp}  ({k})")

    cand = [(d, e) for d, e in agg.items()
            if not any(r == d or d.endswith("." + r) or r in d for r in RUIDO)
            and e["total"] >= 2]
    print()
    print("=" * 108)
    print("CANDIDATOS a client_domains (externos, sin ruido de infra, >=2 correos)")
    print("=" * 108)
    for d, e in sorted(cand):
        detalle = ", ".join(f"{c} ({n})" for c, n in e["carpetas"].most_common(3))
        print(f'    "{d}",   # {e["total"]} correos: {detalle}')
    print()
    print("--- listo para pegar ---")
    print("client_domains = " + repr(sorted(d for d, _ in cand)))
    print("FIN")


main()

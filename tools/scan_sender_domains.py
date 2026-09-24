"""SOLO LECTURA. Recorre las carpetas destino del Inbox y cuenta los dominios de
remitente EXTERNOS de cada una. Sirve para proponer 'client_domains' con datos
reales en vez de adivinarlos. No usa el LLM ni embeddings: solo COM de Outlook.

Por que existe: 'client_domains' no es cosmetico. En triage_agent.py alimenta
SENSITIVE_SUBSTR (@dominio) -> todo correo de un cliente queda marcado como
sensible y NUNCA se auto-mueve: siempre se pregunta. Si falta un dominio de
cliente, el agente puede archivar solo correo de cliente. Por eso se deriva del
buzon, no de una lista de nombres de carpeta.

Salida completa (por carpeta) -> C:\\ProgramData\\sender-domains.txt
Resumen (dominio -> carpeta principal) por stdout.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402

PER_FOLDER = int(sys.argv[1]) if len(sys.argv) > 1 else 50
OUT = r"C:\ProgramData\sender-domains.txt"
INTERNOS = ("dxc.com",)

# Infraestructura / proveedores: casi nunca son "el cliente" aunque aparezcan.
RUIDO = (
    "microsoft.com", "office365.com", "microsoftonline.com", "windows.com",
    "amazon.com", "amazonaws.com", "aws.com", "google.com", "googlemail.com",
    "github.com", "atlassian.net", "atlassian.com", "servicenow.com",
    "salesforce.com", "zoom.us", "cisco.com", "vmware.com", "oracle.com",
    "sap.com", "ibm.com", "dell.com", "hp.com", "adobe.com", "slack.com",
    "noreply", "notifications", "linkedin.com", "indeed.com", "workday.com",
)


def _dominio(item):
    """Dominio del remitente, rapido. Los internos (EX) no interesan aqui:
    los clientes son externos, y resolver EX cuesta una llamada a AD por correo."""
    try:
        if item.SenderEmailType == "EX":
            return None
        a = (item.SenderEmailAddress or "").lower()
        return a.split("@")[-1].strip("> ").strip() if "@" in a else None
    except Exception:
        return None


def _scan(path, limit):
    f = ta._find_by_path(path)
    items = f.Items
    try:
        items.Sort("[ReceivedTime]", True)
    except Exception:
        pass
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
    por_carpeta = {}
    total = 0
    for nombre, path in dests:
        try:
            c, n = ta.com(_scan, path, PER_FOLDER)
        except Exception as ex:
            c, n = collections.Counter(), 0
            print(f"  ! {path}: {ex.__class__.__name__}")
        por_carpeta[path] = (c, n)
        total += n

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(f"carpetas={len(dests)} correos_leidos={total} por_carpeta={PER_FOLDER}\n")
        for path, (c, n) in por_carpeta.items():
            fh.write(f"\n=== {path}  ({n} correos leidos)\n")
            for d, k in c.most_common(25):
                fh.write(f"    {d:44s} {k}\n")

    # Agregado: para cada dominio externo, donde aparece mas
    agg = {}
    for path, (c, n) in por_carpeta.items():
        for d, k in c.items():
            if any(d.endswith(i) for i in INTERNOS):
                continue
            a = agg.setdefault(d, {"total": 0, "carpetas": collections.Counter()})
            a["total"] += k
            a["carpetas"][path] += k

    print(f"carpetas destino     : {len(dests)}")
    print(f"correos leidos        : {total}  (hasta {PER_FOLDER} por carpeta, los mas recientes)")
    print(f"dominios externos     : {len(agg)}")
    print(f"detalle por carpeta   : {OUT}")
    print()
    print("DOMINIO EXTERNO                            TOTAL  CARPETA PRINCIPAL")
    print("-" * 100)
    for d, a in sorted(agg.items(), key=lambda kv: -kv[1]["total"])[:90]:
        carp, k = a["carpetas"].most_common(1)[0]
        print(f"{d:42s} {a['total']:6d}  {carp}  ({k})")

    cand = [d for d, a in agg.items()
            if not any(r in d for r in RUIDO) and a["total"] >= 3]
    print()
    print("CANDIDATOS a client_domains (fuera de ruido de infra, >=3 correos):")
    for d in sorted(cand):
        carp, k = agg[d]["carpetas"].most_common(1)[0]
        print(f"    \"{d}\",   # {k} en {carp}")


# main() corre en el hilo PRINCIPAL a proposito: por dentro usa ta.com(...) y el
# pool de COM tiene un solo worker, asi que meterlo dentro del pool se bloquearia.
main()

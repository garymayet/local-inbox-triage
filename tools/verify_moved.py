"""SOLO LECTURA. Comprueba que la decision de Teams se aplico de verdad en Outlook:
el correo debe estar YA en la carpeta elegida y no en el Inbox."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402

OBJETIVO = "Inbox/CloudOps/Aeromexico"
AGUJA = "[WIZ] Vulnerabilidades"


def _buscar(folder_path, aguja):
    f = ta._find_by_path(folder_path) if folder_path else ta._inbox
    if f is None:
        return {"existe": False}
    total = 0
    hits = []
    for it in f.Items:
        try:
            if it.Class != ta.OL_MAIL:
                continue
        except Exception:
            continue
        total += 1
        try:
            sub = it.Subject or ""
        except Exception:
            continue
        if aguja.lower() in sub.lower():
            hits.append({"subject": sub, "from": ta._sender_smtp(it)})
    return {"existe": True, "carpeta": f.Name, "items": total, "coincidencias": hits}


print("=== donde esta el correo de aeromexico")
for carpeta in (OBJETIVO, ""):
    etiqueta = carpeta if carpeta else "<Inbox directo>"
    r = ta.com(_buscar, carpeta, AGUJA)
    if not r.get("existe"):
        print(f"  {etiqueta}: NO EXISTE la carpeta")
        continue
    print(f"  {etiqueta}: {r['items']} correos, {len(r['coincidencias'])} coincidencias")
    for h in r["coincidencias"]:
        print(f"      - {h['subject']!r}  de {h['from']}")

print()
print("=== decisiones registradas por el agente (decisions.jsonl)")
d = os.path.join(ta.STATE_DIR, "decisions.jsonl")
if os.path.isfile(d):
    with open(d, encoding="utf-8") as fh:
        for linea in fh:
            print("  " + linea.rstrip())
else:
    print("  (no existe)")
print("FIN")

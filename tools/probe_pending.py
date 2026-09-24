"""Sonda de solo lectura: comprueba si los correos pendientes del bridge siguen en el Inbox.
Sirve para saber si una tarjeta de Teams todavia es 'accionable' antes de probar el puente.

OJO: los objetos COM viven en el hilo del pool del agente; todo acceso va por ta.com(...)
o lanza 'marshalled for a different thread'."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402


def _detalle(entry_id):
    it = ta._ns.GetItemFromID(entry_id)
    return {"asunto": it.Subject, "ruta": it.Parent.FolderPath, "en_inbox": ta._in_inbox(entry_id)}


pend = ta.load_pending()
print(f"pendientes en pending.json: {len(pend)}")
print(f"seen.json: {len(ta.load_seen())} entradas")
for cid, m in pend.items():
    eid = m.get("entry_id")
    try:
        d = ta.com(_detalle, eid)
    except Exception as ex:
        d = {"asunto": f"<error {ex.__class__.__name__}>", "ruta": "?", "en_inbox": "?"}
    print(f"  cid={cid[:24]}")
    print(f"    from     = {m.get('from')}")
    print(f"    sugerido = {m.get('suggested')!r}")
    print(f"    reqfile  = {m.get('reqfile')}")
    print(f"    en Inbox = {d['en_inbox']}")
    print(f"    asunto   = {d['asunto']!r}")
    print(f"    esta en  = {d['ruta']}")
print("FIN SONDA")

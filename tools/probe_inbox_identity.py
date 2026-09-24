"""SOLO LECTURA. Reproduce exactamente lo que hace el bridge: lista el Inbox y
comprueba _in_inbox sobre esos mismos correos, en el momento. Si devuelve False para
correos que SI estan en el Inbox, la reconciliacion de _bridge_tick cancela tarjetas
vivas (que es lo que estamos viendo)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402


def _diag(n):
    inbox_eid = ta._inbox.EntryID
    filas = []
    items = ta._inbox.Items
    try:
        items.Sort("[ReceivedTime]", True)
    except Exception:
        pass
    i = 0
    for it in items:
        if i >= n:
            break
        try:
            if it.Class != ta.OL_MAIL:
                continue
        except Exception:
            continue
        i += 1
        fila = {"asunto": (it.Subject or "")[:45], "entry": it.EntryID}
        try:
            par = it.Parent.EntryID
            fila["parent"] = par
            fila["parent_ok"] = (par == inbox_eid)
        except Exception as ex:
            fila["parent"] = f"<error {ex.__class__.__name__}: {ex}>"
            fila["parent_ok"] = None
        try:
            fila["in_inbox"] = ta._in_inbox(it.EntryID)
        except Exception as ex:
            fila["in_inbox"] = f"<error {ex.__class__.__name__}>"
        try:
            fila["parent_name"] = it.Parent.Name
        except Exception as ex:
            fila["parent_name"] = f"<error {ex.__class__.__name__}>"
        filas.append(fila)
    return inbox_eid, filas


eid_inbox, filas = ta.com(_diag, 5)
print(f"EntryID del Inbox (_inbox) : {eid_inbox[:40]}...")
print()
for f in filas:
    print(f"asunto    : {f['asunto']!r}")
    print(f"  entry   : {f['entry'][:40]}...")
    print(f"  parent  : {str(f['parent'])[:40]}...  name={f['parent_name']!r}")
    print(f"  parent == _inbox.EntryID : {f['parent_ok']}")
    print(f"  _in_inbox(entry)         : {f['in_inbox']}   <-- esto decide si la tarjeta se cancela")
    print()
print("FIN")

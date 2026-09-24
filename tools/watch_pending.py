"""SOLO LECTURA. Vigila pending.json cada 2 s y, en cuanto aparece una tarjeta,
dispara la misma comprobacion que hace la reconciliacion del bridge, para ver por que
la da por muerta. Tambien comprueba si el entry_id pendiente sigue estando entre los
correos que devuelve _list_inbox.

No modifica nada: solo informa."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402


def _probe(entry_id, otros):
    out = {}
    try:
        it = ta._ns.GetItemFromID(entry_id)
        out["getitem"] = "ok"
        out["asunto"] = (it.Subject or "")[:50]
        out["parent_name"] = it.Parent.Name
        out["parent_eid"] = it.Parent.EntryID
        out["inbox_eid"] = ta._inbox.EntryID
        out["parent_igual_inbox"] = (it.Parent.EntryID == ta._inbox.EntryID)
    except Exception as ex:
        out["getitem"] = f"ERROR {ex.__class__.__name__}: {ex}"
    out["in_inbox"] = ta._in_inbox(entry_id)
    out["esta_en_list_inbox"] = entry_id in otros
    out["entry_id_len"] = len(entry_id)
    return out


def _listar_inbox(n=10):
    out = []
    i = 0
    for it in ta._inbox.Items:
        if i >= n:
            break
        try:
            if it.Class != ta.OL_MAIL:
                continue
        except Exception:
            continue
        i += 1
        out.append(it.EntryID)
    return out


print(f"vigilando pending.json durante 240 s...  ({time.strftime('%H:%M:%S')})")
fin = time.time() + 240
vistos = set()
while time.time() < fin:
    pend = ta.com(ta.load_pending)
    if pend:
        otros = ta.com(_listar_inbox, 10)
        for cid, m in pend.items():
            if cid in vistos:
                continue
            vistos.add(cid)
            eid = m.get("entry_id")
            info = ta.com(_probe, eid, otros)
            print()
            print(f"### TARJETA DETECTADA {time.strftime('%H:%M:%S')}")
            print(f"  cid                = {cid}")
            print(f"  from               = {m.get('from')}")
            print(f"  entry_id completo  = {eid}")
            for k, v in info.items():
                print(f"  {k:19s}= {v}")
        if vistos:
            break
    time.sleep(2)

if not vistos:
    print("no aparecio ninguna tarjeta en la ventana de vigilancia")
print("FIN")

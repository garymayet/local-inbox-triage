"""Solo lectura: comprueba que TODAS las carpetas destino se resuelven por ruta,
incluida la que tiene '/' en el nombre ('KT AWS/Azure'), que antes fallaba en
silencio (el bootstrap la daba por vacia y mover ahi no funcionaba).

OJO (aprendido a golpes): los objetos COM de Outlook que devuelve el agente viven en
su hilo del pool. Tocar .Name/.FolderPath desde el hilo principal lanza
'The application called an interface that was marshalled for a different thread'.
Por eso TODO acceso COM va dentro de una funcion que se ejecuta con ta.com(...).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import triage_agent as ta  # noqa: E402

OBJETIVO = "Inbox/CloudOps/VALE/KT AWS/Azure"


def _info(path):
    f = ta._find_by_path(path)
    if f is None:
        return None
    return {"name": f.Name, "folderpath": f.FolderPath}


def _sin_resolver(dests):
    return [p for _, p in dests if ta._find_by_path(p) is None]


info = ta.com(_info, OBJETIVO)
print(f"objetivo con '/' en el nombre : {OBJETIVO}")
print(f"  resuelve = {info is not None}")
if info:
    print(f"  Name                       = {info['name']!r}")
    print(f"  FolderPath real de Outlook = {info['folderpath']!r}")

dests = ta.com(ta._dest_folders)
malas = ta.com(_sin_resolver, dests)
print(f"carpetas destino = {len(dests)}   sin resolver = {len(malas)}")
for m in malas:
    print("   SIN RESOLVER:", m)
print("FIN")

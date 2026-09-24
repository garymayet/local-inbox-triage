"""Parche IDEMPOTENTE: hace robusto _find_by_path ante carpetas cuyo NOMBRE contiene '/'.

Problema medido en la VM: existe la carpeta 'Inbox/CloudOps/VALE/KT AWS/Azure'. La
version original parte la ruta por '/' y busca segmento a segmento, asi que NUNCA la
resuelve y devuelve None EN SILENCIO. Consecuencias reales:
  - el bootstrap la da por vacia (0 correos, 0 vectores),
  - si en la tarjeta de Teams eliges esa carpeta, _move/_move_ret fallan.

Arreglo: en cada nivel, emparejar el nombre de carpeta MAS LARGO que encaje como
prefijo del resto de la ruta (asi 'KT AWS/Azure' se reconoce como un solo nombre).

Hace copia de seguridad triage_agent.py.bak-folderpath la primera vez y valida con
py_compile. Ejecutarlo dos veces no hace nada la segunda.
"""
import os
import py_compile
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(REPO, "triage_agent.py")
BAK = AGENT + ".bak-folderpath"
MARK = "PATCH-FOLDER-PATH"

NUEVA_LF = '''def _find_by_path(path):
    """Resuelve una carpeta por ruta completa tipo 'Inbox/Clientes/ClienteA'.
    Como arranca SIEMPRE en Inbox, es imposible mover un correo fuera del Inbox.

    PATCH-FOLDER-PATH: hay carpetas cuyo NOMBRE contiene '/', p.ej.
    'Inbox/CloudOps/VALE/KT AWS/Azure'. Partir la ruta por '/' y buscar segmento a
    segmento NUNCA las encuentra: devolvia None en silencio, el bootstrap las daba por
    vacias y mover ahi fallaba. En cada nivel se empareja el nombre MAS LARGO que encaje
    como prefijo del resto de la ruta."""
    parts = [p for p in path.split("/") if p]
    rest = "/".join(parts[1:]) if parts and parts[0].lower() == "inbox" else "/".join(parts)
    cur = _inbox
    for _ in range(12):
        if not rest:
            return cur
        mejor = None
        for f in cur.Folders:
            n = f.Name
            if rest.lower() == n.lower() or rest.lower().startswith(n.lower() + "/"):
                if mejor is None or len(n) > len(mejor.Name):
                    mejor = f
        if mejor is None:
            return None
        if rest.lower() == mejor.Name.lower():
            return mejor
        rest = rest[len(mejor.Name) + 1:]
        cur = mejor
    return None'''


def main():
    if not os.path.isfile(AGENT):
        print(f"ERROR: no existe {AGENT}")
        return 1
    with open(AGENT, encoding="utf-8", newline="") as fh:
        src = fh.read()

    if MARK in src:
        print("ya esta parcheado (idempotente, no toco nada):", AGENT)
        return 0

    try:
        i = src.index("def _find_by_path(path):")
        j = src.index("def _find_folder(name_lower):")
    except ValueError:
        print("ERROR: no encontre los anclajes _find_by_path / _find_folder.")
        print("       Revisa si el fichero ya fue modificado a mano.")
        return 1
    if j <= i:
        print("ERROR: anclajes en orden inesperado")
        return 1

    nl = "\r\n" if "\r\n" in src else "\n"
    nueva = NUEVA_LF.replace("\n", nl) + nl + nl

    shutil.copy2(AGENT, BAK)
    with open(AGENT, "w", encoding="utf-8", newline="") as fh:
        fh.write(src[:i] + nueva + src[j:])

    py_compile.compile(AGENT, doraise=True)
    print("parcheado OK:", AGENT)
    print("backup       :", BAK)
    print("validacion   : py_compile OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

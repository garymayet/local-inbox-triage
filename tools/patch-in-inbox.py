"""Parche IDEMPOTENTE: _in_inbox deja de confundir "no se pudo comprobar" con "no esta".

PROBLEMA MEDIDO EN LA VM (24-sep): el bridge creaba una tarjeta y, en el tick siguiente,
la reconciliacion la cancelaba con el mensaje "ya no esta en Inbox (accion manual)",
cuando el correo SI seguia en el Inbox (_in_inbox devolvia True desde un proceso aparte
y la tarjeta sobrevivia si no habia contencion de COM).

CAUSA: la version original era

    try:
        it = _ns.GetItemFromID(entry_id)
        return it.Parent.EntryID == _inbox.EntryID
    except Exception:
        return False

Cualquier excepcion TRANSITORIA de COM (Outlook ocupado, RPC_E_CALL_REJECTED, marshalling,
sincronizacion) devolvia False, y _bridge_tick interpreta False como "el usuario ya lo
movio a mano" -> borra el fichero de la tarjeta y libera el hueco EN SILENCIO. La tarjeta
desaparece de requests/ aunque el usuario la este viendo en Teams.

ARREGLO (dos lineas):
  1. _in_inbox devuelve None cuando NO PUDO comprobarlo (error), False solo si de verdad
     no esta en el Inbox.
  2. _bridge_tick solo reconcilia con `is False`, es decir, exige certeza.

Backup: triage_agent.py.bak-ininbox. Valida con py_compile. Idempotente.
"""
import os
import py_compile
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(REPO, "triage_agent.py")
BAK = AGENT + ".bak-ininbox"
MARK = "PATCH-IN-INBOX"

NUEVA_LF = '''def _in_inbox(entry_id):
    """True solo si el correo sigue en el Inbox (no movido/borrado a mano).

    PATCH-IN-INBOX: devuelve None si NO SE PUDO COMPROBAR (excepcion transitoria de COM:
    Outlook ocupado, RPC_E_CALL_REJECTED, marshalling, sincronizacion). Antes cualquier
    excepcion devolvia False y _bridge_tick lo leia como "el usuario ya lo movio", de modo
    que CANCELABA TARJETAS VIVAS en silencio. Distinguir los dos casos es obligatorio."""
    try:
        it = _ns.GetItemFromID(entry_id)
        return it.Parent.EntryID == _inbox.EntryID
    except Exception:
        return None'''

VIEJA_CALL = "        if not com(_in_inbox, m[\"entry_id\"]):"
NUEVA_CALL = ("        # PATCH-IN-INBOX: solo reconciliar con certeza (None = no se pudo comprobar)\n"
              "        if com(_in_inbox, m[\"entry_id\"]) is False:")


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
        i = src.index("def _in_inbox(entry_id):")
        j = src.index("def generate_draft(")
    except ValueError:
        print("ERROR: no encontre los anclajes _in_inbox / generate_draft")
        return 1
    if j <= i:
        print("ERROR: anclajes en orden inesperado")
        return 1

    if VIEJA_CALL not in src:
        print("ERROR: no encontre la llamada de reconciliacion:")
        print("      ", VIEJA_CALL)
        return 1

    nl = "\r\n" if "\r\n" in src else "\n"
    nueva_fn = NUEVA_LF.replace("\n", nl) + nl + nl

    out = src[:i] + nueva_fn + src[j:]
    out = out.replace(VIEJA_CALL, NUEVA_CALL.replace("\n", nl), 1)

    shutil.copy2(AGENT, BAK)
    with open(AGENT, "w", encoding="utf-8", newline="") as fh:
        fh.write(out)

    py_compile.compile(AGENT, doraise=True)
    print("parcheado OK:", AGENT)
    print("backup       :", BAK)
    print("validacion   : py_compile OK")
    print("cambios      : _in_inbox -> None en error; reconciliacion exige `is False`")
    return 0


if __name__ == "__main__":
    sys.exit(main())

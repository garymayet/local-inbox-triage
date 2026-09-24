"""Parche IDEMPOTENTE: guarda pending.json EN CUANTO se publica cada tarjeta.

PROBLEMA (medido en la VM, 24-sep-2026): en _bridge_tick, el ciclo que pide al LLM
clasificar hasta `batch` correos tarda **minutos por correo**. El fichero de la tarjeta se
escribe en `requests/` (y Power Automate la publica en Teams) al principio de cada vuelta,
pero `save_pending(pending)` se llamaba SOLO AL FINAL del ciclo completo.

Consecuencia real: hay una ventana de varios minutos en la que la tarjeta YA esta en Teams
pero el agente todavia no sabe que ese `cid` existe. Si el usuario pulsa un boton en esa
ventana:
  - llega la decision a `decisions/`,
  - `meta = pending.get(cid)` devuelve None,
  - el agente BORRA la decision y sigue como si nada.
Es decir: "he pulsado y no ha pasado nada", el peor fallo posible de cara al usuario.
Y si el bridge se reinicia dentro de esa ventana, la tarjeta queda huerfana para siempre.

ARREGLO: persistir pending inmediatamente despues de anadir cada entrada (y seguir guardando
al final, que no molesta). El coste es un json.dump por correo; el beneficio es que el cid
es durable desde el mismo instante en que la tarjeta es visible.

Backup: triage_agent.py.bak-pendingsave. Valida con py_compile. Idempotente.
"""
import os
import py_compile
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(REPO, "triage_agent.py")
BAK = AGENT + ".bak-pendingsave"
MARK = "PATCH-PENDING-SAVE"

VIEJO = ('                        "vec": vec, "suggested": dcn.get("folder"), "reqfile": reqname}\n'
         '        made += 1\n')

NUEVO = ('                        "vec": vec, "suggested": dcn.get("folder"), "reqfile": reqname}\n'
         '        # PATCH-PENDING-SAVE: guardar YA. Si el bridge muere (o el usuario responde)\n'
         '        # entre publicar la tarjeta y guardar pending.json, la tarjeta existe en Teams\n'
         '        # pero el agente no conoce el cid -> la respuesta se descartaria EN SILENCIO.\n'
         '        save_pending(pending)\n'
         '        made += 1\n')


def main():
    if not os.path.isfile(AGENT):
        print(f"ERROR: no existe {AGENT}")
        return 1
    with open(AGENT, encoding="utf-8", newline="") as fh:
        src = fh.read()

    if MARK in src:
        print("ya esta parcheado (idempotente, no toco nada):", AGENT)
        return 0

    if VIEJO not in src:
        print("ERROR: no encontre el bloque de creacion del pendiente. Buscado:")
        print(repr(VIEJO))
        return 1

    nl = "\r\n" if "\r\n" in src else "\n"
    nuevo = NUEVO.replace("\n", nl)

    shutil.copy2(AGENT, BAK)
    with open(AGENT, "w", encoding="utf-8", newline="") as fh:
        fh.write(src.replace(VIEJO, nuevo, 1))

    py_compile.compile(AGENT, doraise=True)
    print("parcheado OK:", AGENT)
    print("backup       :", BAK)
    print("validacion   : py_compile OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

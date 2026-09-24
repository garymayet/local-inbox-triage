"""Quita UNA carpeta del registro 'bootstrap_done.json' para que el proximo
`triage_agent.py --bootstrap` la vuelva a minar.

Para que sirve: el bootstrap es reanudable y marca cada carpeta como hecha al terminarla. Si
una carpeta se mino mal (p.ej. embeddings que fallaron por timeout mientras otro proceso
competia por la CPU: se observo 'Femsa: 30 correos, 20 vectores'), no hay forma de repetirla
sin quitar su entrada de aqui.

Se hace en PYTHON a proposito: el round-trip ConvertFrom-Json/ConvertTo-Json de PowerShell 5.1
puede ANIDAR el array y corromper el fichero (paso de verdad en esta VM y provoco un bootstrap
desde cero). Ademas deja copia de seguridad.

Uso:
    python tools/remine-folder.py "Inbox/CloudOps/Femsa"
    python tools/remine-folder.py --list
"""
import json
import os
import shutil
import sys

STATE = os.environ.get("TRIAGE_STATE", r"C:\Users\mmayet\.triage")
DONE = os.path.join(STATE, "bootstrap_done.json")


def main():
    if not os.path.isfile(DONE):
        print(f"no existe {DONE} (¿bootstrap sin ejecutar?)")
        return 1
    with open(DONE, encoding="utf-8-sig") as fh:
        hechas = json.load(fh)
    if not isinstance(hechas, list):
        print(f"ERROR: {DONE} no es una lista (esta corrupto). Contenido: {type(hechas)}")
        return 1

    if len(sys.argv) < 2 or sys.argv[1] == "--list":
        print(f"{len(hechas)} carpetas marcadas como hechas:")
        for h in hechas:
            print("   ", h)
        return 0

    objetivo = sys.argv[1]
    if objetivo not in hechas:
        print(f"'{objetivo}' no estaba en la lista (nada que hacer)")
        return 0

    shutil.copy2(DONE, DONE + ".bak-remine")
    quedan = [h for h in hechas if h != objetivo]
    with open(DONE, "w", encoding="utf-8") as fh:
        json.dump(quedan, fh)

    # Verificacion: releer y confirmar que sigue siendo una lista plana
    with open(DONE, encoding="utf-8") as fh:
        comp = json.load(fh)
    ok = isinstance(comp, list) and objetivo not in comp and len(comp) == len(hechas) - 1
    print(f"quitada: {objetivo}")
    print(f"carpetas: {len(hechas)} -> {len(comp)}   verificacion={'OK' if ok else 'FALLO'}")
    print("ahora ejecuta:  triage_agent.py --bootstrap --per-folder 30")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

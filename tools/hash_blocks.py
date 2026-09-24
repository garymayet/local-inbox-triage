"""Imprime el sha256 de cada bloque de N lineas de un fichero.

Sirve para comparar dos copias del mismo fichero cuando no se puede transferir entero
(Run Command trunca la salida): si un bloque difiere, se ve exactamente cual.
Normaliza CRLF->LF para que el fin de linea no ensucie la comparacion.
"""
import hashlib
import sys

ruta = sys.argv[1]
bloque = int(sys.argv[2]) if len(sys.argv) > 2 else 100

with open(ruta, "rb") as fh:
    texto = fh.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
lineas = texto.split("\n")
print(f"{ruta}: {len(lineas)} lineas, bloques de {bloque}")
i = 0
while i < len(lineas):
    trozo = "\n".join(lineas[i:i + bloque])
    h = hashlib.sha256(trozo.encode("utf-8")).hexdigest()[:16]
    print(f"  {i + 1:5d}-{min(i + bloque, len(lineas)):5d}  {h}")
    i += bloque

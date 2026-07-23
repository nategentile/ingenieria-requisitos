#!/usr/bin/env python3
"""Visor local de los planos (ingeniería de requisitos).

Sirve la plantilla fija (plantilla.html, junto a este script) y los datos del
proyecto (planos.json) en 127.0.0.1 y se apaga solo pasados N minutos (15
por defecto). Intenta siempre el puerto 8765: así relanzar conserva la URL y
la pestaña del usuario revive con recargar. Solo biblioteca estándar.

Uso:
    python3 servir.py --datos <ruta/planos.json> [--minutos 15] [--sin-navegador]
"""

import argparse
import http.server
import json
import os
import re
import sys
import threading
import time
import webbrowser
from urllib.parse import urlsplit

RUTA_ACTIVIDAD = re.compile(r"^/actividades/([a-z0-9][a-z0-9-]*)/(datos\.json|spec\.md|encargo\.md)$")

BASE = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = os.path.join(BASE, "plantilla.html")


def hacer_handler(ruta_datos, estado):
    class Visor(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            estado["ultimo"] = time.time()
            pedida = urlsplit(self.path).path
            if pedida in ("/", "/index.html"):
                self._fichero(PLANTILLA, "text/html; charset=utf-8")
            elif pedida == "/datos.json":
                # Se relee en cada petición: la página lo sondea sola.
                self._fichero(ruta_datos, "application/json; charset=utf-8")
            elif pedida in ("/spec.md", "/encargo.md"):
                # Documentos de salida, si ya existen junto a los datos.
                ruta = os.path.join(os.path.dirname(ruta_datos), pedida.lstrip("/"))
                if os.path.isfile(ruta):
                    self._fichero(ruta, "text/plain; charset=utf-8")
                else:
                    self.send_error(404, "Aún no se ha generado " + pedida.lstrip("/"))
            elif RUTA_ACTIVIDAD.match(pedida):
                # Planos y documentos de cada actividad (proyectos mapa).
                m = RUTA_ACTIVIDAD.match(pedida)
                nombre = "planos.json" if m.group(2) == "datos.json" else m.group(2)
                ruta = os.path.join(os.path.dirname(ruta_datos), "actividades", m.group(1), nombre)
                if os.path.isfile(ruta):
                    tipo = "application/json; charset=utf-8" if nombre.endswith(".json") else "text/plain; charset=utf-8"
                    self._fichero(ruta, tipo)
                else:
                    self.send_error(404, "Esta actividad aún no tiene " + nombre)
            else:
                self.send_error(404, "Este visor sirve /, /datos.json, /spec.md, /encargo.md y /actividades/<id>/...")

        def do_HEAD(self):
            estado["ultimo"] = time.time()
            self.send_response(200)
            self.end_headers()

        def _fichero(self, ruta, tipo):
            try:
                with open(ruta, "rb") as f:
                    cuerpo = f.read()
            except OSError:
                self.send_error(500, "No se pudo leer " + os.path.basename(ruta))
                return
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *args):
            pass

    return Visor


def main():
    p = argparse.ArgumentParser(description="Visor local de los planos")
    p.add_argument("--datos", required=True, help="Ruta al planos.json del proyecto")
    p.add_argument("--minutos", type=float, default=15, help="Minutos SIN actividad antes de apagarse (defecto: 15)")
    p.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador")
    args = p.parse_args()

    if not (0 < args.minutos <= 1440):
        sys.exit("--minutos debe estar entre 0 y 1440")

    ruta_datos = os.path.abspath(args.datos)
    if not os.path.isfile(ruta_datos):
        sys.exit("No existe el fichero de datos: " + ruta_datos)
    if not os.path.isfile(PLANTILLA):
        sys.exit("Falta la plantilla fija: " + PLANTILLA)
    try:
        with open(ruta_datos, "r", encoding="utf-8") as f:
            json.load(f)
    except (OSError, ValueError) as e:
        sys.exit("El fichero de datos no es JSON válido: " + str(e))

    # Puerto fijo 8765 para que relanzar conserve la URL; si está ocupado,
    # el sistema asigna uno libre.
    # En Windows hay que desactivar allow_reuse_address (SO_REUSEADDR, activo
    # por defecto en los servidores de http.server): ahí permite ligarse a un
    # puerto que otro proceso ya tiene abierto, así que un segundo visor no
    # recibiría el OSError esperado y le ROBARÍA el 8765 al primero (dos
    # sesiones en paralelo = solo se ve la última). Sin él, el segundo visor
    # cae al puerto aleatorio, que es el comportamiento buscado. En Linux y
    # macOS no aplica: un puerto en escucha no se puede robar.
    if os.name == "nt":
        http.server.ThreadingHTTPServer.allow_reuse_address = False
    estado = {"ultimo": time.time()}
    try:
        servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), hacer_handler(ruta_datos, estado))
    except OSError:
        servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), hacer_handler(ruta_datos, estado))
    puerto = servidor.server_address[1]
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()

    url = "http://127.0.0.1:%d/" % puerto
    print("Visor levantado: %s" % url, flush=True)
    print("Datos: %s (se releen al recargar la página)" % ruta_datos, flush=True)
    print("Se apaga solo tras %g minutos sin actividad (cada visita o recarga "
          "reinicia el contador)." % args.minutos, flush=True)
    if not args.sin_navegador:
        webbrowser.open(url)

    try:
        while True:
            restante = args.minutos * 60 - (time.time() - estado["ultimo"])
            if restante <= 0:
                break
            time.sleep(min(restante, 15))
    except KeyboardInterrupt:
        pass
    servidor.shutdown()
    print("Visor cerrado tras un rato sin uso. Para volver a verlo, lanza este comando otra vez.", flush=True)


if __name__ == "__main__":
    main()

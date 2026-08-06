#!/usr/bin/env python3
"""Bootstrap interno: monta el workspace completo desde unos planos.

``iniciar.py`` lo usa al principio para que la entrevista nazca ya dentro del
workspace. También puede convertir planos previos en un proyecto de trabajo entero,
siempre con la misma forma, listo para las fases siguientes del método
(investigación, planificación, especificación, construcción, cierre):

    <destino>/            meta-repo (git propio, con commit inicial)
      AGENTS.md           router de contexto y reglas (CLAUDE.md y GEMINI.md lo importan)
      README.md GUIA.md   el mapa del workspace y la guía sin tecnicismos
      setup.py            monta el workspace en otro ordenador (lee repos.yaml; idempotente)
      docs/00-metodo/     el método íntegro: 16 runbooks, 16 plantillas, sus scripts
                          roles.md, sus ADR (decisiones/) y el kit de requisitos/
      docs/               árbol congelado: 01-constitucion … 05-trabajo, bugs/,
                          conocimiento/, decisiones/
      .private/ .runtime/ lo sensible y lo generado (contenido fuera de git)
      main/               repo de código (git propio: clonado con --remoto, o creado de cero)
      worktrees/          una copia de trabajo por unidad (vacía al nacer)

Determinista: mismos planos + misma plantilla => mismos bytes (sin fechas generadas;
las fechas las pone git). Al final ejecuta el linter del método sobre lo creado: si
el linter falla, el bootstrap falla. Solo stdlib.

Uso:
  python3 RUTA_HERRAMIENTA/visor/bootstrap.py \
      --planos  CARPETA_PROYECTO            (la carpeta con planos.json) \
      --destino <carpeta nueva del workspace> \
      [--github <cuenta>]                   (crea los DOS repos privados en GitHub: <nombre>
                                             para el código y <nombre>-agents para el meta)
      [--remoto <url>] [--remoto-meta <url>] (remotos ya existentes, cualquier host git)
      [--compilar]                          (regenera especificaciones/ antes de montar)

Siempre son dos repos independientes. ``main/`` está ignorado por el meta-repo y
``setup.py`` lo clona o actualiza desde ``origin/main``: nunca queda fijado a un commit.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from json import load
from pathlib import Path

BASE = Path(__file__).resolve().parent          # visor/
PLANTILLA = BASE.parent / "plantilla"           # el molde del workspace

# Estados de actividad de la herramienta -> vocabulario cerrado del método.
ESTADOS = {
    "sin empezar": "por_descomponer",
    "en entrevista": "por_descomponer",
    "especificada": "especificada",
    "en obra": "en_obra",
    "entregada": "entregada",
}

# Enrutado tecnológico (vocabulario cerrado): el tipo de proyecto decide qué bias viaja.
# La entrevista y la lanzadera son UNIVERSALES; el bias no. Hoy solo `webapp` tiene
# receta completa; los demás tipos viajan con el bias genérico (la fase 3 decide el
# stack con investigación + ADR). Cuando un tipo gane su receta, se añade su fichero
# en plantilla/bias/ y se apunta aquí.
# El defecto es NEUTRO a propósito: si nadie dice el tipo, no se presupone web. Un proceso
# por lotes que naciera clasificado como `webapp` arrastraría Django + PostgreSQL + Compose
# a su bias, y la fase 3 investiga con el bias delante (runbooks/investigacion.md, paso 2):
# el defecto equivocado no se corrige después, envenena toda la investigación.
BIAS_POR_TIPO = {
    "webapp": "webapp.md",          # aplicación web de gestión (la receta completa)
    "automatizacion": "generico.md",  # pipelines, informes, procesos por lotes
    "agente": "generico.md",          # asistentes/chatbots/agentes como producto
    "otro": "generico.md",            # todo lo demás (la estructura vale igual)
}

IGNORAR = {"__pycache__", ".DS_Store"}

# El método se publica mediante una lista cerrada. Así un apunte local o un residuo
# histórico no puede colarse en todos los workspaces por estar dentro de la carpeta.
RUNBOOKS = ("adopcion", "auditoria", "bug", "cierre", "control-plane", "deploy", "directo", "documentacion",
            "expres", "feature", "hotfix", "investigacion", "migracion", "planificacion",
            "peticiones", "primer-despliegue", "refactor")
PLANTILLAS = ("agents-repo-codigo", "bug", "conocimiento", "decision", "despliegue", "directo",
              "especificacion", "hallazgos", "informe", "investigacion", "plano-operativo",
              "peticion-investigacion-informe", "peticion-investigacion-plan",
              "peticion-investigacion-sintesis", "roadmap", "sintesis")
SCRIPTS = ("caja_negra.py", "control_plane.py", "doctor.py", "ejecucion.py", "lint_ci.py",
           "lint_deploy.py", "lint_metodo.py", "lease.py", "peticion.py", "repo_config.py",
           "unidad.py", "workspace_paths.py")
DECISIONES = (
    "README.md",
    "001-docs-fuera-del-repo.md",
    "002-bootstrap-desde-ingenieria-requisitos.md",
    "003-entrevista-y-lanzadera-universales-bias-enrutado.md",
    "004-adopcion-brownfield-y-doctrina-de-tests.md",
    "005-fusion-especificacion-tareas.md",
    "006-bugs-como-fichero-vivo.md",
    "007-planos-canonicos-y-limites.md",
    "008-entrevistas-de-arranque-de-roles-operativos.md",
    "009-cierre-scriptado-y-entorno-comprobado.md",
    "010-estado-en-validacion-y-guardianes-que-miran.md",
    "011-salida-del-trabajo-probada-y-deploy-sin-stack.md",
    "012-entorno-minimo-por-defecto-y-tipo-no-presupuesto.md",
    "013-se-escribe-segun-se-hace.md",
    "014-carril-directo-y-puerta-de-flujos-por-delta.md",
    "015-plan-a-medida-y-arquitectura-en-el-bias.md",
    "016-nadie-espera-a-ciegas-y-el-coste-a-medida.md",
    "017-quien-construye-lo-dice-el-carril.md",
    "018-ci-real-nace-con-el-stack.md",
    "019-e2e-y-autorizacion-derivados-de-planos.md",
    "020-inbox-peticiones-e-investigacion-continua.md",
    "021-proceso-nativo-y-caja-negra-estructurada.md",
    "022-control-plane-de-ejecucion.md",
    "023-control-plane-concurrencia-local.md",
    "024-control-plane-de-seguridad-y-evidencia-causal.md",
    "025-el-trabajo-aparcado-no-bloquea-el-modo-d.md",
)
METODO_RAIZ = (
    "README.md", "VERSION", "roles.md", "comunicacion.md", "auditoria-calidad.md",
    "auditoria-metodo.md", "auditoria-seguridad.md", "seguridad-por-stack.md", "sandbox.md",
    "proceso-nativo.md",
)
ARCHIVOS_METODO = tuple(
    [*METODO_RAIZ]
    + [f"decisiones/{nombre}" for nombre in DECISIONES]
    + [f"runbooks/{nombre}.md" for nombre in RUNBOOKS]
    + [f"plantillas/{nombre}.md" for nombre in PLANTILLAS]
    + [f"scripts/{nombre}" for nombre in SCRIPTS]
)
ARCHIVOS_REQUISITOS = (
    "RUNBOOK.md", "servir.py", "validar.py", "validar_web.py", "generar_spec.py",
    "compilar.py", "finalizar.py", "requisitos.py", "revision.py", "plantilla.html",
    "esquema.json", "requirements-dev.txt",
)


def version_metodo():
    """Versión semver del método: la declara `plantilla/docs/00-metodo/VERSION`.

    Viaja al workspace en METODO.json (campo `version`, junto a formato y huella) para que
    actualizar.py pueda decir "método x.y.z → a.b.c" en vez de comparar solo huellas.
    """
    return (PLANTILLA / "docs" / "00-metodo" / "VERSION").read_text(encoding="utf-8").strip()


def esqueleto_congelado():
    """Esqueleto vacío de las carpetas del árbol congelado que nacen SIN contenido.

    {carpeta: {fichero: contenido}}. Fuente única compartida: el bootstrap lo usa al montar
    el workspace, y actualizar.py (Modo D) repone la carpeta entera cuando falta — un
    workspace anterior a docs/bugs/ no podía volver a verde porque Modo D tiene prohibido
    tocar bugs/, pero reponer el ESQUELETO vacío no es tocar contenido.
    """
    return {
        "docs/03-investigacion": {".gitkeep": ""},
        # 04-planificacion nace VACÍA, igual que 03-investigacion: el ROADMAP lo escribe la
        # fase 4 desde `plantillas/roadmap.md` (estructura fija). Un ROADMAP de relleno haría
        # fallar los tests e2e del método y mentiría sobre el estado del proyecto.
        "docs/04-planificacion": {".gitkeep": ""},
        "docs/decisiones": {".gitkeep": ""},
        # docs/bugs/: parte del árbol congelado que el linter EXIGE (ADR-006). Un bug es un
        # fichero vivo NNN-slug.md aquí, y su índice nace vacío.
        "docs/bugs": {".gitkeep": "", "INDICE.md": generar_indice_bugs()},
        "docs/05-trabajo/archivo": {".gitkeep": ""},
    }


def manifiesto_metodo():
    """La lista de ficheros del método tal como viaja en METODO.json.

    Es lo que lint_metodo.py recorre en cada arranque para que borrar o vaciar un guardián
    (runbooks, ADRs, los propios scripts) no pase jamás con 0 FAIL: sin esta lista dentro
    del workspace, el linter no tenía forma de saber qué debía existir.
    """
    return ["docs/00-metodo/" + relativo for relativo in ARCHIVOS_METODO]


def morir(msg):
    sys.exit(f"bootstrap: {msg}")


def git(destino, *args):
    r = subprocess.run(["git", *args], cwd=destino, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def copiar_arbol(origen, destino):
    shutil.copytree(origen, destino,
                    ignore=lambda d, nombres: [n for n in nombres if n in IGNORAR])


def comprobar_y_copiar_metodo(destino):
    """Copia solo el manifiesto público y falla si la plantilla contiene residuos."""
    origen = PLANTILLA / "docs" / "00-metodo"
    presentes = {
        str(ruta.relative_to(origen))
        for ruta in origen.rglob("*")
        if ruta.is_file() and not any(parte in IGNORAR for parte in ruta.parts)
    }
    esperados = set(ARCHIVOS_METODO)
    faltan = sorted(esperados - presentes)
    sobran = sorted(presentes - esperados)
    if faltan or sobran:
        partes = []
        if faltan:
            partes.append("faltan: " + ", ".join(faltan))
        if sobran:
            partes.append("no están en el manifiesto: " + ", ".join(sobran))
        morir("contenido de docs/00-metodo no publicable (" + "; ".join(partes) + ")")
    for relativo in ARCHIVOS_METODO:
        salida = destino / relativo
        salida.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen / relativo, salida)


def huella_plantilla():
    """Huella de contenido para orientar futuras auditorías hechas por el harness."""
    piezas = []
    for relativo in ARCHIVOS_METODO:
        piezas.append(("docs/00-metodo/" + relativo,
                       PLANTILLA / "docs" / "00-metodo" / relativo))
    for nombre in ARCHIVOS_REQUISITOS:
        origen = (BASE.parent / nombre if nombre in ("RUNBOOK.md", "requirements-dev.txt")
                  else BASE / nombre)
        piezas.append(("docs/00-metodo/requisitos/" + nombre, origen))
    for nombre in ("AGENTS.md", "setup.py"):
        piezas.append((nombre, PLANTILLA / nombre))
    for origen in sorted((PLANTILLA / "githooks").rglob("*")):
        if origen.is_file():
            piezas.append((".githooks/" + str(origen.relative_to(PLANTILLA / "githooks")),
                           origen))
    digest = hashlib.sha256()
    for nombre, ruta in sorted(piezas):
        digest.update(nombre.encode("utf-8") + b"\0" + ruta.read_bytes() + b"\0")
    digest.update(generar_ci().encode("utf-8"))
    return digest.hexdigest()


def preparar_documentos_de_planos(origen_planos, destino_planos):
    """Deja junto a cada planos.json su spec regenerada y un encargo explícito."""
    spec = destino_planos.parent / "spec.md"
    r = subprocess.run(
        [sys.executable, str(BASE / "generar_spec.py"), "--datos", str(destino_planos),
         "--salida", str(spec)],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        morir("no pude generar %s:\n%s%s" % (spec, r.stdout, r.stderr))
    encargo_origen = origen_planos.parent / "encargo.md"
    encargo_destino = destino_planos.parent / "encargo.md"
    if encargo_origen.is_file():
        shutil.copyfile(encargo_origen, encargo_destino)
        return
    datos = json.loads(destino_planos.read_text(encoding="utf-8"))
    encargo_destino.write_text(
        "# Encargo de definición — %s\n\n" % datos.get("titulo", "Actividad")
        + "Estos planos son el contrato de negocio aprobado. La siguiente sesión debe "
          "leer `planos.json` y `spec.md`, respetar sus límites y continuar por las fases "
          "pendientes del método: investigación, planificación y especificaciones de obra.\n\n"
        + "Este encargo no autoriza a programar directamente ni a saltarse la aprobación "
          "de cada unidad.\n",
        encoding="utf-8",
    )


def generar_readme(titulo, frase, url_meta, carpeta):
    """README del workspace desde `plantilla/README.md` (fuente única; aquí solo se rellena).

    El texto vive en la plantilla para que el workspace generado sea un espejo exacto del
    molde: si el método cambia, se edita allí y no hay que tocar Python.
    """
    plantilla = (PLANTILLA / "README.md").read_text(encoding="utf-8")
    return (plantilla
            .replace("{{TITULO}}", titulo)
            .replace("{{CITA}}", f"> {frase}\n\n" if frase else "")
            .replace("{{URL_META}}", url_meta or "<url-del-meta-repo>")
            .replace("{{CARPETA}}", carpeta))


# Los dos planos operativos (ADR-008) gobiernan los roles OBSERVABILIDAD y DEPLOY con un
# hard-gate, y hasta ahora simplemente NO EXISTÍAN al nacer el workspace: el agente se
# encontraba una puerta cerrada sin nada escrito al lado, y el usuario un fichero fantasma.
# Nacen, pues, como el hueco que se explica a sí mismo: quién lo llena, con qué preguntas y
# que se sobrescribe entero. No es relleno —no simula ninguna decisión y los gates lo siguen
# viendo en rojo, que es lo correcto— y por eso no cae en lo que prohíbe el ROADMAP de relleno.
PLANOS_OPERATIVOS = {
    "deploy": {
        "gate": "el rol DEPLOY no toca ninguna máquina",
        "preguntas": [
            "¿Dónde corre esto hoy, y dónde debería correr? (tu ordenador, un equipo de la "
            "oficina, internet)",
            "¿Qué etapas hay? (¿existe un sitio donde probar antes de que lo usen los demás, "
            "o solo hay uno y es el bueno?)",
            "¿Cómo se despliega ahora? (qué haces exactamente para que un cambio llegue a la "
            "gente)",
            "¿Hay copia de seguridad, dónde está, y se ha restaurado alguna vez de verdad?",
            "¿Quién da el OK antes de que algo llegue a producción? (nombre de persona)",
            "¿Qué pasa si hay que volver atrás? (cómo se deshace y cuánto se tarda)",
        ],
        "nota": "Si el usuario no ha desplegado NUNCA nada, estas preguntas no tienen "
                "respuesta y no se le puede pedir que decida a ciegas: se empieza por "
                "`docs/00-metodo/runbooks/primer-despliegue.md`, que pregunta por su negocio "
                "y deduce lo técnico.",
        "mientras": "`docs/00-metodo/scripts/lint_deploy.py` seguirá en rojo, y hace bien: "
                    "nadie ha decidido todavía cómo se despliega esto",
    },
    "observabilidad": {
        "gate": "el rol OBSERVABILIDAD no mira nada y no informa de nada",
        "preguntas": [
            "¿Qué se vigila hoy, y con qué? (¿hay algo que avise si la aplicación se cae?)",
            "¿Dónde están los logs? (si algo falla, ¿dónde se mira?)",
            "¿Dónde se ven los errores? (¿alguien se entera cuando un usuario ve una pantalla "
            "rota?)",
            "¿Qué significa para ti que «va bien»? (qué tiene que funcionar sí o sí, y a qué "
            "hora del día importa más)",
            "¿A quién se avisa cuando algo se rompe, y cómo? (persona, canal, y si es de noche)",
            "¿En qué etapa está el proyecto: local, red local (LAN) o internet (VPS)?",
        ],
        "nota": "Lo que el usuario no sepa pero exista, el rol lo DERIVA con evidencia y se lo "
                "confirma. Lo que NO exista no lo construye: es un hallazgo, y va a la sección "
                "«Lo que NO existe todavía» del plano.",
        "mientras": "cualquier informe de estado sería una opinión: sin saber qué se vigila y "
                    "qué significa «va bien» para el usuario, no hay nada contra lo que "
                    "comparar",
    },
}


def generar_plano_pendiente(rol):
    """docs/conocimiento/plano-<rol>.md: el hueco que dice cómo se llena (ADR-008)."""
    ficha = PLANOS_OPERATIVOS[rol]
    preguntas = "\n".join(f"   {i}. {p}" for i, p in enumerate(ficha["preguntas"], 1))
    return f"""---
rol: {rol}
actualizado: PENDIENTE
---

# Plano operativo · {rol.upper()} — SIN ENTREVISTAR

> **Este fichero está vacío a propósito y no se rellena de memoria ni "a ojo".** Es la salida
> escrita de la entrevista de arranque del rol (ADR-008), y mientras siga así:
> `<HARD-GATE>` **{ficha['gate']}**.

## Cómo se llena (una vez, y ya)

1. Sesión nueva con el rol {rol.upper()}. Un rol = una sesión: no se mezcla con la de
   construir.
2. Preguntar al usuario, en su idioma y **una por una** (ficha del rol en
   `docs/00-metodo/roles.md`):

{preguntas}

3. **Sobrescribir este fichero ENTERO** desde
   `docs/00-metodo/plantillas/plano-operativo.md` (`rol: {rol}`), con las respuestas en
   palabras del usuario. De este texto no se conserva nada: no es una plantilla que rellenar
   hueco a hueco, es un cartel de "aquí todavía no hay nada".

{ficha['nota']}

Mientras tanto, {ficha['mientras']}.
"""


def generar_indice_bugs():
    """docs/bugs/INDICE.md: el índice vacío de bugs (ADR-006). El árbol congelado lo exige."""
    return (
        "# Índice de bugs\n"
        "\n"
        "> Una línea por bug. Lo escribe SOLO el padre: al reportar (alta con severidad y triaje) y\n"
        "> al cerrar (estado final). El detalle vive en la ficha `NNN-slug.md` de cada bug.\n"
        "\n"
        "| NNN | ficha | severidad | triaje | estado |\n"
        "|---|---|---|---|---|\n"
    )


def generar_repos_yaml(nombre, remoto, rama_principal="main"):
    recrear = ("# Re-crear este workspace en otra máquina:\n"
               "#   git clone <remoto-del-meta>\n"
               "#   python3 setup.py\n")
    return (
        "# Referencia al repo de código que este meta-repo orquesta (patrón meta-repo/repos.yaml).\n"
        "# main/ es su clon canónico (solo pull); worktrees/NNN-slug/ sus copias de trabajo por unidad.\n"
        "\n"
        "codigo:\n"
        f"  nombre: {nombre}\n"
        f"  remoto: {remoto}\n"
        f"  rama_principal: {rama_principal}\n"
        "  ruta_local: main/\n"
        "  # baseline_sha: <sha>   # solo en repos ADOPTADOS: el detector de drift solo\n"
        "  #                         cuenta commits POSTERIORES a este (lo escribe la adopción).\n"
        "\n"
        f"{recrear}"
        "# Crear worktree de una unidad (desde main/):\n"
        f"#   git -C main worktree add ../worktrees/NNN-slug -b NNN-slug origin/{rama_principal}\n"
        "# Borrarlo en el cierre:\n"
        "#   git -C main worktree remove ../worktrees/NNN-slug && git -C main branch -d NNN-slug\n"
    )


def generar_estado(remoto_pendiente):
    pendientes = ["Fase 3 (investigación): `03-investigacion/SINTESIS.md` por escribir, "
                  "acotada por `01-constitucion/bias.md`.",
                  "Fase 4 (planificación): `04-planificacion/ROADMAP.md` por escribir "
                  "(esqueleto andante primero)."]
    if remoto_pendiente:
        pendientes.append("Remotos git por crear y conectar (meta y código); ver `repos.yaml`.")
    lista = "\n".join(f"{i}. {p}" for i, p in enumerate(pendientes, 1))
    return (
        "# ESTADO — dónde estamos (≤100 líneas, regenerable, solo lo escribe el padre)\n"
        "\n"
        "## Posición actual\n"
        "\n"
        "- **Fase del proyecto**: workspace recién montado por el bootstrap. Fases 1-2 con\n"
        "  contenido (constitución + mapa de actividades); 3-4 sin arrancar.\n"
        "- **Última actividad**: bootstrap del workspace (fecha: ver git log).\n"
        "\n"
        "## Unidades\n"
        "\n"
        "| NNN | slug | tipo | carril | estado |\n"
        "|---|---|---|---|---|\n"
        "| — | (ninguna abierta todavía) | | | |\n"
        "\n"
        "## Bloqueos y pendientes de arranque\n"
        "\n"
        f"{lista}\n"
        "\n"
        "## Continuidad de sesión\n"
        "\n"
        "- Para retomar: leer este fichero + `AGENTS.md`.\n"
        "- El mapa del negocio: `02-flujos/INDICE.md`; el detalle de cada actividad, en su fichero.\n"
    )


def generar_guia(titulo):
    return (
        f"# GUÍA — así funciona «{titulo}» (sin tecnicismos)\n"
        "\n"
        "La entrevista terminó y este es tu taller, ya montado. No necesitas saber\n"
        "programar: tú diriges, los agentes construyen, y este sistema los mantiene a raya.\n"
        "\n"
        "## Qué hay en esta carpeta\n"
        "\n"
        "- `docs/` — el pensamiento del proyecto: qué es, cómo funciona tu negocio (lo que\n"
        "  contaste en la entrevista), los arreglos pendientes (`docs/bugs/`) y el trabajo en\n"
        "  marcha (`docs/05-trabajo/ESTADO.md` te dice dónde estás).\n"
        "- `main/` — el código de tu aplicación. No se toca a mano: para eso están los agentes.\n"
        "- `worktrees/` — las mesas de trabajo temporales donde construyen los agentes.\n"
        "- `.private/` — tus secretos y contraseñas. Nunca sale de tu ordenador.\n"
        "- `setup.py` — si te llevas esto a otro ordenador, lo ejecutas y se monta solo.\n"
        "\n"
        "## Tú solo haces 3 cosas\n"
        "\n"
        "1. **Pides**: abre tu agente en esta carpeta y cuéntale qué quieres (\"quiero que se\n"
        "   pueda…\"). Él escribe el contrato EN TU IDIOMA.\n"
        "2. **Apruebas leyendo**: te enseña el contrato — qué pasará cuando esté hecho, con\n"
        "   ejemplos de tu negocio. Lo corriges y das el OK. Sin tu OK no se construye.\n"
        "3. **Pruebas usando**: cuando digan \"hecho\", abres tu aplicación y compruebas con tus\n"
        "   ejemplos reales. ¿Sí? Siguiente. ¿No? Di \"esto no es lo que pedí\" y se convierte\n"
        "   en un arreglo con su prueba.\n"
        "\n"
        "## Qué pasa por detrás\n"
        "\n"
        "Cada petición tuya se convierte en: contrato aprobado por ti → un agente constructor\n"
        "trabaja en una mesa aparte escribiendo PRIMERO las pruebas que deben cumplirse →\n"
        "otro agente distinto revisa el trabajo → las pruebas corren solas (en GitHub si el\n"
        "proyecto tiene CI; los creados de cero la traen, los adoptados la ganan en su\n"
        "roadmap) → solo con todo en verde se incorpora al código → la mesa se recoge sola.\n"
        "Nada entra sin\n"
        "pruebas y sin revisión. Tu palabra final siempre es USAR la aplicación.\n"
        "\n"
        "## Los seguros que te protegen\n"
        "\n"
        "- Los contratos que juzgan el trabajo viven FUERA del alcance de quien construye.\n"
        "- Nadie construye sin contrato escrito (hay un guardián automático).\n"
        "- Cada error arreglado deja una prueba permanente: ese error no vuelve.\n"
        "- Producción y servicios externos: solo lectura; los cambios de verdad te los piden.\n"
        "- Secretos y datos sensibles: en `.private/`, que jamás sale de tu ordenador.\n"
        "\n"
        "## Si tu negocio cambia\n"
        "\n"
        "Vuelve a la herramienta de la entrevista (modo iteración): tus planos viven en\n"
        "`docs/02-flujos/planos/`. De ahí se regenera la documentación — nunca a mano.\n"
    )


def generar_ci():
    return (
        "name: metodo\n"
        "on:\n"
        "  push: { branches: [main] }\n"
        "  pull_request:\n"
        "jobs:\n"
        "  lint:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: |\n"
        "          git config --global user.name \"CI\"\n"
        "          git config --global user.email \"ci@localhost\"\n"
        "      - run: python3 docs/00-metodo/scripts/lint_metodo.py\n"
    )


def generar_indice(planos, detalles):
    """INDICE.md desde el bloque `actividades` del mapa. `detalles`: id -> fichero copiado."""
    lineas = [
        "# Índice de actividades — el mapa del negocio",
        "",
        "> Una línea por actividad, con su estado. Estados: `por_descomponer → especificada → "
        "en_obra → entregada`.",
        "> Solo lo escribe el padre (ritual de cierre). El detalle de cada actividad, en su "
        "propio fichero.",
        "",
        "Generado por el bootstrap desde los planos de la entrevista; después lo mantiene el "
        "padre. La fuente",
        "de verdad vive en `planos/` (el mapa y el planos.json de cada actividad): un cambio "
        "de negocio se itera",
        "ahí con la herramienta de entrevista (modo C), se recompila y se vuelca aquí en un "
        "cierre. Estos .md no",
        "se editan a mano.",
        "",
    ]
    actividades = planos.get("actividades", [])
    if not actividades:  # proyecto de una sola actividad (o planos mínimos/inferidos)
        actividades = [{
            "id": next(iter(detalles), "actividad-unica"),
            "nombre": planos.get("titulo", "Actividad única"),
            "area": "General",
            # Solo está "especificada" si existe su documento compilado; si no, honestidad.
            "estado": "especificada" if detalles else "sin empezar",
        }]
    areas = []
    for x in actividades:
        if x["area"] not in areas:
            areas.append(x["area"])
    for area in areas:
        lineas += [f"## {area}", "", "| Actividad | Estado | Detalle | Depende de |", "|---|---|---|---|"]
        for x in [y for y in actividades if y["area"] == area]:
            estado = ESTADOS.get(x.get("estado", "sin empezar"))
            if estado is None:
                morir(f"estado de actividad fuera de vocabulario: '{x.get('estado')}' ({x['id']})")
            detalle = f"[{x['id']}.md]({x['id']}.md)" if x["id"] in detalles else "(sin planos aún)"
            depende = ", ".join(x.get("depende_de", [])) or "—"
            lineas.append(f"| {x['nombre']} (`{x['id']}`) | {estado} | {detalle} | {depende} |")
        lineas.append("")
    return "\n".join(lineas) + "\n"


def remoto_esta_vacio(url):
    rc, out = git(Path.cwd(), "ls-remote", url)
    if rc != 0:
        morir(f"no puedo acceder al remoto {url}:\n{out}")
    return not out.strip()


def rama_por_defecto(url):
    """La rama principal REAL del remoto, preguntándosela a él.

    Un repo que ya existía puede llamarla `master`, `develop` o cualquier cosa. Dar por hecho
    `main` mataba la adopción de código existente en el primer comando del Modo B: el clon
    fallaba y el mensaje hablaba de una rama que en ese repo no existe.
    """
    rc, out = git(Path.cwd(), "ls-remote", "--symref", url, "HEAD")
    if rc == 0:
        m = re.search(r"^ref:\s+refs/heads/(\S+)\s+HEAD", out, flags=re.M)
        if m:
            return m.group(1)
    return "main"


def remoto_tiene_codigo(url):
    """Distingue un repo con implementación de uno que solo tiene README/CI de arranque."""
    if remoto_esta_vacio(url):
        return False
    with tempfile.TemporaryDirectory(prefix="ingenieria-requisitos-detectar-") as temporal:
        # Sin `--branch`: se clona la rama por defecto que diga el remoto, sea cual sea.
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--no-checkout", url, temporal],
            capture_output=True,
            text=True,
        )
        if r.returncode:
            morir(
                f"no puedo inspeccionar el contenido de {url}:\n"
                f"{r.stdout}{r.stderr}"
            )
        listado = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=temporal,
            capture_output=True,
            text=True,
        )
        if listado.returncode:
            morir(f"no puedo leer el contenido inicial de {url}:\n{listado.stderr}")
        soporte = {
            "readme", "readme.md", "readme.txt", "license", "license.md",
            "copying", ".gitignore", ".gitattributes",
        }
        for ruta in listado.stdout.splitlines():
            normalizada = ruta.strip().lower()
            if not normalizada or normalizada in soporte:
                continue
            if normalizada.startswith(".github/"):
                continue
            return True
        return False


def crear_repos_github(owner, nombre_codigo, nombre_meta):
    """Crea los dos repos privados en GitHub con gh. Devuelve (url_codigo, url_meta)."""
    if not shutil.which("gh"):
        morir("para --github necesito el programa `gh` (GitHub CLI) instalado y con sesión "
              "iniciada (`gh auth login`). Sin GitHub, ejecútame sin --github: todo funciona "
              "igual en local y los remotos se pueden conectar más tarde.")
    urls = []
    for nombre in (nombre_codigo, nombre_meta):
        r = subprocess.run(["gh", "repo", "create", f"{owner}/{nombre}", "--private"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            morir(f"gh no pudo crear {owner}/{nombre}:\n{r.stdout}{r.stderr}")
        # Las ramas mueren solas al mergear su PR.
        subprocess.run(["gh", "repo", "edit", f"{owner}/{nombre}", "--delete-branch-on-merge"],
                       capture_output=True, text=True)
        urls.append(f"https://github.com/{owner}/{nombre}.git")
    return urls[0], urls[1]


def proteger_main(owner, nombre):
    """Intenta proteger main del repo de código (solo PR, checks en verde). Devuelve aviso o None."""
    cuerpo = json.dumps({
        "required_status_checks": {
            "strict": True,
            "contexts": ["tests", "quality-security"],
        },
        "enforce_admins": False,
        "required_pull_request_reviews": None,
        "restrictions": None,
    })
    r = subprocess.run(["gh", "api", "--method", "PUT",
                        f"repos/{owner}/{nombre}/branches/main/protection", "--input", "-"],
                       input=cuerpo, capture_output=True, text=True)
    if r.returncode != 0:
        return ("no pude proteger main (en repos PRIVADOS del plan gratis GitHub lo capa). "
                "El método protege igual: todo pasa por PR con la CI en verde — pero un push "
                "directo a main no está bloqueado por GitHub. Opciones: GitHub Pro o repo público.")
    return None


def montar_git(destino, nombre_codigo, titulo, remoto_codigo, remoto_meta):
    """git init + commit del meta y repo de código independiente dentro de main/.

    - Sin remotos: dos repos locales (el de código, creado de cero en main/).
    - Con remoto de código: main/ se clona (si tiene contenido) o se crea y empuja (si
      está vacío).
    - Con remoto del meta: el padre se publica sin main/, que setup.py recupera siempre
      desde origin/main.
    """
    avisos = []
    rc, out = git(destino, "init", "-b", "main")
    if rc != 0:
        morir(f"git init falló: {out}")
    git(destino, "add", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "README.md", "GUIA.md",
        "METODO.json",
        "setup.py", "repos.yaml", ".gitignore", "docs", "worktrees", ".github",
        ".githooks", ".private/README.md", ".runtime/README.md")
    rc, out = git(destino, "commit", "-m",
                  f"Workspace inicial ({titulo}) — bootstrap de ingenieria-requisitos")
    if rc != 0:
        avisos.append(f"no pude hacer el commit inicial del meta-repo ({out}); "
                      "hazlo a mano cuando configures tu identidad de git")

    # --- El repo de código, en main/ ---
    main_dir = destino / "main"
    if remoto_codigo and not remoto_esta_vacio(remoto_codigo):
        rc, out = git(destino, "clone", remoto_codigo, "main")   # su rama por defecto, no `main`
        if rc != 0:
            morir(f"no pude clonar el repo de código ({remoto_codigo}):\n{out}")
    else:
        main_dir.mkdir()
        rc, out = git(main_dir, "init", "-b", "main")
        if rc != 0:
            morir(f"git init del repo de código falló: {out}")
        # El repo de código nace LIMPIO: sin stack no hay suite, linter ni auditor de
        # dependencias reales. La primera unidad del esqueleto los materializa juntos.
        (main_dir / "README.md").write_text(
            f"# {nombre_codigo}\n\nRepo de código de «{titulo}». Se orquesta desde su "
            "meta-repo (carpeta padre; ver `repos.yaml` allí).\n", encoding="utf-8")
        # Mínimo y AGNÓSTICO del lenguaje: solo secretos y ruido. Los ignores del stack
        # (entorno virtual, node_modules, build) los añade el esqueleto andante, que es quien
        # sabe cuál es el stack. Sin esto, el `.env` que el método exige tener en el repo de
        # código entra en el PRIMER commit y se queda en el historial para siempre.
        (main_dir / ".gitignore").write_text(
            "# Secretos: NUNCA en git (el método los quiere en .env, fuera del repo).\n"
            ".env\n.env.*\n!.env.example\n*.pem\n*.key\n\n"
            "# Ruido del sistema.\n.DS_Store\nThumbs.db\n\n"
            "# Lo generado en local (logs, capturas, dumps).\n.runtime/\n", encoding="utf-8")
        git(main_dir, "add", "README.md", ".gitignore")
        rc, out = git(main_dir, "commit", "-m",
                      "Inicial: README + .gitignore (establece main)")
        if rc != 0:
            avisos.append(f"no pude hacer el commit inicial de main/ ({out})")
        if remoto_codigo:
            git(main_dir, "remote", "add", "origin", remoto_codigo)
            rc, out = git(main_dir, "push", "-u", "origin", "main")
            if rc != 0:
                avisos.append(f"no pude empujar main/ a {remoto_codigo} ({out})")

    # El hook agnóstico se activa en main/ (los worktrees heredan la config del repo).
    git(main_dir if main_dir.exists() else destino / "main", "config", "core.hooksPath",
        str((destino / ".githooks").resolve()))

    if remoto_meta:
        git(destino, "remote", "add", "origin", remoto_meta)
    if remoto_meta:
        rc, out = git(destino, "push", "-u", "origin", "main")
        if rc != 0:
            avisos.append(f"no pude empujar el meta-repo a {remoto_meta} ({out})")
    return avisos


def main():
    ap = argparse.ArgumentParser(description="Monta el workspace completo desde los planos.")
    ap.add_argument("--planos", required=True,
                    help="carpeta del proyecto de la entrevista (contiene planos.json)")
    ap.add_argument("--destino", required=True, help="carpeta nueva del workspace (no debe existir)")
    ap.add_argument("--tipo", choices=sorted(BIAS_POR_TIPO), default="otro",
                    help="tipo de proyecto (decide el bias tecnológico que viaja). Sin --tipo "
                         "viaja el bias NEUTRO y el stack lo decide la fase 3 con "
                         "investigación y ADR: nunca se presupone que esto es una web")
    ap.add_argument("--remoto", help="URL del repo de código (existente o vacío)")
    ap.add_argument("--remoto-meta", help="URL del repo del meta (vacío)")
    ap.add_argument("--github", metavar="CUENTA",
                    help="crea los DOS repos privados en esa cuenta de GitHub (necesita gh con sesión)")
    ap.add_argument("--compilar", action="store_true",
                    help="regenerar especificaciones/ desde los planos antes de montar")
    args = ap.parse_args()

    proyecto = Path(args.planos).resolve()
    destino = Path(args.destino).resolve()
    mapa = proyecto / "planos.json"
    if not mapa.is_file():
        morir(f"no encuentro {mapa}")
    if destino.exists() and any(destino.iterdir()):
        morir(f"el destino ya existe y no está vacío: {destino} (el bootstrap solo crea workspaces nuevos)")
    if destino.exists():
        destino.rmdir()  # carpeta vacía pre-creada: el copytree la necesita inexistente
    piezas = ["bias/webapp.md", "bias/generico.md", "bias/brownfield.md",
              "AGENTS.md", "README.md", "gitignore", "setup.py", "worktrees-README.md"]
    for pieza in piezas:
        if not (PLANTILLA / pieza).exists():
            morir(f"plantilla incompleta: falta {pieza} en {PLANTILLA}")

    if args.compilar:
        r = subprocess.run([sys.executable, str(BASE / "compilar.py"), "--mapa", str(mapa)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            morir(f"compilar.py falló:\n{r.stdout}{r.stderr}")

    especificaciones = proyecto / "especificaciones"
    constitucion = especificaciones / "01-constitution" / "constitution.md"
    flows = especificaciones / "02-flows"
    if not constitucion.is_file():
        morir(f"no encuentro {constitucion} (pásame --compilar, o corre visor/compilar.py)")

    with open(mapa, encoding="utf-8") as f:
        planos = load(f)
    titulo = planos.get("titulo", destino.name)
    frase = (planos.get("contrato") or {}).get("frase", "")

    # Namespace de los dos repos: el de código lleva el nombre del proyecto; el meta,
    # el mismo nombre con sufijo -agents.
    nombre_codigo = destino.name.removesuffix("-agents")
    nombre_meta = destino.name if destino.name.endswith("-agents") else f"{nombre_codigo}-agents"
    if args.github:
        remoto_codigo, remoto_meta = crear_repos_github(args.github, nombre_codigo, nombre_meta)
    else:
        remoto_codigo, remoto_meta = args.remoto, args.remoto_meta
    remoto_pendiente = not remoto_codigo
    remoto = remoto_codigo or "PENDIENTE  # crear el remoto del repo de código y poner aquí su URL"

    # Brownfield: si el repo de código YA tiene contenido, el stack ya está elegido —
    # viaja el bias brownfield (adopción obligatoria como primera unidad), sea cual sea --tipo.
    # La rama principal se le PREGUNTA al remoto: un repo adoptado puede ser `master`.
    rama_codigo = rama_por_defecto(remoto_codigo) if remoto_codigo else "main"
    brownfield = bool(remoto_codigo) and remoto_tiene_codigo(remoto_codigo)
    fichero_bias = "brownfield.md" if brownfield else BIAS_POR_TIPO[args.tipo]

    # --- El árbol congelado ---
    docs = destino / "docs"
    (docs / "02-flujos").mkdir(parents=True)
    comprobar_y_copiar_metodo(docs / "00-metodo")
    # El workspace se lleva también el rol y las herramientas de requisitos:
    # podrá añadir o alterar flujos en el futuro sin depender de esta carpeta.
    requisitos = docs / "00-metodo" / "requisitos"
    requisitos.mkdir()
    shutil.copyfile(BASE.parent / "RUNBOOK.md", requisitos / "RUNBOOK.md")
    for nombre in (
        "servir.py", "validar.py", "validar_web.py", "generar_spec.py",
        "compilar.py", "finalizar.py", "requisitos.py", "revision.py",
        "plantilla.html", "esquema.json",
    ):
        shutil.copyfile(BASE / nombre, requisitos / nombre)
    shutil.copyfile(BASE.parent / "requirements-dev.txt",
                    requisitos / "requirements-dev.txt")
    (docs / "01-constitucion").mkdir()
    shutil.copyfile(constitucion, docs / "01-constitucion" / "manifiesto.md")
    shutil.copyfile(PLANTILLA / "bias" / fichero_bias,
                    docs / "01-constitucion" / "bias.md")
    # conocimiento/ ya no nace del todo vacía: los dos planos operativos nacen como el hueco
    # que explica quién lo llena y con qué preguntas (ver PLANOS_OPERATIVOS).
    (docs / "conocimiento").mkdir()
    for rol in PLANOS_OPERATIVOS:
        (docs / "conocimiento" / f"plano-{rol}.md").write_text(
            generar_plano_pendiente(rol), encoding="utf-8")
    # Las carpetas del árbol congelado que nacen vacías, cada una con su esqueleto (fuente
    # única compartida con actualizar.py; el porqué de cada una, en esqueleto_congelado).
    for carpeta, ficheros in esqueleto_congelado().items():
        (destino / carpeta).mkdir(parents=True, exist_ok=True)
        for nombre, contenido in sorted(ficheros.items()):
            (destino / carpeta / nombre).write_text(contenido, encoding="utf-8")
    # Workspaces nuevos nacen estrictos: el almacén existe, pero no LEGACY.json.
    (docs / "05-trabajo" / "peticiones").mkdir()

    # 02-flujos: un doc por actividad con planos (aplanado: el id es único en el mapa).
    detalles = {}
    for doc in sorted(flows.rglob("*.md")) if flows.is_dir() else []:
        if doc.stem in detalles:
            morir(f"id de actividad duplicado entre áreas: {doc.stem}")
        detalles[doc.stem] = doc
        shutil.copyfile(doc, docs / "02-flujos" / doc.name)

    # Los planos viajan con el workspace: son la fuente de verdad regenerable. A partir
    # de aquí ESTA copia es la canónica (las iteraciones modo C parten de ella); la
    # carpeta de la entrevista queda como borrador desechable. Las notas privadas de la
    # entrevista (mural.md) no viajan.
    planos_dir = docs / "02-flujos" / "planos"
    planos_dir.mkdir()
    mapa_destino = planos_dir / "planos.json"
    shutil.copyfile(mapa, mapa_destino)
    preparar_documentos_de_planos(mapa, mapa_destino)
    for pj in sorted((proyecto / "actividades").glob("*/planos.json")):
        carpeta_actividad = planos_dir / "actividades" / pj.parent.name
        carpeta_actividad.mkdir(parents=True)
        pj_destino = carpeta_actividad / "planos.json"
        shutil.copyfile(pj, pj_destino)
        preparar_documentos_de_planos(pj, pj_destino)
    (docs / "02-flujos" / "INDICE.md").write_text(generar_indice(planos, detalles),
                                                  encoding="utf-8")
    (docs / "05-trabajo" / "ESTADO.md").write_text(generar_estado(remoto_pendiente),
                                                   encoding="utf-8")

    # --- Raíz ---
    agents = (PLANTILLA / "AGENTS.md").read_text(encoding="utf-8")
    (destino / "AGENTS.md").write_text(agents.replace("{{TITULO}}", titulo), encoding="utf-8")
    # Puentes de los harnesses que no leen AGENTS.md por su cuenta. Mismo contenido: una
    # línea que importa el router. Cualquier agente nuevo se añade aquí, no en AGENTS.md.
    for puente in ("CLAUDE.md", "GEMINI.md"):
        (destino / puente).write_text("@AGENTS.md\n", encoding="utf-8")
    (destino / "README.md").write_text(
        generar_readme(titulo, frase, remoto_meta, destino.name), encoding="utf-8")
    # setup.py: deja el workspace listo en cualquier ordenador (lee repos.yaml, clona o
    # actualiza main/, crea las carpetas que git no trae y linta). Idempotente.
    shutil.copyfile(PLANTILLA / "setup.py", destino / "setup.py")
    (destino / "repos.yaml").write_text(generar_repos_yaml(nombre_codigo, remoto, rama_codigo),
                                        encoding="utf-8")
    ignora = (PLANTILLA / "gitignore").read_text(encoding="utf-8")
    (destino / ".gitignore").write_text(ignora, encoding="utf-8")
    (destino / "worktrees").mkdir()
    shutil.copyfile(PLANTILLA / "worktrees-README.md", destino / "worktrees" / "README.md")
    # Hook de git AGNÓSTICO al agente (Codex, OpenCode, Antigravity, a mano): pre-push
    # que bloquea ramas de unidad sin spec. Lo ejecuta git, no el agente.
    copiar_arbol(PLANTILLA / "githooks", destino / ".githooks")
    # Sitios definidos para lo sensible y lo generado (su CONTENIDO va gitignored; solo
    # viaja el README de cada uno, para que un clon fresco encuentre las carpetas descritas).
    (destino / ".private").mkdir()
    (destino / ".private" / "README.md").write_text(
        "Secretos, PII y material sensible. NUNCA en git. En docs y specs: evidencia\n"
        "sanitizada con una referencia a este directorio.\n", encoding="utf-8")
    (destino / ".runtime").mkdir()
    (destino / ".runtime" / "README.md").write_text(
        "Artefactos generados: logs, capturas, dumps, estados de tareas. Fuera de git.\n",
        encoding="utf-8")
    # La guía del humano (sin tecnicismos) y la CI del método
    (destino / "GUIA.md").write_text(generar_guia(titulo), encoding="utf-8")
    (destino / "METODO.json").write_text(
        json.dumps({"formato": 1, "huella": huella_plantilla(), "version": version_metodo(),
                    "archivos": manifiesto_metodo()},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destino / ".github" / "workflows").mkdir(parents=True)
    (destino / ".github" / "workflows" / "lint.yml").write_text(
        generar_ci(), encoding="utf-8")

    # --- El linter del método valida lo creado: si falla, el bootstrap falla ---
    lint = docs / "00-metodo" / "scripts" / "lint_metodo.py"
    r = subprocess.run([sys.executable, str(lint)], capture_output=True, text=True)
    print(r.stdout, end="")
    if r.returncode != 0:
        morir("el workspace creado NO pasa el linter (arriba); no lo uses sin arreglarlo")

    # --- Los dos repositorios ---
    avisos = montar_git(destino, nombre_codigo, titulo, remoto_codigo, remoto_meta)
    if args.github:
        aviso = proteger_main(args.github, nombre_codigo)
        if aviso:
            avisos.append(aviso)

    # Memoria local de esta instalación. Vive fuera de Git y nunca bloquea un
    # bootstrap válido: si falla, el workspace sigue siendo utilizable y se puede
    # registrar después con `python3 visor/proyectos.py registrar RUTA`.
    registro = subprocess.run(
        [sys.executable, str(BASE / "proyectos.py"), "registrar", str(destino), "--generado"],
        capture_output=True,
        text=True,
    )
    if registro.returncode:
        avisos.append("no pude guardar el workspace en el registro local (%s)" %
                      (registro.stdout + registro.stderr).strip())

    print(f"\nWorkspace montado en {destino} ({len(detalles)} actividad(es) con detalle).")
    if brownfield:
        print("Código existente detectado en el remoto: viaja el bias BROWNFIELD. La "
              "PRIMERA unidad del workspace es obligatoria: la ADOPCIÓN (analizar main/, "
              "extraer su documentación y stack a 03-investigacion/, inventariar tests y "
              "levantar el gap-map código↔flujos). Ver docs/01-constitucion/bias.md.")
    elif fichero_bias == "generico.md":
        print(f"Tipo '{args.tipo}': aún sin receta tecnológica propia — viaja el bias "
              "GENÉRICO (el stack se decide en la fase 3, con investigación y ADR).")
    if remoto_pendiente:
        print("Dos repos locales (sin remotos). Cuando quieras GitHub: vuelve a la herramienta "
              "y pídelo, o conecta remotos a mano y actualiza repos.yaml.")
    else:
        print(f"Dos repos independientes: meta ({nombre_meta}) y código desde "
              f"{remoto_codigo}; main/ sigue siempre origin/{rama_codigo}.")
    for a in avisos:
        print(f"AVISO: {a}")
    print("Siguiente paso del método: fase 3 (investigación) — abrir sesión del agente "
          "padre en el workspace; se orientará con AGENTS.md y ESTADO.md.")


if __name__ == "__main__":
    main()

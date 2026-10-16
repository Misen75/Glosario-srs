"""Migra DS-GlosarioTerminos.html -> PostgreSQL (DATABASE_URL) en la nube (Supabase / Render).

Uso (desde la raíz glosario-srs/):
    python scripts/seed_from_html.py [--html RUTA] [--db DATABASE_URL]

* Lee los términos y categorías de DS-GlosarioTerminos.html sin perder una coma.
* Rellena las 11 categorías con su explicación simple y nivel de Zoom (1 a 4).
* Inserta de forma inmutable los términos e inicializa su srs_state.
* Es idempotente: si ya existen, los omite de forma segura.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.domain.models import NewTermDraft, ZoomLevel  # noqa: E402
from src.infrastructure.db_repository import PostgresGlossaryRepository  # noqa: E402

DEFAULT_HTML = ROOT / "data" / "DS-GlosarioTerminos.html"
MACRO = "Diseño y Arquitectura de Software"

CATEGORY_META: dict[str, tuple[ZoomLevel, str]] = {
    "Principios Fundamentales": (
        ZoomLevel.MICRO,
        "Las ideas base que juzgan si un trozo de código está bien organizado: qué tan enfocado está "
        "(cohesión), cuánto depende de otros (acoplamiento) y qué esconde (abstracción, encapsulamiento). "
        "Actúan a nivel de clase y función, pero son la raíz de todo lo demás.",
    ),
    "SOLID": (
        ZoomLevel.MICRO,
        "Cinco reglas prácticas para diseñar clases orientadas a objetos que se puedan cambiar sin romperse. "
        "Son los Principios Fundamentales convertidos en criterios concretos. Actúan a nivel de clase e interfaz.",
    ),
    "Patrones GoF (Creacionales)": (
        ZoomLevel.MICRO,
        "Recetas probadas (catálogo 'Gang of Four', 1994) para resolver CÓMO se crean los objetos sin que el "
        "código cliente quede atado a clases concretas. Actúan dentro del código, en el momento de instanciar.",
    ),
    "Patrones GoF (Comportamiento)": (
        ZoomLevel.MICRO,
        "Recetas GoF para organizar QUIÉN hace qué y cómo se comunican los objetos en tiempo de ejecución "
        "(algoritmos intercambiables, notificaciones, órdenes encolables). Actúan a nivel de colaboración entre objetos.",
    ),
    "Patrones GoF (Estructurales)": (
        ZoomLevel.MEDIO,
        "Recetas GoF para ENSAMBLAR clases y objetos en estructuras mayores: envolver, adaptar o simplificar "
        "piezas existentes. Viven en la frontera entre tu código y librerías o subsistemas, por eso suben a nivel módulo.",
    ),
    "Interfaces y Contratos": (
        ZoomLevel.MEDIO,
        "Cómo dos programas o módulos acuerdan hablar entre sí sin conocer sus tripas: qué se puede pedir, "
        "con qué formato y qué se recibe. Actúan en el borde entre componentes o sistemas.",
    ),
    "Calidad y Operatividad": (
        ZoomLevel.MEDIO,
        "Propiedades y prácticas transversales que mantienen el software confiable y barato de cambiar en el "
        "tiempo (operaciones repetibles, datos que no mutan, deuda técnica, refactorización). Afectan a módulos enteros.",
    ),
    "Arquitectura y DDD": (
        ZoomLevel.MACRO,
        "Cómo se reparte y se nombra el sistema completo alrededor del negocio: límites entre modelos, "
        "lenguaje compartido, núcleo aislado de la tecnología y forma de documentarlo. Vista de plano de la ciudad.",
    ),
    "Estilos Arquitectónicos": (
        ZoomLevel.MACRO,
        "Las 'formas' generales que puede tomar un sistema entero (capas, monolito, microservicios, eventos). "
        "Es la decisión de más alto nivel sobre cómo se despliegan y comunican sus partes.",
    ),
    "Rendimiento e Infraestructura": (
        ZoomLevel.INFRAESTRUCTURA,
        "Cómo se mide y se hace crecer el sistema cuando ya corre en servidores reales: tráfico, velocidad, "
        "memoria rápida y reparto de carga. Actúa en hardware, red y despliegue.",
    ),
    "Resiliencia y Disponibilidad": (
        ZoomLevel.INFRAESTRUCTURA,
        "Cómo logra el sistema seguir en pie cuando algo falla (un servicio externo, un servidor, la red). "
        "Actúa en la operación en producción, entre servicios distribuidos.",
    ),
}

TRADEOFFS: dict[str, str] = {
    "Cohesión": "Forzar cohesión extrema fragmenta el código en decenas de clases diminutas que obligan a saltar entre archivos para entender un flujo simple.",
    "Acoplamiento": "Desacoplar todo con interfaces e indirecciones en un sistema pequeño añade capas de complejidad que no se pagan si las piezas nunca cambian por separado.",
    "Abstracción": "Abstraer antes de tener al menos dos casos reales produce abstracciones equivocadas (abstracciones con fugas) más costosas de deshacer que el código duplicado.",
    "Encapsulamiento": "Encapsular estructuras de datos simples (DTOs) con getters/setters triviales solo agrega ruido sin proteger ninguna regla real.",
    "Separation of Concerns (SoC)": "Separar en capas un script o prototipo de 100 líneas multiplica archivos y ceremonia sin beneficio de mantenimiento.",
    "Single Responsibility (S)": "Aplicado de forma dogmática atomiza el dominio en clases anémicas y dispersa una regla de negocio en demasiados lugares.",
    "Open/Closed (O)": "Diseñar puntos de extensión 'por si acaso' crea jerarquías especulativas; si el cambio nunca llega, solo dejaste complejidad.",
    "Liskov Substitution (L)": "Su costo oculto es que obliga a renunciar a herencias 'intuitivas' (Cuadrado hereda de Rectángulo) y preferir composición, lo que a veces duplica código.",
    "Interface Segregation (I)": "Fragmentar en exceso produce una explosión de interfaces de un solo método que dificulta descubrir qué puede hacer realmente un objeto.",
    "Dependency Inversion (D)": "Crear una interfaz para cada dependencia con una sola implementación posible añade indirección y archivos sin ganar flexibilidad real.",
    "Factory Method / Abstract Factory": "Evítalo cuando solo existe un tipo concreto: un simple constructor es más claro que una jerarquía de fábricas.",
    "Builder": "Para objetos con pocos campos es ceremonia innecesaria; los parámetros con nombre o valores por defecto del lenguaje resuelven lo mismo.",
    "Singleton": "Introduce estado global oculto que acopla todo el código, complica los tests en paralelo y suele reemplazarse mejor con Inyección de Dependencias.",
    "Adapter": "Acumular adaptadores sobre adaptadores oculta el verdadero problema de integración y agrega latencia y puntos de fallo por cada traducción.",
    "Decorator": "Muchas capas de decoradores anidados dificultan depurar qué envoltura alteró el comportamiento y el orden de aplicación se vuelve frágil.",
    "Facade": "Tiende a convertirse en una 'clase Dios' que lo sabe todo; además oculta capacidades del subsistema que algunos clientes sí necesitan.",
    "Proxy": "Cada intermediario añade latencia y comportamiento invisible (caché, permisos) que puede sorprender cuando el objeto real cambia.",
    "Strategy": "Si solo hay dos variantes que casi nunca cambian, un if/else es más legible que una familia de clases intercambiables.",
    "Observer": "Las notificaciones implícitas hacen difícil rastrear quién reacciona a qué, y olvidar desuscribirse provoca fugas de memoria.",
    "Command": "Convertir cada acción en un objeto infla el número de clases; no lo uses si no necesitas encolar, auditar ni deshacer operaciones.",
    "API (Application Programming Interface)": "Publicar una API te compromete a mantener compatibilidad hacia atrás; cada cambio rompe a clientes que no controlas.",
    "SDK (Software Development Kit)": "Mantener SDKs para varios lenguajes es costoso y atan al consumidor a tu ciclo de versiones; para integraciones simples basta la API HTTP.",
    "Contrato de API (API Schema)": "Exige disciplina para mantener el esquema sincronizado con el código; un contrato desactualizado es peor que no tenerlo.",
    "IoC / Inyección de Dependencias": "Los contenedores con 'magia' de autowiring vuelven opaco el arranque y difícil saber qué implementación se está usando realmente.",
    "Domain-Driven Design (DDD)": "Su alto costo de modelado y colaboración no se justifica en aplicaciones CRUD o dominios simples sin reglas de negocio complejas.",
    "Bounded Context": "Cada contexto adicional exige traducciones y sincronización de datos entre modelos, multiplicando la integración si se trazan límites demasiado finos.",
    "Lenguaje Ubicuo": "Requiere esfuerzo continuo para alinear negocio y código; si el negocio cambia de vocabulario, renombrar el código tiene un costo real.",
    "Entity vs. Value Object": "Modelar todo como Value Object inmutable genera muchas copias y presión de memoria; modelar todo como Entity infla la persistencia con IDs innecesarios.",
    "Aggregate / Aggregate Root": "Agregados demasiado grandes provocan bloqueos y conflictos de concurrencia porque toda modificación pasa por una sola raíz transaccional.",
    "Arquitectura Hexagonal": "En proyectos pequeños o CRUD los puertos y adaptadores duplican modelos y archivos sin que nunca se cambie realmente la tecnología externa.",
    "C4 Model": "Los diagramas se desactualizan rápido si no se generan desde el código; documentar los cuatro niveles en sistemas pequeños rara vez compensa.",
    "Capacidad de Negocio": "Es un concepto abstracto que no dicta cómo implementar nada; usarlo como único criterio de división técnica puede crear servicios desbalanceados.",
    "Arquitectura en Capas": "Genera código 'pasamanos' donde cada capa solo reenvía datos a la siguiente y un cambio pequeño atraviesa todas las capas.",
    "Monolito vs. Microservicios": "Los microservicios cambian complejidad de código por complejidad operativa (red, observabilidad, consistencia eventual); para equipos pequeños un monolito modular suele ganar.",
    "Arquitectura Orientada a Eventos (EDA)": "La consistencia eventual y los flujos asíncronos hacen difícil depurar, rastrear y garantizar el orden de los procesos de negocio.",
    "Capacidad (Capacity Planning)": "Sobredimensionar 'por seguridad' desperdicia presupuesto; en la nube suele ser más barato autoescalar que planificar picos con precisión.",
    "Escalabilidad (Vertical vs. Horizontal)": "Escalar horizontalmente exige que la aplicación sea sin estado y complica sesiones, transacciones y consistencia de datos.",
    "Throughput vs. Latencia": "Optimizar uno suele empeorar el otro: agrupar peticiones (batching) sube el throughput pero aumenta la latencia de cada una.",
    "Caché (Caching)": "Introduce el problema de la invalidación: servir datos obsoletos es un error sutil y difícil de detectar.",
    "Balanceador de Carga": "Se convierte en un punto único de falla y añade un salto de red si no se despliega también de forma redundante.",
    "Circuit Breaker": "Configurar mal sus umbrales provoca cortes innecesarios ante fallos transitorios o reacciona demasiado tarde ante caídas reales.",
    "Tolerancia a Fallos / Failover": "Duplicar infraestructura de respaldo eleva costos y el failover mal probado puede fallar justo en la emergencia (o causar 'split-brain').",
    "Idempotencia": "Exige almacenar claves de idempotencia y estado previo, añadiendo almacenamiento y lógica extra a cada operación de escritura.",
    "Inmutabilidad": "Crear nuevas copias en cada cambio aumenta el consumo de memoria y la presión sobre el recolector de basura en estructuras grandes.",
    "Deuda Técnica": "Pagarla toda de inmediato también es un error: a veces endeudarse conscientemente es la decisión correcta para validar un producto a tiempo.",
    "Refactorización": "Refactorizar sin una red de tests automatizados es arriesgado y, si el código está por desecharse, es tiempo perdido.",
}


def parse_html(html_path: Path) -> list[tuple[str, str, str, str]]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    rows = []
    for tr in soup.select("table tbody tr"):
        def cell(cls: str) -> str:
            td = tr.find("td", class_=cls)
            if td is None:
                raise ValueError(f"Fila sin columna '{cls}': {tr}")
            return " ".join(td.get_text().split())
        rows.append((cell("cat"), cell("term"), cell("def"), cell("analog")))
    return rows


def seed(html_path: Path, db_url: str | None = None) -> None:
    repo = PostgresGlossaryRepository(database_url=db_url)
    print("Inicializando esquema en PostgreSQL...")
    repo.init_schema()

    rows = parse_html(html_path)
    html_categories = list(dict.fromkeys(r[0] for r in rows))

    # 1. Categorías ordenadas por zoom
    cat_ids: dict[str, int] = {}
    for name in sorted(html_categories, key=lambda c: (CATEGORY_META[c][0], html_categories.index(c))):
        zoom, desc = CATEGORY_META[name]
        cat = repo.get_or_create_category(MACRO, name, desc, zoom)
        cat_ids[name] = cat.id

    # 2. Términos + SRS
    inserted = skipped = 0
    for cat, term, definition, analogy in rows:
        if repo.find_term_by_name(term):
            skipped += 1
            continue
        zoom, desc = CATEGORY_META[cat]
        draft = NewTermDraft(
            term=term,
            macro_area=MACRO,
            category_name=cat,
            category_description=desc,
            zoom_level=zoom,
            technical_definition=definition,
            daily_analogy=analogy,
            tradeoff_when_not_to_use=TRADEOFFS.get(term),
        )
        repo.add_term(cat_ids[cat], draft)
        inserted += 1

    print(f"HTML: {len(rows)} filas · {len(html_categories)} categorías")
    print(f"PostgreSQL: insertados={inserted} · ya existían={skipped} · total términos={repo.count_terms()}")
    print("¡Migración a PostgreSQL completada con éxito!")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    ap.add_argument("--db", type=str, default=None, help="DATABASE_URL de PostgreSQL (o toma de .env)")
    args = ap.parse_args()
    seed(args.html, args.db)

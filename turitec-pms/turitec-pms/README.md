# Turitec PMS — Sistema de gestión para hoteles boutique y posadas

**Turitec** es un PMS (Property Management System) para hoteles boutique y posadas pequeñas (menos de 100 habitaciones) de Tuxtla Gutiérrez, Chiapas.

Resuelve el problema que hoy tienen muchas posadas: las reservas se llevan en libreta, Excel o WhatsApp, y eso provoca **sobreventa de habitaciones, errores de cobro y falta de trazabilidad**.

Con Turitec puedes:

1. **Reservar en un calendario interactivo** (rack de disponibilidad) que muestra qué habitaciones están libres u ocupadas, y que **rechaza automáticamente una reservación si se solapa** con otra en la misma habitación.
2. **Hacer check-in y check-out sin papel**, desde la lista de reservaciones.
3. **Generar factura y comprobante de pago automáticos** (noches × precio + consumos, IVA 16%, RFC del huésped).
4. **Ver reportes sencillos de ocupación mensual**: cuartos ocupados, tasa de ocupación e ingresos, con gráfico de barras.
5. **Sincronizar la disponibilidad con plataformas externas** (Booking, Airbnb) de forma simulada, para evitar la sobreventa.
6. **Chatbot de Telegram (opcional):** avisos automáticos de reservaciones, check-in/out y facturas, y un bot para clientes que responde /habitaciones, /precios y /disponibilidad; `/reservas` y `/ocupacion` son solo para el personal. Se configura con `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en el archivo `.env`.

> Proyecto: **Equipo 10 / DevTuritec, UNACH — Proyectos Integradores 2026** · Tuxtla Gutiérrez, Chiapas.
> Equipo: Víctor Manuel Calderón Vázquez (Director General), Luis Enrique Balbuena Aguilar (Backend/BD), Luis Alejandro Toledo Hernández (Frontend), José Giovanny Gamboa Sarmiento (QA/Soporte).

---

## Stack (el que pidió Fabiola)

| Capa | Tecnología |
|---|---|
| Arquitectura | **Hexagonal (puertos y adaptadores)** — NO arquitectura por capas, NO Spring Boot/Java, NO Angular |
| Backend | **Python + Flask** (Flask usa Jinja2 de forma nativa) + **Node.js** (servicio aparte de sincronización) |
| Frontend | **Jinja2, CSS propio y JavaScript** para animaciones e interactividad |
| Base de datos | **PostgreSQL** (esquema en `schema_postgres.sql` + `docker-compose.yml`). La demo corre por defecto con **SQLite** (`turitec.db`) si no hay Postgres, gracias a SQLAlchemy + `DATABASE_URL` |

### Arquitectura hexagonal — dónde vive cada cosa

```
src/turitec/
├── domain/            # Dominio puro: NO depende de Flask ni SQLAlchemy
│   ├── entities.py    # Habitacion, Huesped, Reservacion, Factura, Consumo, Usuario
│   │                  # Reglas: anti-sobreventa, noches/total, estados
│   │                  # reservada -> check-in -> check-out / cancelada, factura con IVA 16%
│   └── ports.py       # Interfaces (ABC): repositorios y puerto de canal externo
├── application/       # Casos de uso: dependen solo de los puertos
│   └── use_cases.py   # CrearReservacion, HacerCheckIn, HacerCheckOut,
│                      # GenerarFactura, ReporteOcupacionMensual, SincronizarDisponibilidad
└── infrastructure/    # Adaptadores: lo que se puede cambiar sin tocar el dominio
    ├── db.py          # Modelos SQLAlchemy + repositorios + datos de ejemplo (seed)
    ├── channels.py    # Canal externo simulado (Booking/Airbnb/directo, sin llamadas reales)
    └── web/
        ├── routes.py      # Blueprints Flask: vistas HTML + API REST JSON
        ├── templates/     # Jinja2 en español
        └── static/        # CSS propio (azul/naranja) y JS (animaciones, validación)

services/channel_sync/ # Servicio Node.js aparte: lee /api/disponibilidad y
                       # simula empujar disponibilidad a los canales (solo log)
tests/test_domain.py   # Pruebas pytest del dominio (sin Flask ni BD)
```

**La idea clave:** si mañana se cambia la base de datos o el framework web, el dominio y los casos de uso no cambian; solo se escribe un adaptador nuevo.

---

## Guía paso a paso en Visual Studio Code (desde cero)

> Esta guía **no asume conocimientos previos de terminal**. Sigue los pasos en orden, copiando cada comando tal cual. La terminal es solo una ventana donde escribes comandos y pulsas **Enter**.

### Paso 0 — Qué necesitas instalado antes de empezar

- **Visual Studio Code** (descárgalo en https://code.visualstudio.com/ e instálalo como cualquier programa).
- **Python 3.11 o superior** (en https://www.python.org/downloads/ — en Windows, marca la casilla **"Add Python to PATH"** durante la instalación).
- **Docker Desktop** — solo si quieres usar PostgreSQL con Docker (opcional; la demo funciona sin él). En https://www.docker.com/products/docker-desktop/.
- **Node.js 18 o superior** — solo para el servicio de sincronización (opcional). En https://nodejs.org/.

Para comprobar que Python está instalado, abre una terminal y escribe `python --version` (Windows) o `python3 --version` (Mac/Linux). Debe mostrar algo como `Python 3.12.x`.

### Paso 1 — Abrir la carpeta del proyecto en VS Code

1. Abre **Visual Studio Code**.
2. En el menú superior, haz clic en **File > Open Folder...** (en Mac: **Archivo > Abrir carpeta...**).
3. Busca y selecciona la carpeta **`turitec-pms`** (la que contiene este README) y pulsa **Select Folder / Abrir**.
4. Si VS Code pregunta si confías en los autores de la carpeta, pulsa **Yes, I trust the authors / Sí, confío en los autores**.

Ya deberías ver a la izquierda el explorador de archivos con carpetas como `src`, `tests` y `services`.

### Paso 2 — Abrir la terminal integrada de VS Code

1. En el menú superior, haz clic en **Terminal > New Terminal** (en español: **Terminal > Nueva terminal**).
2. Se abrirá un panel en la parte inferior de VS Code con un cursor parpadeante. Ahí es donde escribirás los comandos de los siguientes pasos.
3. Comprueba que estás dentro de la carpeta del proyecto: escribe este comando y pulsa **Enter**:

```bash
pwd
```

Debe mostrar una ruta que termina en `turitec-pms`. Si no es así, usa el comando `cd` con la ruta de tu carpeta, por ejemplo (Windows):

```powershell
cd C:\Users\TuUsuario\Documents\turitec-pms
```

o (Mac/Linux):

```bash
cd ~/Documents/turitec-pms
```

> **Consejo:** en la terminal puedes pegar con **Ctrl + V** (Windows/Linux) o **Cmd + V** (Mac).

### Paso 3 — Crear y activar el entorno virtual

Un entorno virtual es una carpeta (`venv`) donde se instalan las librerías solo para este proyecto, sin ensuciar el resto de tu computadora. Se crea una sola vez.

**En Windows (PowerShell):**

```powershell
python -m venv venv
```

```powershell
.\venv\Scripts\Activate.ps1
```

**En macOS / Linux:**

```bash
python3 -m venv venv
```

```bash
source venv/bin/activate
```

Sabrás que funcionó porque al inicio de la línea de la terminal aparecerá **`(venv)`**, por ejemplo: `(venv) PS C:\...\turitec-pms>`.

> Si en Windows te da un error de permisos al activar (`...cannot be loaded because running scripts is disabled...`), escribe este comando, pulsa Enter y vuelve a intentar la activación:
>
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Paso 4 — Instalar las dependencias

Con el entorno virtual activado (debe verse `(venv)`), escribe:

```bash
pip install -r requirements.txt
```

Verás cómo se descargan Flask, Flask-SQLAlchemy y pytest. Espera a que termine (puede tardar un minuto la primera vez).

> **Importante:** `psycopg2-binary` ya NO está en `requirements.txt`, a propósito. Es opcional y solo se necesita para PostgreSQL. En Python 3.14 la versión 2.9.9 no tenía rueda precompilada para Windows, intentaba compilar desde el código fuente y pedía "Microsoft Visual C++ 14.0", y ese fallo cancelaba TODA la instalación, incluido Flask. Por eso la demo con SQLite no lo necesita. Si vas a usar PostgreSQL, instálalo aparte después, en el Paso 5B.

### Paso 5 — Configurar la base de datos (elige UNA de las dos opciones)

#### Opción A — Demo con SQLite (recomendada para empezar, no instala nada)

No tienes que hacer nada. Por defecto la app usa un archivo local llamado **`turitec.db`** que se crea solo al arrancar. Pasa directamente al Paso 6.

#### Opción B — PostgreSQL con Docker (la base de datos real del proyecto)

1. Primero instala el adaptador de PostgreSQL (solo para esta opción, con `(venv)` activo):

```bash
pip install -r requirements-postgres.txt
```

Esto instala `psycopg2-binary==2.9.11`, la primera versión con rueda precompilada para Python 3.14 en Windows. No uses la 2.9.9.

2. Asegúrate de que **Docker Desktop esté abierto y corriendo** (su icono debe verse en la barra de tareas).
3. En la misma terminal de VS Code, escribe:

```bash
docker compose up -d postgres
```

Esto descarga PostgreSQL 16 y crea la base de datos `turitec` con el usuario `turitec` y la contraseña `turitec123`. El esquema de tablas está en `schema_postgres.sql` y se aplica solo al crear el contenedor.

4. Ahora dile a la app que use Postgres en lugar de SQLite. Crea un archivo llamado **`.env`** copiando el ejemplo:

**En Windows (PowerShell):**

```powershell
copy .env.example .env
```

**En macOS / Linux:**

```bash
cp .env.example .env
```

5. Abre el archivo `.env` en VS Code, busca la línea que empieza con `# DATABASE_URL=` y quítale el `#` del inicio para que quede así:

```
DATABASE_URL=postgresql://turitec:turitec123@localhost:5432/turitec
```

Guarda el archivo (**Ctrl + S** / **Cmd + S**).

> Para volver a la demo con SQLite, simplemente borra o vuelve a comentar esa línea en `.env`.

### Paso 6 — Cargar los datos de ejemplo (seed)

En la terminal (con `(venv)` activo), escribe:

```bash
python seed.py
```

Debe aparecer: `Datos de ejemplo creados.` (la segunda vez dirá que ya tenía datos y no duplicará nada).

Esto crea el hotel demo **"Posada Turitec"** con:

- **12 habitaciones**: 5 sencillas ($650–$700), 4 dobles ($950–$1,050) y 3 suites ($1,500–$1,800 MXN por noche).
- **8 huéspedes** con RFC.
- **10 reservaciones de ejemplo en noviembre de 2026**, algunas con check-in y check-out.
- **1 factura de ejemplo** (3 noches + consumos, IVA 16%).
- **2 usuarios demo** (ver Paso 8).

> El seed también se ejecuta solo al primer arranque si la base de datos está vacía, así que este paso es de seguridad: si lo saltas, la app igual crea los datos.

### Paso 7 — Levantar el servidor

En la terminal (con `(venv)` activo), escribe:

```bash
python run.py
```

Verás algo como:

```
Turitec PMS corriendo en http://127.0.0.1:5000
Usuarios demo: admin@turitec.mx / admin123  |  recepcion@turitec.mx / recepcion123
 * Running on http://127.0.0.1:5000
```

**No cierres la terminal ni pulses nada más: mientras ese texto esté ahí, el servidor está corriendo.** Si cierras la terminal, el servidor se apaga.

### Paso 8 — Abrir la app en el navegador

1. Abre tu navegador (Chrome, Edge, Firefox...).
2. En la barra de direcciones escribe exactamente:

```
http://127.0.0.1:5000
```

3. Pulsa **Enter**. Verás la pantalla de inicio de sesión de Turitec.
4. Entra con uno de los usuarios demo:

| Rol | Correo | Contraseña |
|---|---|---|
| Administrador | `admin@turitec.mx` | `admin123` |
| Recepción | `recepcion@turitec.mx` | `recepcion123` |

5. Prueba el flujo completo:
   - **Panel**: habitaciones ocupadas hoy, reservaciones activas, ingresos y tasa de ocupación.
   - **Reservaciones > Nueva reservación**: crea una con fechas que se solapen con otra de la misma habitación y verás el error en español (anti-sobreventa). Con fechas libres, se crea bien.
   - **Calendario**: el rack mensual por habitación (verde = ocupada, amarillo = reservada, azul claro = libre).
   - **Check-in / Check-out**: desde la lista de reservaciones.
   - **Facturación**: genera la factura de una reservación con check-out (verás subtotal, IVA 16% y total) y usa **Imprimir comprobante**.
   - **Reportes**: cambia el mes y verás el gráfico de barras animado.

### Paso 9 — Cómo detener el servidor

1. Vuelve a la terminal de VS Code donde corre el servidor.
2. Pulsa **Ctrl + C** (en Mac también **Ctrl + C**, no Cmd + C).
3. La terminal vuelve a mostrar el cursor `(venv)` y el servidor queda apagado.
4. Para volver a levantarlo, repite solo el Paso 7 (`python run.py`).

> Si usaste Docker (Opción B) y quieres apagar también PostgreSQL, escribe:
>
> ```bash
> docker compose down
> ```

### Paso 10 — Cómo correr las pruebas (tests)

Con el servidor detenido (o en una terminal nueva: **Terminal > New Terminal**), con `(venv)` activo, escribe:

```bash
python -m pytest tests/ -v
```

Debe mostrar **16 passed**. Las pruebas comprueban, sin necesidad de base de datos ni servidor:

- Que una reservación solapada se rechaza (anti-sobreventa).
- Que las noches y el total se calculan bien (3 noches × $650 = $1,950).
- Que las fechas inválidas se rechazan.
- Que el check-in ocupa la habitación y el check-out la libera.
- Que la factura calcula el IVA 16% (subtotal $1,300 → IVA $208.00 → total $1,508.00).
- Que el reporte mensual calcula las noches ocupadas y la tasa de ocupación.
- Que el chatbot de Telegram responde /disponibilidad, /reservas y /ocupacion, y que el notificador no tumba la app si Telegram falla.

---

## Chatbot de Telegram (opcional)

Turitec tiene dos cosas con Telegram, y las dos se configuran con lo mismo. No necesitas instalar nada más: usa solo la librería estándar de Python.

**1. Avisos automáticos:** cuando se crea una reservación, se hace check-in/check-out, se cancela o se genera una factura, la app te manda un mensaje a Telegram. Si Telegram no está configurado o falla, la app sigue funcionando igual, solo no llega el aviso.

**2. Chatbot para los clientes:** cualquier persona que abra tu bot en Telegram (`t.me/Hoteleraboutiquebot`) puede preguntarle habitaciones, precios y si están libres u ocupadas, sin abrir la app web. Los datos de los huéspedes y los ingresos quedan solo para ti: `/reservas` y `/ocupacion` solo te responden a ti (al chat de `TELEGRAM_CHAT_ID`).

### Paso 1 — Crear el bot y conseguir el token

1. En Telegram busca **@BotFather** y escribe `/newbot`.
2. Te pedirá un nombre (por ejemplo, `Turitec Hotel`) y un nombre de usuario que termine en `bot` (por ejemplo, `TuritecHotelBot`).
3. Al final te dará un **token** que se ve así (guárdalo, es como una contraseña, no lo compartas):

```
123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCrK7g5s
```

### Paso 2 — Averiguar tu id de chat

1. Busca tu bot en Telegram (por su nombre de usuario) y escribe `/start`.
2. En la terminal de VS Code, con `(venv)` activo, corre:

```bash
python services/telegram_bot/bot.py --get-id
```

Te mostrará algo como `chat_id=123456789`. Ese número es tu id de chat. También puedes escribirle a **@userinfobot** en Telegram y te dirá tu id.

> Si el bot va a avisar a un grupo en lugar de a ti, agrega el bot al grupo, escribe algo en el grupo y corre `--get-id`: el id del grupo será un número negativo (por ejemplo, `-1001234567890`).

### Paso 3 — Poner el token y el id en el archivo .env

1. Copia el ejemplo (si aún no tienes `.env`):

```powershell
copy .env.example .env
```

En macOS/Linux: `cp .env.example .env`

2. Abre `.env` en VS Code y quita el `#` de estas líneas, poniendo tus valores:

```
TELEGRAM_BOT_TOKEN=123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCrK7g5s
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ENABLED=true
```

3. Guarda (**Ctrl + S**). Si usas PostgreSQL, esas líneas conviven en el mismo `.env` con `DATABASE_URL`.

### Paso 4 — Probar que funciona

Con `(venv)` activo, corre:

```bash
python services/telegram_bot/bot.py --test
```

Debe llegarte un mensaje a Telegram que dice que Turitec está conectado. También puedes probarlo desde la app web, ya logueado, con:

```bash
curl -X POST http://127.0.0.1:5000/api/telegram/test
```

(o con la ruta `POST /api/telegram/test` en tu cliente de API favorito).

### Paso 5 — Poner el chatbot a escuchar

El bot es un proceso aparte: abre **otra terminal** en VS Code (**Terminal > New Terminal**), con `(venv)` activo, y corre:

```bash
python services/telegram_bot/bot.py
```

Verás `🤖 Turitec Bot escuchando en Telegram`. Déjalo corriendo en esa terminal. Para detenerlo, pulsa **Ctrl + C**.

> La app web (`python run.py`) y el bot son dos terminales distintas y pueden correr a la vez. Los avisos automáticos los manda la app web sola, sin necesidad de que el bot esté escuchando. El bot que escucha es solo para que tú le escribas comandos.

### Comandos del chatbot

**Para los clientes (cualquiera puede usarlos):**

| Comando | Qué hace |
|---|---|
| `/start` o `/ayuda` | Muestra la lista de comandos |
| `/habitaciones` | Todas las habitaciones, con precio y si están libres u ocupadas hoy |
| `/precios` | Precios por tipo de habitación (Sencilla, Doble, Suite) |
| `/disponibilidad` | Habitaciones libres y ocupadas hoy y mañana |
| `/disponibilidad 2026-11-12 2026-11-14` | Habitaciones libres y ocupadas en ese rango |

**Solo para el personal (responden únicamente al chat de `TELEGRAM_CHAT_ID`):**

| Comando | Qué hace |
|---|---|
| `/reservas` | Reservaciones activas |
| `/ocupacion` | Ocupación de este mes |
| `/ocupacion 2026-11` | Ocupación de noviembre de 2026 |

> 💬 **También se puede escribir sin la barra:** el bot entiende mensajes normales como `habitaciones`, `precios`, `¿hay habitaciones libres?` o `¿cuánto cuesta?`. Para fechas, se escriben así: `disponibilidad 2026-11-12 2026-11-14`.
>
> ⚠️ **El token es como una contraseña: no lo compartas ni lo pegues en un chat.** Va solo en tu archivo `.env`, en tu computadora. Si alguna vez se filtra, en Telegram escribe a **@BotFather**, usa `/revoke`, elige tu bot y te dará un token nuevo; luego actualiza el `.env` con ese token.

> Seguridad: si pusiste `TELEGRAM_CHAT_ID` en `.env`, el bot solo responde a ese chat y ignora mensajes de cualquier otro. Si no lo pones, responde a quien le escriba.

---

## Servicio de sincronización con canales externos (Node.js, opcional)

Este servicio **no es necesario** para que la app Python corra. Es un proceso aparte que lee la disponibilidad desde la API y simula enviarla a Booking y Airbnb (solo escribe un log, no hace llamadas reales).

1. Con la app Python corriendo (`python run.py` en una terminal), abre **otra terminal** en VS Code (**Terminal > New Terminal**).
2. Entra a la carpeta del servicio y ejecútalo:

```bash
cd services/channel_sync
node sync.js 2026-11-01 2026-11-30
```

Verás líneas como: `[Turitec Sync] booking: habitación 101 (Sencilla) → DISPONIBLE (simulado)`.

También puedes usar la API directamente: `POST /api/sync` con `{"fecha_inicio": "2026-11-01", "fecha_fin": "2026-11-30"}`.

---

## API REST

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/habitaciones` | Lista de habitaciones (JSON) |
| GET | `/api/reservaciones` | Lista de reservaciones (JSON) |
| GET | `/api/disponibilidad?fecha_inicio=2026-11-10&fecha_fin=2026-11-12` | Disponibilidad por habitación en un rango |
| POST | `/api/reservaciones` | Crea una reservación (JSON). Si se solapa, responde **400** con el error en español; si es válida, **201** |
| POST | `/api/sync` | Sincronización simulada con canales externos |
| POST | `/api/telegram/test` | Envía un mensaje de prueba por Telegram (requiere login; útil para verificar token e id) |

Ejemplo con curl (en una terminal, con la app corriendo):

```bash
curl "http://127.0.0.1:5000/api/disponibilidad?fecha_inicio=2026-11-10&fecha_fin=2026-11-12"
```

---

## Estructura de archivos

```
turitec-pms/
├── README.md
├── requirements.txt
├── .env.example
├── docker-compose.yml          # postgres + app
├── Dockerfile
├── schema_postgres.sql
├── run.py                      # python run.py
├── config.py
├── seed.py                     # python seed.py
├── src/turitec/
│   ├── __init__.py             # app factory Flask (create_app)
│   ├── domain/entities.py
│   ├── domain/ports.py
│   ├── application/use_cases.py
│   └── infrastructure/
│       ├── db.py               # modelos SQLAlchemy + repositorios + seed
│       ├── channels.py         # canal externo simulado
│       ├── telegram.py         # notificador + chatbot de Telegram (Bot API, sin dependencias nuevas)
│       └── web/
│           ├── routes.py
│           ├── templates/      # base, login, dashboard, habitaciones,
│           │                   # huespedes, reservaciones, calendario,
│           │                   # facturacion, factura_detalle, reportes
│           └── static/css/style.css
│           └── static/js/app.js
├── services/channel_sync/
│   ├── sync.js
│   └── package.json
├── services/telegram_bot/
│   └── bot.py                  # python services/telegram_bot/bot.py [--test | --get-id]
└── tests/
    ├── test_domain.py
    └── test_telegram.py
```

## Problemas frecuentes

| Problema | Solución |
|---|---|
| `python no se reconoce como comando` (Windows) | Reinstala Python marcando **"Add Python to PATH"**, o usa `py` en lugar de `python`. |
| No aparece `(venv)` en la terminal | Vuelve a ejecutar el comando de activación del Paso 3 (según tu sistema). |
| `Address already in use` / puerto 5000 ocupado | Otra app usa el puerto. Detén el servidor anterior con **Ctrl + C** o cambia el puerto en `run.py`. |
| La página no carga en el navegador | Comprueba que la terminal sigue mostrando `Running on http://127.0.0.1:5000` y que la dirección es exactamente `http://127.0.0.1:5000`. |
| `docker compose` no funciona | Abre Docker Desktop y espera a que diga que está corriendo; luego reintenta. Recuerda: la Opción A (SQLite) no necesita Docker. |
| `Failed building wheel for psycopg2-binary` / `Microsoft Visual C++ 14.0 or greater is required` | No instales psycopg2 con la versión vieja. Ya está fuera de `requirements.txt`: instala solo lo demás con `pip install -r requirements.txt` y, solo si usas PostgreSQL, después `pip install -r requirements-postgres.txt` (versión 2.9.11, con rueda para Python 3.14). |
| `source no se reconoce` (Windows PowerShell) | `source` es de Linux/Mac. En Windows usa `.\venv\Scripts\Activate.ps1`. |
| `seed.py` o `run.py` solos dan error | En PowerShell un archivo `.py` no se ejecuta solo: escribe siempre `python seed.py` y `python run.py`. `pip seed.py` tampoco existe. |
| `ModuleNotFoundError: No module named 'flask'` | La instalación falló antes de instalar Flask (casi siempre por el error de psycopg2 de arriba). Vuelve a correr `pip install -r requirements.txt` ya corregido y verifica que termine sin `error:`. |

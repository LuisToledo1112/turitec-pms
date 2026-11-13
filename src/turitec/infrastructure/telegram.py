"""Adaptador de Telegram para Turitec PMS (arquitectura hexagonal).

Puerto de salida: ``NotificacionPort`` (ver domain/ports.py).
Adaptador: ``TelegramNotificador`` — envía mensajes con la API HTTP de
Telegram (Bot API) usando solo la librería estándar de Python
(``urllib``), sin dependencias nuevas. Por eso funciona en Windows con
Python 3.14 sin instalar nada más.

Configuración (en el archivo ``.env``, nunca en el código):
    TELEGRAM_BOT_TOKEN=123456789:AA...   # token que da @BotFather
    TELEGRAM_CHAT_ID=123456789          # id del chat que recibe los avisos
    TELEGRAM_ENABLED=true               # false = no enviar nada

Qué hace:
1. **Notificaciones**: la app web avisa por Telegram cuando se crea una
   reservación, se hace check-in/check-out, se cancela o se genera una
   factura. Si Telegram no está configurado o falla, la app sigue
   funcionando: el aviso simplemente no se envía.
2. **Chatbot**: el script ``services/telegram_bot/bot.py`` escucha
   mensajes (long polling con getUpdates) y responde comandos. Al
   escribir, cada persona elige si es **cliente** o **personal**:
   el cliente solo usa consultas simples (habitaciones, precios,
   disponibilidad, reservar y consultar *sus* reservaciones) y no ve
   facturas, ventas, huéspedes ni ingresos; el personal entra con
   **teléfono, correo y contraseña** (los mismos de la app web) y ve
   todo: /reservas, /ocupacion, /ventas, /facturas, /corte. El bot
   identifica a quien le escribe (nombre, @usuario e id) y lo incluye
   en los avisos al personal. Solo puede correr UN bot a la vez: un
   segundo proceso se niega a arrancar (bloqueo en
   services/telegram_bot/bot.lock), porque dos bots contestan lo mismo
   y uno dice "No entendí eso".
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..application.use_cases import CorteDia, CrearReservacion, ReporteOcupacionMensual
from ..domain.entities import Huesped
from ..domain.ports import NotificacionPort

from werkzeug.security import check_password_hash

logger = logging.getLogger("turitec.telegram")


def telegram_config() -> dict:
    """Lee la configuración de Telegram desde las variables de entorno.

    Intenta cargar ``.env`` si python-dotenv está instalado; si no,
    simplemente usa las variables ya definidas en el sistema.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    enabled = os.environ.get("TELEGRAM_ENABLED", "true").strip().lower() in ("1", "true", "si", "sí")
    return {"token": token, "chat_id": chat_id, "enabled": enabled}


class TelegramNotificador(NotificacionPort):
    """Envía mensajes de texto a un chat de Telegram con la Bot API."""

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        cfg = telegram_config()
        self.token = (token or cfg["token"]).strip()
        self.chat_id = (chat_id or cfg["chat_id"]).strip()
        self.enabled = cfg["enabled"]

    @property
    def configurado(self) -> bool:
        """True solo si hay token y chat id y el envío está activado."""
        return bool(self.token and self.chat_id and self.enabled)

    def _post(self, metodo: str, datos: dict, timeout: int = 15) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{metodo}"
        cuerpo = urllib.parse.urlencode(datos).encode("utf-8")
        peticion = urllib.request.Request(url, data=cuerpo, method="POST")
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))

    def enviar(self, mensaje: str) -> bool:
        """Envía ``mensaje`` al chat configurado. Devuelve True si llegó."""
        return self.enviar_a(self.chat_id, mensaje)

    def enviar_a(self, chat_id: str | int, mensaje: str, teclado: list | None = None) -> bool:
        if not self.enabled:
            logger.info("Telegram desactivado (TELEGRAM_ENABLED=false); aviso no enviado.")
            return False
        if not self.token:
            logger.warning("Telegram sin token (TELEGRAM_BOT_TOKEN vacío); aviso no enviado.")
            return False
        if not chat_id:
            logger.warning("Telegram sin chat id (TELEGRAM_CHAT_ID vacío); aviso no enviado.")
            return False
        try:
            datos = {
                "chat_id": str(chat_id),
                "text": mensaje,
                "parse_mode": "HTML",
            }
            if teclado:
                # Teclado con botones en el chat (ej. "Soy cliente" / "Soy del personal").
                datos["reply_markup"] = json.dumps({
                    "keyboard": teclado,
                    "one_time_keyboard": True,
                    "resize_keyboard": True,
                })
            resultado = self._post("sendMessage", datos)
            return bool(resultado.get("ok"))
        except Exception as exc:  # noqa: BLE001 - un aviso nunca debe tumbar la app
            logger.warning("No se pudo enviar el aviso de Telegram: %s", exc)
            return False

    def obtener_chat_ids_recientes(self) -> list[dict]:
        """Devuelve los chats que han escrito al bot (para averiguar tu id).

        Útil para el paso de configuración: escribe /start al bot en
        Telegram y luego corre ``python services/telegram_bot/bot.py --get-id``.
        """
        if not self.token:
            return []
        try:
            resultado = self._post("getUpdates", {"timeout": 0, "limit": 20})
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer getUpdates: %s", exc)
            return []
        chats: dict[str, dict] = {}
        for update in resultado.get("result", []):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("id") is not None:
                chats[str(chat["id"])] = {
                    "chat_id": chat["id"],
                    "tipo": chat.get("type", ""),
                    "nombre": chat.get("title") or chat.get("first_name") or "",
                    "usuario": chat.get("username") or "",
                }
        return list(chats.values())


# ---------------- Mensajes de aviso (texto plano, probados en tests) ----------------

def formatear_reservacion(reserva_id: int, habitacion_numero: str, huesped_nombre: str,
                          entrada, salida, noches: int, total: float,
                          telegram_usuario: str = "") -> str:
    texto = (
        f"🛎️ <b>Nueva reservación #{reserva_id}</b>\n"
        f"🏨 Habitación {habitacion_numero}\n"
        f"👤 {huesped_nombre}\n"
        f"📅 {entrada} → {salida} ({noches} noches)\n"
        f"💰 Total: ${total:,.2f} MXN"
    )
    if telegram_usuario:
        texto += f"\n💬 Reservó por Telegram: {telegram_usuario}"
    return texto


def formatear_check_in(reserva_id: int, habitacion_numero: str, huesped_nombre: str) -> str:
    return f"🔑 <b>Check-in</b> — Reservación #{reserva_id}\n🏨 Habitación {habitacion_numero}\n👤 {huesped_nombre}"


def formatear_check_out(reserva_id: int, habitacion_numero: str, huesped_nombre: str) -> str:
    return f"🚪 <b>Check-out</b> — Reservación #{reserva_id}\n🏨 Habitación {habitacion_numero}\n👤 {huesped_nombre}\nYa se puede generar su factura."


def formatear_cancelacion(reserva_id: int, habitacion_numero: str) -> str:
    return f"❌ <b>Reservación #{reserva_id} cancelada</b>\n🏨 Habitación {habitacion_numero} vuelve a estar libre."


def formatear_factura(folio: str, reserva_id: int, huesped_nombre: str, total: float, venta_folio: str = "") -> str:
    origen = f"Venta {venta_folio}" if venta_folio else f"Reservación #{reserva_id}"
    return (
        f"🧾 <b>Factura {folio} generada</b>\n"
        f"{origen} — {huesped_nombre}\n"
        f"💰 Total: ${total:,.2f} MXN (IVA 16% incluido)"
    )


def formatear_venta(folio: str, cliente: str, total: float, metodo_pago: str, cambio: float = 0.0) -> str:
    texto = (
        f"💵 <b>Venta de caja {folio}</b>\n"
        f"Cliente: {cliente}\n"
        f"💰 Total: ${total:,.2f} MXN — pagó con {metodo_pago}"
    )
    if cambio:
        texto += f"\n💸 Cambio: ${cambio:,.2f} MXN"
    return texto


@dataclass
class UsuarioTelegram:
    """Quién escribe al bot: id, @usuario y nombre de Telegram.

    El bot identifica a cada persona que le escribe y usa esta
    identidad en los avisos al personal (por ejemplo, quién reservó),
    para que el personal sepa quién está del otro lado del chat.
    """

    user_id: int | None = None
    username: str = ""
    nombre: str = ""

    def descripcion(self) -> str:
        partes = []
        if self.nombre:
            partes.append(self.nombre)
        if self.username:
            partes.append(f"@{self.username}")
        if self.user_id:
            partes.append(f"id {self.user_id}")
        return " · ".join(partes) or "sin identificar"


def identidad_desde_mensaje(mensaje: dict) -> UsuarioTelegram:
    """Saca la identidad del remitente de un mensaje de Telegram."""
    remitente = mensaje.get("from") or {}
    nombre = " ".join(
        p for p in (remitente.get("first_name", ""), remitente.get("last_name", ""))
        if p
    ).strip()
    return UsuarioTelegram(
        user_id=remitente.get("id"),
        username=remitente.get("username", "") or "",
        nombre=nombre,
    )


def adquirir_bloqueo_bot(ruta_lock) -> object:
    """Impide que dos bots con el mismo token corran a la vez.

    Si el bot ya está corriendo en otra terminal (o arrancó solo con el
    Inicio de Windows), un segundo proceso recibiría los mismos mensajes
    y contestaría "No entendí eso" porque no tiene la conversación en
    curso. Esta función bloquea el arranque del segundo con un error
    claro. Devuelve el archivo abierto: hay que guardarlo mientras el
    bot corre, porque al cerrarse el proceso el sistema libera el
    bloqueo solo, sin dejar archivos basura.
    """
    from pathlib import Path

    ruta = Path(ruta_lock)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    archivo = open(ruta, "w")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        archivo.close()
        raise RuntimeError(
            "Ya hay otro bot de Turitec corriendo. Ciérralo antes de arrancar este: "
            "revisa las terminales de VS Code y el Inicio de Windows (Win+R, shell:startup). "
            "Dos bots a la vez contestan lo mismo y uno de ellos dice 'No entendí eso'."
        )
    archivo.write(str(os.getpid()))
    archivo.flush()
    return archivo


# ---------------- Chatbot: consultas y respuestas ----------------

class ConsultaTuritec:
    """Lee los datos que el chatbot necesita, usando los repositorios.

    Es el adaptador de lectura del bot: no escribe nada en la base de datos.
    """

    def __init__(self, habitaciones, reservaciones, facturas, ventas=None, huespedes=None):
        self.habitaciones = habitaciones
        self.reservaciones = reservaciones
        self.facturas = facturas
        self.ventas = ventas  # opcional: solo lo usa el personal (/ventas, /corte)
        self.huespedes = huespedes  # opcional: nombres en /facturas

    def disponibilidad(self, inicio: date, fin: date) -> list[dict]:
        resultado = []
        for h in self.habitaciones.listar():
            solapadas = self.reservaciones.buscar_solapadas(h.id, inicio, fin)
            resultado.append({
                "numero": h.numero, "tipo": h.tipo,
                "precio_noche": float(h.precio_noche),
                "disponible": not solapadas and h.estado != "mantenimiento",
            })
        return resultado

    def estado_hoy(self) -> list[dict]:
        """Habitaciones con su estado de hoy: libre u ocupada, y su precio."""
        return self.disponibilidad(date.today(), date.today() + timedelta(days=1))

    def precios_por_tipo(self) -> list[dict]:
        """Precios agrupados por tipo de habitación: tipo, desde cuánto, y qué cuartos."""
        por_tipo: dict[str, dict] = {}
        for h in self.habitaciones.listar():
            grupo = por_tipo.setdefault(h.tipo, {"tipo": h.tipo, "desde": float(h.precio_noche), "numeros": []})
            grupo["desde"] = min(grupo["desde"], float(h.precio_noche))
            grupo["numeros"].append(h.numero)
        return [por_tipo[t] for t in sorted(por_tipo)]

    def reservas_activas(self) -> list:
        return [r for r in self.reservaciones.listar() if r.estado in ("reservada", "check-in")]

    def ocupacion(self, anio: int, mes: int) -> dict:
        return ReporteOcupacionMensual(self.reservaciones, self.habitaciones, self.facturas).execute(anio, mes)

    def ventas_hoy(self) -> list:
        """Ventas de caja de hoy. Solo para el personal."""
        if self.ventas is None:
            return []
        hoy = date.today()
        return [v for v in self.ventas.listar() if v.fecha and v.fecha.date() == hoy]

    def facturas_recientes(self, cantidad: int = 5) -> list:
        """Últimas facturas emitidas. Solo para el personal."""
        facturas = sorted(
            self.facturas.listar(),
            key=lambda f: f.fecha_emision or date.min, reverse=True,
        )
        return facturas[:cantidad]

    def nombre_huesped(self, huesped_id: int | None) -> str:
        if self.huespedes is None or not huesped_id:
            return ""
        huesped = self.huespedes.obtener(huesped_id)
        return huesped.nombre if huesped else ""

    def corte_hoy(self) -> dict | None:
        """Corte de caja de hoy (ventas y efectivo neto). Solo para el personal."""
        if self.ventas is None:
            return None
        return CorteDia(self.ventas, self.facturas).execute(date.today())


# ---------------- Reservaciones desde el chatbot ----------------

def referencia_reserva(reserva_id: int) -> str:
    """Referencia que se le da al cliente para confirmar su reservación: TUR-000123."""
    return f"TUR-{int(reserva_id):06d}"


def id_desde_referencia(referencia: str) -> int | None:
    coincidencia = re.search(r"(\d+)", referencia or "")
    return int(coincidencia.group(1)) if coincidencia else None


class RegistroReservas:
    """Registra huéspedes y reservaciones desde el chatbot.

    Usa el caso de uso CrearReservacion, así que se aplica la misma
    regla anti-sobreventa que en la app web: si la habitación ya está
    ocupada en esas fechas, la reservación no se crea.
    """

    def __init__(self, huespedes, habitaciones, reservaciones):
        self.huespedes = huespedes
        self.habitaciones = habitaciones
        self.reservaciones = reservaciones

    def buscar_huesped_por_contacto(self, contacto: str):
        contacto = (contacto or "").strip().lower()
        for h in self.huespedes.listar():
            if h.email.lower() == contacto or h.telefono.strip() == contacto:
                return h
        return None

    def registrar_huesped(self, nombre: str, contacto: str, telegram_user_id: int | None = None) -> Huesped:
        """Registra al huésped (o reutiliza el que ya tenga ese contacto).

        Si el huésped ya existe y aún no tiene id de Telegram, se le
        vincula: así el bot sabe que sus reservaciones son de esa
        persona y solo ella puede consultarlas.
        """
        existente = self.buscar_huesped_por_contacto(contacto)
        if existente is not None:
            if telegram_user_id is not None and existente.telegram_user_id is None:
                existente.telegram_user_id = telegram_user_id
                self.huespedes.guardar(existente)
            return existente
        if "@" in contacto:
            huesped = Huesped(nombre=nombre.strip(), email=contacto.strip(), telegram_user_id=telegram_user_id)
        else:
            huesped = Huesped(nombre=nombre.strip(), telefono=contacto.strip(), telegram_user_id=telegram_user_id)
        return self.huespedes.guardar(huesped)

    def tipos_disponibles(self, entrada: date, salida: date) -> dict[str, list]:
        """Tipos con al menos una habitación libre en esas fechas."""
        libres: dict[str, list] = {}
        for h in self.habitaciones.listar():
            solapadas = self.reservaciones.buscar_solapadas(h.id, entrada, salida)
            if not solapadas and h.estado != "mantenimiento":
                libres.setdefault(h.tipo, []).append(h)
        return libres

    def crear_reservacion(self, huesped_id: int, habitacion_id: int, entrada: date, salida: date):
        return CrearReservacion(self.reservaciones, self.habitaciones).execute(
            habitacion_id, huesped_id, entrada, salida, origen="telegram",
        )

    def consultar(self, referencia: str) -> dict | None:
        """Devuelve reserva + habitación + huésped de una referencia TUR-000000."""
        reserva_id = id_desde_referencia(referencia)
        if reserva_id is None:
            return None
        reserva = self.reservaciones.obtener(reserva_id)
        if reserva is None:
            return None
        return {
            "reserva": reserva,
            "habitacion": self.habitaciones.obtener(reserva.habitacion_id),
            "huesped": self.huespedes.obtener(reserva.huesped_id),
        }


def avanzar_reserva(estado: dict, texto: str, registro: RegistroReservas | None
                    ) -> tuple[str, bool, dict | None]:
    """Avanza un paso de la reservación por chat.

    ``estado`` es {"paso": ..., "datos": {...}} y se guarda por chat en el
    bot. Devuelve (respuesta, terminado, creado): ``creado`` trae los
    datos de la reservación solo cuando se confirma y se crea.
    """
    if registro is None:
        return ("Las reservaciones por Telegram no están disponibles en este momento.", True, None)
    paso = estado.get("paso", "nombre")
    datos = estado.setdefault("datos", {})

    if paso == "nombre":
        nombre = (texto or "").strip()
        if len(nombre) < 3 or nombre.replace(" ", "").isdigit():
            return ("¿Cómo te llamas? Escríbeme tu nombre completo.", False, None)
        datos["nombre"] = nombre
        estado["paso"] = "contacto"
        return (
            f"Mucho gusto, {nombre}. 📱 ¿Me das tu teléfono o tu correo "
            "para confirmarte la reservación? (por ejemplo: 9611234567 o ana@correo.mx)",
            False, None,
        )

    if paso == "contacto":
        contacto = (texto or "").strip()
        es_correo = "@" in contacto and "." in contacto
        digitos = "".join(c for c in contacto if c.isdigit())
        es_telefono = len(digitos) >= 7 and len(digitos) == len(contacto.replace(" ", "").replace("+", "").replace("-", ""))
        if not es_correo and not es_telefono:
            return (
                "Ese contacto no me sirve. Escríbeme tu teléfono (solo números) "
                "o tu correo (por ejemplo: ana@correo.mx).",
                False, None,
            )
        datos["contacto"] = contacto
        estado["paso"] = "fechas"
        return (
            "📅 ¿Para qué fechas? Escríbeme entrada y salida así:\n"
            "2026-11-12 2026-11-14",
            False, None,
        )

    if paso == "fechas":
        fechas = _extraer_fechas(texto or "")
        if len(fechas) < 2:
            return (
                "Necesito las dos fechas. Escríbelas así:\n2026-11-12 2026-11-14",
                False, None,
            )
        entrada, salida = fechas[0], fechas[1]
        if salida <= entrada:
            return ("La fecha de salida debe ser después de la de entrada. Inténtalo de nuevo.", False, None)
        if entrada < date.today():
            return ("La fecha de entrada ya pasó. Escríbeme fechas futuras.", False, None)
        tipos = registro.tipos_disponibles(entrada, salida)
        if not tipos:
            return (
                f"😔 No hay habitaciones libres del {entrada} al {salida}.\n"
                "Prueba con otras fechas.",
                False, None,
            )
        datos["entrada"] = entrada
        datos["salida"] = salida
        estado["paso"] = "tipo"
        lineas = [f"✅ Hay disponibilidad del {entrada} al {salida}. ¿Qué tipo de habitación quieres?"]
        for tipo, habs in sorted(tipos.items()):
            desde = min(float(h.precio_noche) for h in habs)
            lineas.append(f"• {tipo} — desde ${desde:,.2f}/noche ({len(habs)} libre(s))")
        return ("\n".join(lineas), False, None)

    if paso == "tipo":
        elegido = _normalizar(texto or "").strip()
        tipos = registro.tipos_disponibles(datos["entrada"], datos["salida"])
        habitacion = None
        for tipo, habs in tipos.items():
            if _normalizar(tipo) == elegido:
                habitacion = habs[0]
                break
        if habitacion is None:
            lineas = ["Ese tipo no está libre en esas fechas. Elige uno de estos:"]
            for tipo, habs in sorted(tipos.items()):
                desde = min(float(h.precio_noche) for h in habs)
                lineas.append(f"• {tipo} — desde ${desde:,.2f}/noche")
            return ("\n".join(lineas), False, None)
        datos["habitacion"] = habitacion
        noches = (datos["salida"] - datos["entrada"]).days
        total = noches * float(habitacion.precio_noche)
        datos["total"] = total
        estado["paso"] = "confirmar"
        return (
            "📋 <b>Tu reservación</b>\n"
            f"👤 {datos['nombre']}\n"
            f"🏨 Habitación {habitacion.numero} ({habitacion.tipo}) — ${float(habitacion.precio_noche):,.2f}/noche\n"
            f"📅 {datos['entrada']} → {datos['salida']} ({noches} noches)\n"
            f"💰 Total: ${total:,.2f} MXN\n\n"
            "¿Confirmo la reservación? Escribe <b>SI</b> para confirmar o <b>NO</b> para cancelar.",
            False, None,
        )

    if paso == "confirmar":
        respuesta = _normalizar(texto or "").strip()
        if respuesta in ("si", "confirmar", "confirmo", "si confirmo"):
            try:
                huesped = registro.registrar_huesped(
                    datos["nombre"], datos["contacto"],
                    telegram_user_id=datos.get("telegram_user_id"),
                )
                habitacion = datos["habitacion"]
                reserva = registro.crear_reservacion(
                    huesped.id, habitacion.id, datos["entrada"], datos["salida"]
                )
            except ValueError as exc:
                estado["paso"] = "fechas"
                return (f"😔 No se pudo confirmar: {exc}\nPrueba con otras fechas.", False, None)
            creado = {"reserva": reserva, "habitacion": habitacion, "huesped": huesped}
            return (
                "✅ <b>¡Reservación confirmada!</b>\n\n"
                f"🔖 Tu referencia es: <b>{referencia_reserva(reserva.id)}</b>\n"
                "Guárdala, con ella confirmas tu reservación en recepción.\n\n"
                f"👤 {huesped.nombre}\n"
                f"🏨 Habitación {habitacion.numero} ({habitacion.tipo})\n"
                f"📅 {datos['entrada']} → {datos['salida']} ({reserva.noches()} noches)\n"
                f"💰 Total: ${float(reserva.total):,.2f} MXN\n\n"
                "¿Quieres consultar tu reservación? Escríbeme /consultar "
                f"{referencia_reserva(reserva.id)}",
                True, creado,
            )
        if respuesta in ("no", "cancelar"):
            return ("❌ Reservación cancelada. Si quieres hacer otra, escribe /reservar.", True, None)
        return ("¿Confirmo la reservación? Escribe SI para confirmar o NO para cancelar.", False, None)

    estado["paso"] = "nombre"
    return ("Vamos a hacer tu reservación. ¿Cómo te llamas? (nombre completo)", False, None)


AYUDA = (
    "🏨 <b>Turitec — chatbot del hotel</b>\n\n"
    "Consulta habitaciones, precios y disponibilidad:\n"
    "/habitaciones — todas las habitaciones, con precio y si están libres u ocupadas hoy\n"
    "/precios — precios por tipo de habitación\n"
    "/disponibilidad [AAAA-MM-DD AAAA-MM-DD] — habitaciones libres y ocupadas en ese rango (por defecto, hoy y mañana)\n"
    "/reservar — hacer una reservación paso a paso (te da una referencia TUR-000000 para confirmarla)\n"
    "/consultar TUR-000000 — ver tu reservación con su referencia\n"
    "/cancelar — cancelar la reservación que estás haciendo\n"
    "/ayuda — ver esta lista\n\n"
    "💬 También puedes escribirlo sin la barra, como lo dirías: "
    "habitaciones, precios, disponibilidad, quiero reservar o ¿hay habitaciones libres?\n\n"
    "Comandos del personal: /reservas, /ocupacion."
)

DENEGADO_ADMIN = (
    "🔒 Este comando es solo para el personal del hotel.\n"
    "Para consultar habitaciones y precios usa /habitaciones, /precios o /disponibilidad."
)

AYUDA_ADMIN = (
    "🔐 <b>Comandos del personal</b> (solo este chat los ve):\n"
    "/reservas — reservaciones activas\n"
    "/ocupacion [AAAA-MM] — ocupación e ingresos del mes\n"
    "/ventas — ventas de caja de hoy\n"
    "/facturas — últimas facturas emitidas\n"
    "/corte — corte del día (ventas y efectivo neto en caja)\n"
    "/miid — ver tu id de chat y de usuario (para configurar el personal)\n"
    "/salir — cerrar sesión"
)

# Sesión del personal en el chat: 12 horas; después hay que volver a
# entrar con teléfono, correo y contraseña.
SESION_ADMIN_HORAS = 12

MENU_ROL = (
    "🏨 <b>Bienvenido a Turitec</b>\n\n"
    "¿Quién eres?\n\n"
    "👤 <b>Cliente</b> — consulta habitaciones, precios y disponibilidad, "
    "y haz tu reservación. Solo verás lo que tú generes.\n"
    "🔐 <b>Personal del hotel</b> — entra con tu teléfono, correo y "
    "contraseña para ver reservaciones, ventas, facturas y ocupación.\n\n"
    "Toca un botón o escríbelo: <b>Cliente</b> o <b>Personal</b>."
)

TECLADO_ROL = [[{"text": "👤 Soy cliente"}, {"text": "🔐 Soy del personal"}]]


def _parse_fecha(texto: str) -> date:
    """Acepta 2026-11-12, 12/11/2026 y 12-11-2026."""
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto.strip(), formato).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {texto}")


def _extraer_fechas(texto: str) -> list[date]:
    """Saca las fechas que vengan en un mensaje, en cualquier formato aceptado."""
    candidatos = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}-\d{1,2}-\d{4}", texto)
    fechas = []
    for c in candidatos:
        try:
            fechas.append(_parse_fecha(c))
        except ValueError:
            pass
    return fechas


def _normalizar(texto: str) -> str:
    """Minúsculas sin acentos ni signos, para entender mensajes sin slash."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9\- ]", " ", sin_acentos)


def _comando_desde_texto(texto: str) -> tuple[str, list[str]] | None:
    """Traduce un mensaje normal (sin /) al comando que le corresponde.

    Ejemplos: "habitaciones" -> /habitaciones, "cuanto cuesta" -> /precios,
    "hay habitaciones libres?" -> /disponibilidad, "hola" -> /ayuda.
    Devuelve None si no se entiende el mensaje.
    """
    normal = _normalizar(texto)
    fechas = re.findall(r"\d{4}-\d{2}-\d{2}", texto)
    if "cancelar" in normal:
        return ("/cancelar", [])
    if "salir" in normal or "cerrar sesion" in normal:
        return ("/salir", [])
    if "soy cliente" in normal or normal.strip() == "cliente":
        return ("/cliente", [])
    if "soy del personal" in normal or "soy administrador" in normal or normal.strip() in ("personal", "administrador", "admin"):
        return ("/personal", [])
    if "consultar" in normal or "mi reserva" in normal or re.search(r"tur-\d+", normal):
        return ("/consultar", re.findall(r"tur-\d+", normal))
    if "reservar" in normal or "quiero una habitacion" in normal or "quiero reservar" in normal:
        return ("/reservar", [])
    if "vend" in normal:
        return ("/ventas", [])
    if "factura" in normal:
        return ("/facturas", [])
    if "corte" in normal or "cierre" in normal:
        return ("/corte", [])
    if "precio" in normal or "cuanto cuesta" in normal or "cuesta" in normal or "tarifa" in normal:
        return ("/precios", [])
    if "disponibilidad" in normal or "disponible" in normal or "libre" in normal:
        return ("/disponibilidad", fechas)
    if "ocupacion" in normal:
        return ("/ocupacion", [])
    if "ocupad" in normal:
        return ("/habitaciones", [])
    if "habitacion" in normal or "cuarto" in normal:
        return ("/habitaciones", [])
    if "reserva" in normal:
        return ("/reservas", [])
    if any(p in normal for p in ("ayuda", "menu", "hola", "buenas", "buenos dias", "start", "inicio")):
        return ("/start", [])
    return None


RESERVAR_INFO = (
    "📩 ¡Perfecto! Vamos a hacer tu reservación.\n"
    "¿Cómo te llamas? Escríbeme tu nombre completo."
)


def responder(texto: str, consulta: ConsultaTuritec, es_admin: bool = True) -> str:
    """Devuelve la respuesta del bot a un mensaje. Función pura (sin red).

    Se prueba en tests/test_telegram.py sin Telegram ni base de datos real.
    ``es_admin`` es True solo para el chat del personal (TELEGRAM_CHAT_ID):
    /reservas y /ocupacion muestran datos de huéspedes e ingresos, así que
    los clientes no pueden usarlos.
    """
    partes = (texto or "").strip().split()
    if not partes:
        return AYUDA
    if partes[0].startswith("/"):
        comando = partes[0].split("@")[0].lower()
        args = partes[1:]
    else:
        # Mensaje normal, sin slash: se entiende por palabras clave.
        traducido = _comando_desde_texto(texto)
        if traducido is None:
            return "No entendí eso. 😅\n\n" + AYUDA
        comando, args = traducido

    if comando in ("/start", "/ayuda", "/help"):
        if es_admin:
            return AYUDA + "\n\n" + AYUDA_ADMIN
        return AYUDA

    if comando == "/reservar":
        return RESERVAR_INFO

    if comando == "/cancelar":
        return "No tienes ninguna reservación en curso. Si quieres hacer una, escribe /reservar."

    if comando == "/consultar":
        if args:
            return "Para consultar esa reservación escríbeme su referencia así: " + args[0].upper()
        return "Escríbeme la referencia de tu reservación así: /consultar TUR-000001"

    if comando == "/precios":
        grupos = consulta.precios_por_tipo()
        if not grupos:
            return "No hay habitaciones registradas."
        lineas = ["💰 <b>Precios por noche</b>"]
        for g in grupos:
            lineas.append(
                f"• {g['tipo']} — desde ${g['desde']:,.2f} MXN "
                f"(habitaciones {', '.join(g['numeros'])})"
            )
        lineas.append("\nEscribe /disponibilidad para ver cuáles están libres.")
        return "\n".join(lineas)

    if comando == "/habitaciones":
        habitaciones = consulta.estado_hoy()
        if not habitaciones:
            return "No hay habitaciones registradas."
        lineas = ["🏨 <b>Habitaciones — hoy</b>"]
        for h in habitaciones:
            estado = "✅ Libre" if h["disponible"] else "🔴 Ocupada"
            lineas.append(
                f"• {h['numero']} ({h['tipo']}) — ${h['precio_noche']:,.2f}/noche — {estado}"
            )
        lineas.append("\nEscribe /disponibilidad 2026-11-12 2026-11-14 para consultar otras fechas.")
        return "\n".join(lineas)

    if comando == "/disponibilidad":
        try:
            inicio = _parse_fecha(args[0]) if len(args) > 0 else date.today()
            fin = _parse_fecha(args[1]) if len(args) > 1 else date.today() + timedelta(days=1)
        except ValueError:
            return "Fechas inválidas. Usa: /disponibilidad 2026-11-10 2026-11-12"
        if fin <= inicio:
            return "La fecha de salida debe ser después de la de entrada."
        todas = consulta.disponibilidad(inicio, fin)
        libres = [h for h in todas if h["disponible"]]
        ocupadas = [h for h in todas if not h["disponible"]]
        if not libres:
            return f"😔 No hay habitaciones libres del {inicio} al {fin}."
        lineas = [f"✅ <b>Libres del {inicio} al {fin}</b>"]
        for h in libres:
            lineas.append(f"• Habitación {h['numero']} ({h['tipo']}) — ${h['precio_noche']:,.2f}/noche")
        if ocupadas:
            lineas.append(f"\n🔴 <b>Ocupadas en esas fechas</b>")
            for h in ocupadas:
                lineas.append(f"• Habitación {h['numero']} ({h['tipo']}) — ${h['precio_noche']:,.2f}/noche")
        return "\n".join(lineas)

    if comando == "/reservas":
        if not es_admin:
            return DENEGADO_ADMIN
        activas = consulta.reservas_activas()
        if not activas:
            return "No hay reservaciones activas."
        lineas = ["📋 <b>Reservaciones activas</b>"]
        for r in sorted(activas, key=lambda x: x.fecha_entrada)[:20]:
            lineas.append(
                f"• #{r.id} — Hab. {r.habitacion_id} — {r.fecha_entrada} → {r.fecha_salida} — {r.estado}"
            )
        return "\n".join(lineas)

    if comando == "/ocupacion":
        if not es_admin:
            return DENEGADO_ADMIN
        try:
            if args:
                anio, mes = (int(p) for p in args[0].split("-"))
            else:
                hoy = date.today()
                anio, mes = hoy.year, hoy.month
        except ValueError:
            return "Mes inválido. Usa: /ocupacion 2026-11"
        rep = consulta.ocupacion(anio, mes)
        return (
            f"📊 <b>Ocupación {anio}-{mes:02d}</b>\n"
            f"Cuartos ocupados: {rep['cuartos_ocupados']} de {rep['total_habitaciones']}\n"
            f"Noches ocupadas: {rep['noches_ocupadas']} de {rep['capacidad_noches']}\n"
            f"Tasa de ocupación: {rep['tasa_ocupacion']}%\n"
            f"Ingresos facturados: ${rep['ingresos']:,.2f} MXN"
        )

    if comando == "/ventas":
        if not es_admin:
            return DENEGADO_ADMIN
        ventas = consulta.ventas_hoy()
        if not ventas:
            return "Hoy no hay ventas de caja todavía."
        total = sum(float(v.total) for v in ventas)
        por_metodo: dict[str, float] = {}
        for v in ventas:
            por_metodo[v.metodo_pago] = por_metodo.get(v.metodo_pago, 0.0) + float(v.total)
        lineas = [f"💵 <b>Ventas de hoy</b> — {len(ventas)} ventas, total: ${total:,.2f} MXN"]
        for metodo, monto in sorted(por_metodo.items()):
            lineas.append(f"• {metodo}: ${monto:,.2f}")
        lineas.append("")
        for v in sorted(ventas, key=lambda x: x.fecha or datetime.min, reverse=True)[:5]:
            lineas.append(f"• {v.folio} — {v.cliente} — ${float(v.total):,.2f} — {v.metodo_pago}")
        return "\n".join(lineas)

    if comando == "/facturas":
        if not es_admin:
            return DENEGADO_ADMIN
        facturas = consulta.facturas_recientes()
        if not facturas:
            return "No hay facturas emitidas."
        lineas = ["🧾 <b>Últimas facturas</b>"]
        for f in facturas:
            nombre = f.cliente or consulta.nombre_huesped(f.huesped_id)
            lineas.append(
                f"• {f.folio} — {nombre or '—'} — "
                f"{f.fecha_emision.strftime('%d/%m/%Y') if f.fecha_emision else ''} — "
                f"${float(f.total):,.2f} MXN"
            )
        return "\n".join(lineas)

    if comando == "/corte":
        if not es_admin:
            return DENEGADO_ADMIN
        corte = consulta.corte_hoy()
        if corte is None:
            return "El corte del día no está disponible."
        dia = corte["dia"]
        return (
            f"🧾 <b>Corte del día {dia.strftime('%d/%m/%Y')}</b>\n"
            f"Ventas: {corte['num_ventas']} — Total: ${corte['total_ventas']:,.2f} MXN\n"
            f"Efectivo: ${corte['efectivo']:,.2f} · Tarjeta: ${corte['tarjeta']:,.2f} · "
            f"Transferencia: ${corte['transferencia']:,.2f}\n"
            f"Efectivo recibido: ${corte['recibido_efectivo']:,.2f} — "
            f"Cambio: ${corte['cambio_total']:,.2f}\n"
            f"💵 <b>Efectivo neto en caja: ${corte['efectivo_neto']:,.2f} MXN</b>\n"
            f"Facturas hoy: {corte['num_facturas']} — ${corte['total_facturas']:,.2f} MXN"
        )

    return f"No conozco ese comando. Escribe /ayuda para ver la lista."


def _detectar_comando(texto: str) -> tuple[str | None, list[str]]:
    """Detecta el comando de un mensaje, con o sin slash."""
    partes = (texto or "").strip().split()
    if not partes:
        return (None, [])
    if partes[0].startswith("/"):
        return (partes[0].split("@")[0].lower(), partes[1:])
    traducido = _comando_desde_texto(texto)
    return traducido if traducido is not None else (None, [])


class TuritecBot:
    """Escucha mensajes de Telegram (getUpdates) y responde con responder().

    ``app`` es la aplicación Flask: cada mensaje se responde dentro de su
    contexto, porque las consultas a la base de datos lo necesitan. Sin
    eso, responder daba "Working outside of application context".
    ``registro`` permite hacer reservaciones desde el chat; sin él, el
    bot solo consulta. ``conversaciones`` guarda en qué paso de la
    reservación va cada chat. ``sesiones`` guarda el login de cada chat:
    el cliente solo usa consultas simples y solo ve lo que él genera;
    el personal entra con teléfono, correo y contraseña (verificados
    contra los usuarios de la app web) y ve todo.
    """

    def __init__(self, notificador: TelegramNotificador, consulta: ConsultaTuritec, app=None, registro: RegistroReservas | None = None, usuarios=None):
        self.notificador = notificador
        self.consulta = consulta
        self.app = app
        self.registro = registro
        self.usuarios = usuarios  # repositorio de usuarios (para el acceso del personal)
        self.conversaciones: dict[int, dict] = {}
        self.offset = 0
        # Updates ya procesados: si Telegram entrega el mismo dos veces
        # (o corre otro bot con el mismo token), no se contesta dos veces.
        self._updates_vistos: deque[int] = deque(maxlen=200)
        # Sesiones del chat: {"rol": "cliente"|"admin", "usuario": UsuarioTelegram,
        # "admin_email": str|None, "telefono": str|None, "expira": datetime|None}.
        # El cliente solo ve lo que él genera; el personal entra con
        # teléfono, correo y contraseña y ve todo.
        self.sesiones: dict[int, dict] = {}
        # Acceso del personal en curso: {"paso": "telefono"|"correo"|"contrasena",
        # "telefono": str, "correo": str, "intentos": int}.
        self.login_en_curso: dict[int, dict] = {}

    # ---------------- Sesiones (login del chat) ----------------

    def _rol_sesion(self, chat_id: int) -> str | None:
        """Rol de la sesión activa del chat (None si no hay o caducó)."""
        sesion = self.sesiones.get(chat_id)
        if sesion is None:
            return None
        expira = sesion.get("expira")
        if expira is not None and datetime.now() > expira:
            self.sesiones.pop(chat_id, None)
            return None
        return sesion["rol"]

    def _iniciar_sesion_cliente(self, chat_id: int, usuario: UsuarioTelegram) -> str:
        self.sesiones[chat_id] = {
            "rol": "cliente", "usuario": usuario,
            "admin_email": None, "telefono": None, "expira": None,
        }
        saludo = f"👋 ¡Hola, {usuario.nombre}! " if usuario.nombre else "👋 ¡Hola! "
        return saludo + "Ya puedes consultar el hotel. Solo verás lo que tú generes.\n\n" + AYUDA

    def _iniciar_login_personal(self, chat_id: int) -> str:
        self.login_en_curso[chat_id] = {"paso": "telefono", "telefono": "", "correo": "", "intentos": 0}
        return (
            "🔐 <b>Acceso del personal</b>\n\n"
            "Escribe tu número de teléfono (el del personal del hotel):"
        )

    def _avanzar_login(self, chat_id: int, texto: str, usuario: UsuarioTelegram) -> str:
        """Un paso del acceso del personal: teléfono → correo → contraseña."""
        estado = self.login_en_curso[chat_id]
        paso = estado["paso"]
        if paso == "telefono":
            digitos = "".join(c for c in (texto or "") if c.isdigit())
            if len(digitos) < 7:
                return "Ese teléfono no parece válido. Escríbelo con al menos 7 dígitos (ejemplo: 9611234567)."
            estado["telefono"] = (texto or "").strip()
            estado["paso"] = "correo"
            return "📧 Ahora escribe tu correo (el mismo de la app web):"
        if paso == "correo":
            correo = (texto or "").strip().lower()
            if "@" not in correo or "." not in correo:
                return "Ese correo no parece válido. Escríbelo así: admin@turitec.mx"
            estado["correo"] = correo
            estado["paso"] = "contrasena"
            return "🔑 Ahora escribe tu contraseña (la misma de la app web):"
        # Contraseña: se verifica contra los usuarios de la app web.
        if self.usuarios is None:
            self.login_en_curso.pop(chat_id, None)
            return "El acceso del personal no está disponible en este momento."
        admin = self.usuarios.obtener_por_email(estado["correo"])
        if (
            admin is None or not admin.activo or admin.rol not in ("administrador", "recepcion")
            or not check_password_hash(admin.password_hash, texto or "")
        ):
            estado["intentos"] += 1
            if estado["intentos"] >= 3:
                self.login_en_curso.pop(chat_id, None)
                return (
                    "❌ Correo o contraseña incorrectos (3 intentos).\n"
                    "Se canceló el acceso. Si eres del personal, escribe /start y vuelve a intentarlo."
                )
            return (
                f"❌ Correo o contraseña incorrectos (intento {estado['intentos']} de 3).\n"
                "Inténtalo de nuevo con tu contraseña, o escribe /cancelar para salir."
            )
        self.login_en_curso.pop(chat_id, None)
        self.sesiones[chat_id] = {
            "rol": "admin", "usuario": usuario,
            "admin_email": admin.email, "telefono": estado["telefono"],
            "expira": datetime.now() + timedelta(hours=SESION_ADMIN_HORAS),
        }
        return (
            f"🔓 ¡Hola, {admin.nombre}! Entraste como personal.\n"
            "Tu teléfono quedó registrado como identificación; la contraseña es la que "
            "verifica que eres del personal (la misma de la app web).\n\n"
            + AYUDA + "\n\n" + AYUDA_ADMIN
        )

    def _avanzar_conversacion(self, chat_id: int, texto: str, usuario: UsuarioTelegram | None = None) -> str:
        """Avanza la reservación en curso de un chat y avisa al personal si se confirma."""
        estado = self.conversaciones[chat_id]
        respuesta, terminado, creado = avanzar_reserva(estado, texto, self.registro)
        if terminado:
            self.conversaciones.pop(chat_id, None)
        if creado is not None and self.notificador.chat_id and str(self.notificador.chat_id) != str(chat_id):
            reserva = creado["reserva"]
            try:
                self.notificador.enviar(formatear_reservacion(
                    reserva.id, creado["habitacion"].numero, creado["huesped"].nombre,
                    reserva.fecha_entrada, reserva.fecha_salida, reserva.noches(), float(reserva.total),
                    telegram_usuario=usuario.descripcion() if usuario else "",
                ))
            except Exception:  # noqa: BLE001 - el aviso al personal no debe tumbar la confirmación
                pass
        return respuesta

    def _consultar(self, referencia: str, usuario: UsuarioTelegram | None = None, es_admin: bool = False) -> str:
        if self.registro is None:
            return "La consulta de reservaciones no está disponible en este momento."
        if not referencia:
            return "Escríbeme la referencia de tu reservación así: /consultar TUR-000001"
        datos = self.registro.consultar(referencia)
        if datos is None:
            return f"No encontré ninguna reservación con la referencia {referencia.upper()}."
        if not es_admin and usuario is not None and usuario.user_id is not None:
            # Un cliente solo ve las reservaciones que él hizo desde este
            # chat: si la reservación es de otra persona, no se muestra.
            huesped = datos["huesped"]
            vinculado = huesped.telegram_user_id if huesped else None
            if vinculado != usuario.user_id:
                return (
                    "🔒 Esa reservación no es tuya.\n"
                    "Solo puedes consultar las reservaciones que hiciste tú desde este chat."
                )
        r = datos["reserva"]
        h = datos["habitacion"]
        hu = datos["huesped"]
        return (
            f"📋 <b>Reservación {referencia_reserva(r.id)}</b>\n"
            f"👤 {hu.nombre if hu else ''}\n"
            f"🏨 Habitación {h.numero if h else r.habitacion_id} ({h.tipo if h else ''})\n"
            f"📅 {r.fecha_entrada} → {r.fecha_salida} ({r.noches()} noches)\n"
            f"💰 Total: ${float(r.total):,.2f} MXN\n"
            f"Estado: {r.estado}"
        )

    def procesar_update(self, update: dict) -> bool:
        """Procesa un update de getUpdates. Devuelve True si respondió algo.

        Primero el login: quien escribe elige si es cliente o personal.
        El cliente solo usa consultas simples y solo ve lo que él genera;
        el personal entra con teléfono, correo y contraseña y ve todo.
        """
        update_id = update.get("update_id")
        if update_id is not None:
            if update_id in self._updates_vistos:
                return False  # mismo update dos veces: no contestar dos veces
            self._updates_vistos.append(update_id)
        mensaje = update.get("message") or {}
        chat = mensaje.get("chat") or {}
        texto = mensaje.get("text", "")
        if not texto or not chat.get("id"):
            return False
        chat_id = chat["id"]
        # Quién escribe: se usa para identificar al cliente en los avisos
        # al personal y para que cada quien sepa su id con /miid.
        usuario = identidad_desde_mensaje(mensaje)
        teclado: list | None = None

        def calcular_respuesta() -> str:
            nonlocal teclado
            comando, args = _detectar_comando(texto)
            if comando == "/miid":
                return (
                    "🪪 <b>Tu identidad en Telegram</b>\n"
                    f"Chat: <code>{chat_id}</code>\n"
                    f"Usuario: {usuario.descripcion()}\n\n"
                    "Si tú eres la administradora, pon ese número de chat en el "
                    "archivo .env como TELEGRAM_CHAT_ID."
                )
            # Acceso del personal en curso: teléfono → correo → contraseña.
            if chat_id in self.login_en_curso:
                if comando == "/cancelar":
                    self.login_en_curso.pop(chat_id, None)
                    return "Acceso cancelado. Si quieres intentarlo de nuevo, escribe /start."
                return self._avanzar_login(chat_id, texto, usuario)
            # Cerrar sesión (con o sin sesión).
            if comando == "/salir":
                self.sesiones.pop(chat_id, None)
                self.conversaciones.pop(chat_id, None)
                self.login_en_curso.pop(chat_id, None)
                teclado = TECLADO_ROL
                return "👋 Sesión cerrada.\n\n" + MENU_ROL
            # Elegir rol: cliente o personal.
            if comando in ("/start", "/cliente", "/personal", "/login"):
                rol = self._rol_sesion(chat_id)
                if comando == "/start" and rol == "admin":
                    return AYUDA + "\n\n" + AYUDA_ADMIN
                if comando == "/start" and rol == "cliente":
                    return AYUDA
                if comando == "/cliente":
                    return self._iniciar_sesion_cliente(chat_id, usuario)
                if comando in ("/personal", "/login"):
                    return self._iniciar_login_personal(chat_id)
                teclado = TECLADO_ROL
                return MENU_ROL
            # Sin sesión: primero hay que elegir quién eres.
            rol = self._rol_sesion(chat_id)
            if rol is None:
                teclado = TECLADO_ROL
                return MENU_ROL
            es_admin = rol == "admin"
            if comando == "/cancelar":
                if chat_id in self.conversaciones:
                    self.conversaciones.pop(chat_id, None)
                    return "❌ Reservación cancelada. Si quieres hacer otra, escribe /reservar."
                return "No tienes ninguna reservación en curso. Si quieres hacer una, escribe /reservar."
            if chat_id in self.conversaciones:
                if texto.strip().startswith("/") and comando != "/reservar":
                    # Un comando con slash abandona la reservación en curso.
                    self.conversaciones.pop(chat_id, None)
                else:
                    return self._avanzar_conversacion(chat_id, texto, usuario)
            if comando == "/reservar":
                if self.registro is None:
                    return "Las reservaciones por Telegram no están activas en este momento."
                self.conversaciones[chat_id] = {
                    "paso": "nombre",
                    "datos": {"telegram_user_id": usuario.user_id},
                }
                return RESERVAR_INFO
            if comando == "/consultar":
                return self._consultar(args[0] if args else "", usuario, es_admin=es_admin)
            return responder(texto, self.consulta, es_admin=es_admin)

        if self.app is not None:
            with self.app.app_context():
                respuesta = calcular_respuesta()
        else:
            respuesta = calcular_respuesta()
        return self.notificador.enviar_a(chat_id, respuesta, teclado)

    def leer_updates(self) -> list[dict]:
        if not self.notificador.token:
            return []
        try:
            # getUpdates espera hasta 30 s si no hay mensajes nuevos; por
            # eso la espera de red es mayor (40 s). Que se acabe el tiempo
            # sin mensajes es normal: no es un error, solo no hay nada.
            return self.notificador._post(
                "getUpdates", {"offset": self.offset, "timeout": 30}, timeout=40
            ).get("result", [])
        except OSError as exc:
            # Timeout de lectura, red caída, etc.: no hay mensajes nuevos
            # que procesar; se reintenta en el siguiente ciclo sin ruido.
            logger.debug("Sin mensajes nuevos en Telegram: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error leyendo updates de Telegram: %s", exc)
            return []

    def correr(self) -> None:
        """Bucle de escucha. Se detiene con Ctrl+C."""
        if not self.notificador.token:
            raise SystemExit(
                "Falta TELEGRAM_BOT_TOKEN. Ponlo en el archivo .env "
                "(ver README, sección Chatbot de Telegram) y vuelve a intentarlo."
            )
        print("🤖 Turitec Bot escuchando en Telegram. Pulsa Ctrl+C para detenerlo.")
        print(f"   Chat del personal (avisos): {self.notificador.chat_id or '(sin configurar)'}")
        print("   Quien escribe elige primero si es cliente o personal; el personal")
        print("   entra con teléfono, correo y contraseña (los de la app web).")
        print("   Clientes: solo consultas simples y solo ven lo que ellos generan.")
        # El bucle entero corre dentro del contexto de la app, para que
        # ninguna consulta a la base de datos falle por falta de contexto.
        ctx = self.app.app_context() if self.app is not None else None
        if ctx is not None:
            ctx.push()
        try:
            while True:
                for update in self.leer_updates():
                    self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
                    try:
                        self.procesar_update(update)
                    except Exception as exc:  # noqa: BLE001 - un mensaje no debe tumbar el bot
                        logger.warning("Error procesando un mensaje: %s", exc)
                time.sleep(1)
        finally:
            if ctx is not None:
                ctx.pop()

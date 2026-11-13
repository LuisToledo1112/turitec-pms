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
   mensajes (long polling con getUpdates) y responde comandos.
   Comandos **para clientes** (cualquiera puede usarlos):
   /start, /ayuda, /habitaciones, /disponibilidad, /precios.
   Comandos **solo para el personal** (solo el chat de
   TELEGRAM_CHAT_ID): /reservas, /ocupacion. Así los clientes
   pueden consultar habitaciones, precios y si están libres u
   ocupadas, pero no ven los datos de otros huéspedes ni los ingresos.
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
from datetime import date, datetime, timedelta

from ..application.use_cases import ReporteOcupacionMensual
from ..domain.ports import NotificacionPort

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

    def enviar_a(self, chat_id: str | int, mensaje: str) -> bool:
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
            resultado = self._post("sendMessage", {
                "chat_id": str(chat_id),
                "text": mensaje,
                "parse_mode": "HTML",
            })
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
                          entrada, salida, noches: int, total: float) -> str:
    return (
        f"🛎️ <b>Nueva reservación #{reserva_id}</b>\n"
        f"🏨 Habitación {habitacion_numero}\n"
        f"👤 {huesped_nombre}\n"
        f"📅 {entrada} → {salida} ({noches} noches)\n"
        f"💰 Total: ${total:,.2f} MXN"
    )


def formatear_check_in(reserva_id: int, habitacion_numero: str, huesped_nombre: str) -> str:
    return f"🔑 <b>Check-in</b> — Reservación #{reserva_id}\n🏨 Habitación {habitacion_numero}\n👤 {huesped_nombre}"


def formatear_check_out(reserva_id: int, habitacion_numero: str, huesped_nombre: str) -> str:
    return f"🚪 <b>Check-out</b> — Reservación #{reserva_id}\n🏨 Habitación {habitacion_numero}\n👤 {huesped_nombre}\nYa se puede generar su factura."


def formatear_cancelacion(reserva_id: int, habitacion_numero: str) -> str:
    return f"❌ <b>Reservación #{reserva_id} cancelada</b>\n🏨 Habitación {habitacion_numero} vuelve a estar libre."


def formatear_factura(folio: str, reserva_id: int, huesped_nombre: str, total: float) -> str:
    return (
        f"🧾 <b>Factura {folio} generada</b>\n"
        f"Reservación #{reserva_id} — {huesped_nombre}\n"
        f"💰 Total: ${total:,.2f} MXN (IVA 16% incluido)"
    )


# ---------------- Chatbot: consultas y respuestas ----------------

class ConsultaTuritec:
    """Lee los datos que el chatbot necesita, usando los repositorios.

    Es el adaptador de lectura del bot: no escribe nada en la base de datos.
    """

    def __init__(self, habitaciones, reservaciones, facturas):
        self.habitaciones = habitaciones
        self.reservaciones = reservaciones
        self.facturas = facturas

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


AYUDA = (
    "🏨 <b>Turitec — chatbot del hotel</b>\n\n"
    "Consulta habitaciones, precios y disponibilidad:\n"
    "/habitaciones — todas las habitaciones, con precio y si están libres u ocupadas hoy\n"
    "/precios — precios por tipo de habitación\n"
    "/disponibilidad [AAAA-MM-DD AAAA-MM-DD] — habitaciones libres y ocupadas en ese rango (por defecto, hoy y mañana)\n"
    "/ayuda — ver esta lista\n\n"
    "💬 También puedes escribirlo sin la barra, como lo dirías: "
    "habitaciones, precios, disponibilidad o ¿hay habitaciones libres?\n\n"
    "Para reservar, escribe al hotel o llama a recepción.\n"
    "Comandos del personal: /reservas, /ocupacion."
)

DENEGADO_ADMIN = (
    "🔒 Este comando es solo para el personal del hotel.\n"
    "Para consultar habitaciones y precios usa /habitaciones, /precios o /disponibilidad."
)


def _parse_fecha(texto: str) -> date:
    return datetime.strptime(texto, "%Y-%m-%d").date()


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
    if "reservar" in normal or "quiero una habitacion" in normal or "quiero reservar" in normal:
        return ("/reservar", [])
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
    "📩 Para reservar, escríbenos por aquí con las fechas que quieres "
    "y el tipo de habitación (Sencilla, Doble o Suite), o llama a recepción.\n"
    "Primero puedes ver precios con /precios y disponibilidad con /disponibilidad."
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
        return AYUDA

    if comando == "/reservar":
        return RESERVAR_INFO

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

    return f"No conozco ese comando. Escribe /ayuda para ver la lista."


class TuritecBot:
    """Escucha mensajes de Telegram (getUpdates) y responde con responder().

    ``app`` es la aplicación Flask: cada mensaje se responde dentro de su
    contexto, porque las consultas a la base de datos lo necesitan. Sin
    eso, responder daba "Working outside of application context".
    """

    def __init__(self, notificador: TelegramNotificador, consulta: ConsultaTuritec, app=None):
        self.notificador = notificador
        self.consulta = consulta
        self.app = app
        self.offset = 0

    def procesar_update(self, update: dict) -> bool:
        """Procesa un update de getUpdates. Devuelve True si respondió algo."""
        mensaje = update.get("message") or {}
        chat = mensaje.get("chat") or {}
        texto = mensaje.get("text", "")
        if not texto or not chat.get("id"):
            return False
        # Comandos para clientes: cualquiera puede usarlos. Comandos del
        # personal (/reservas, /ocupacion): solo el chat configurado en
        # TELEGRAM_CHAT_ID; responder() les niega el acceso a los demás.
        admin = self.notificador.chat_id
        es_admin = bool(admin) and str(chat["id"]) == str(admin)
        if self.app is not None:
            with self.app.app_context():
                respuesta = responder(texto, self.consulta, es_admin=es_admin)
        else:
            respuesta = responder(texto, self.consulta, es_admin=es_admin)
        return self.notificador.enviar_a(chat["id"], respuesta)

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
        print(f"   Chat del personal (avisos y comandos /reservas, /ocupacion): {self.notificador.chat_id or '(sin configurar: esos comandos quedan bloqueados)'}")
        print("   Clientes: pueden usar /habitaciones, /precios y /disponibilidad.")
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

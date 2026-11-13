// Servicio Node.js de sincronización con canales externos (Turitec PMS).
// NO es necesario para que la app Python corra. Es un servicio aparte que:
//  1) lee la disponibilidad desde la API de Flask, y
//  2) simula empujarla a Booking y Airbnb (sin llamadas reales, solo log).
// Uso:
//   node sync.js                 (una pasada, fechas por defecto: nov 2026)
//   node sync.js 2026-11-01 2026-11-30
// Variables de entorno: TURITEC_API_URL (por defecto http://127.0.0.1:5000)

const API_URL = process.env.TURITEC_API_URL || "http://127.0.0.1:5000";
const CANALES = ["booking", "airbnb"];

async function main() {
  const fechaInicio = process.argv[2] || "2026-11-01";
  const fechaFin = process.argv[3] || "2026-11-30";
  console.log(`[Turitec Sync] Consultando disponibilidad ${fechaInicio} → ${fechaFin} en ${API_URL} ...`);
  const resp = await fetch(`${API_URL}/api/disponibilidad?fecha_inicio=${fechaInicio}&fecha_fin=${fechaFin}`);
  if (!resp.ok) {
    console.error(`[Turitec Sync] La API respondió ${resp.status}. ¿Está corriendo la app Python (python run.py)?`);
    process.exit(1);
  }
  const datos = await resp.json();
  for (const hab of datos.habitaciones) {
    for (const canal of CANALES) {
      // Simulación: en producción aquí iría la llamada real a la API del canal.
      console.log(`[Turitec Sync] ${canal}: habitación ${hab.numero} (${hab.tipo}) → ${hab.disponible ? "DISPONIBLE" : "NO DISPONIBLE"} (simulado)`);
    }
  }
  console.log(`[Turitec Sync] Listo: ${datos.habitaciones.length} habitaciones sincronizadas (simulación) con ${CANALES.join(" y ")}.`);
}

main().catch((err) => {
  console.error("[Turitec Sync] Error:", err.message);
  process.exit(1);
});

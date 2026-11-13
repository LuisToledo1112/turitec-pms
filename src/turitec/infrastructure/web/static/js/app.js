// Turitec PMS — interactividad y animaciones (sin frameworks)
// 1) Toasts: desaparecen solos a los 6 segundos
// 2) Modal de confirmación para formularios con data-confirm
// 3) Validación de fechas en la reservación + consulta de disponibilidad
// 4) Barras del reporte: se animan al cargar

document.addEventListener("DOMContentLoaded", () => {
  // Toasts
  setTimeout(() => {
    document.querySelectorAll(".toast").forEach((t) => {
      t.style.transition = "opacity .6s";
      t.style.opacity = "0";
      setTimeout(() => t.remove(), 650);
    });
  }, 6000);

  // Confirmación antes de eliminar / cancelar
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Validación de fechas + disponibilidad en vivo (form de reservación)
  const form = document.querySelector("[data-validar-fechas]");
  if (form) {
    const entrada = document.getElementById("fecha_entrada");
    const salida = document.getElementById("fecha_salida");
    const habitacion = document.getElementById("habitacion_id");
    const aviso = document.getElementById("aviso-disponibilidad");

    async function revisar() {
      if (!entrada.value || !salida.value) return;
      if (salida.value <= entrada.value) {
        aviso.textContent = "⚠️ La fecha de salida debe ser posterior a la fecha de entrada.";
        return;
      }
      try {
        const r = await fetch(`/api/disponibilidad?fecha_inicio=${entrada.value}&fecha_fin=${salida.value}`);
        const datos = await r.json();
        const hab = datos.habitaciones.find((h) => String(h.id) === habitacion.value);
        if (hab) {
          aviso.textContent = hab.disponible
            ? `✅ Habitación ${hab.numero} disponible en esas fechas.`
            : `⚠️ La habitación ${hab.numero} NO está disponible en esas fechas. Elige otras fechas u otra habitación.`;
          aviso.style.color = hab.disponible ? "#2e7d32" : "#c62828";
        }
      } catch {
        aviso.textContent = "";
      }
    }
    [entrada, salida, habitacion].forEach((el) => el.addEventListener("change", revisar));
    form.addEventListener("submit", (e) => {
      if (salida.value <= entrada.value) {
        e.preventDefault();
        alert("La fecha de salida debe ser posterior a la fecha de entrada.");
      }
    });
    revisar();
  }

  // Barras animadas del reporte de ingresos por día
  document.querySelectorAll(".barra").forEach((barra, i) => {
    const alto = barra.style.getPropertyValue("--alto") || "0%";
    setTimeout(() => { barra.style.height = alto; }, 120 + i * 30);
  });
});

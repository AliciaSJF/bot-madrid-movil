// Tema claro / oscuro / automático. Se guarda en este dispositivo (localStorage).
// Se carga en <head> para aplicar el tema antes de pintar y evitar un destello.
(function () {
  const KEY = "tema";
  const MODES = ["auto", "light", "dark"];
  const LABELS = { auto: "Tema: automático", light: "Tema: claro", dark: "Tema: oscuro" };
  const THEME_COLORS = { light: "#0f766e", dark: "#111413" };

  function read() {
    try {
      const value = localStorage.getItem(KEY);
      return MODES.includes(value) ? value : "auto";
    } catch {
      return "auto";
    }
  }

  function save(mode) {
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      /* navegación privada: el tema dura hasta recargar */
    }
  }

  function apply(mode) {
    const root = document.documentElement;
    if (mode === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);

    const dark = mode === "dark" || (mode === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = dark ? THEME_COLORS.dark : THEME_COLORS.light;

    document.querySelectorAll(".theme-toggle").forEach((button) => {
      button.dataset.mode = mode;
      button.setAttribute("aria-label", `${LABELS[mode]}. Pulsa para cambiar`);
      button.title = LABELS[mode];
    });
  }

  apply(read());

  document.addEventListener("DOMContentLoaded", () => {
    apply(read());
    document.querySelectorAll(".theme-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const next = MODES[(MODES.indexOf(read()) + 1) % MODES.length];
        save(next);
        apply(next);
      });
    });
    // En automático, seguir los cambios del sistema en vivo
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => apply(read()));
  });
})();

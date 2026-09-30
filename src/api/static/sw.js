// Service worker mínimo para que la web se pueda instalar como app.
// Sin caché a propósito: los estados de las reservas tienen que ser siempre los actuales.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

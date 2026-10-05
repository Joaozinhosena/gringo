self.addEventListener("push", (event) => {
  let data = { title: "Agenda Gringo du corte", body: "Você tem uma atualização.", url: "/dashboard" };
  if (event.data) {
    try { data = event.data.json(); } catch (_) {}
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      data: { url: data.url || "/dashboard" }
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data.url || "/dashboard"));
});

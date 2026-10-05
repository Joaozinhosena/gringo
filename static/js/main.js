document.addEventListener("DOMContentLoaded", () => {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/js/sw.js").catch(() => {});
  }

  const pushButton = document.getElementById("enablePush");
  if (pushButton) {
    pushButton.addEventListener("click", async () => {
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          alert("Permissão de notificação não concedida.");
          return;
        }

        const registration = await navigator.serviceWorker.ready;
        const publicKey = pushButton.dataset.vapid;
        const subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey),
        });

        const response = await fetch("/api/push/subscribe", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
          },
          body: JSON.stringify(subscription.toJSON()),
        });

        if (!response.ok) throw new Error("Falha ao salvar assinatura");
        pushButton.textContent = "Notificações ativadas";
        pushButton.disabled = true;
      } catch (error) {
        alert("Não foi possível ativar as notificações neste navegador.");
      }
    });
  }
});

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
}

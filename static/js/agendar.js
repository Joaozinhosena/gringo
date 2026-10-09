(() => {
    if (window.__gringoAgendaProfissionalInicializada) {
        return;
    }
    window.__gringoAgendaProfissionalInicializada = true;

    const service = document.getElementById("service");
    const professional = document.getElementById("professional");
    const panel = document.getElementById("schedulePanel");
    const statusLabel = document.getElementById("statusLabel");
    const weekLabel = document.getElementById("weekLabel");
    const feedback = document.getElementById("bookingFeedback");
    const feedbackText = document.getElementById("bookingFeedbackText");
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');

    if (!service || !professional || !panel || !statusLabel || !weekLabel || !csrfMeta) {
        return;
    }

    const csrfToken = csrfMeta.content;
    let loadSequence = 0;
    let bookingInProgress = false;

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function loadingState() {
        panel.innerHTML = `
            <div class="rounded-3xl border border-dashed border-white/10 bg-black/10 p-8 text-center md:col-span-2 2xl:col-span-3">
                <div class="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-white/[.04] text-xl text-zinc-500">◷</div>
                <strong class="mt-4 block text-sm font-black text-zinc-400">Atualizando horários...</strong>
                <p class="mt-2 text-xs text-zinc-600">Consultando a agenda do profissional selecionado.</p>
            </div>
        `;
        statusLabel.innerHTML = '<span class="h-2 w-2 animate-pulse rounded-full bg-[#f4600d]"></span> Atualizando...';
    }

    function emptyState(message = "Nenhum horário disponível para esta combinação.") {
        panel.innerHTML = `
            <div class="rounded-3xl border border-dashed border-white/10 bg-black/10 p-8 text-center md:col-span-2 2xl:col-span-3">
                <div class="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-white/[.04] text-xl text-zinc-500">◷</div>
                <strong class="mt-4 block text-sm font-black text-zinc-400">Sem horários</strong>
                <p class="mt-2 text-xs text-zinc-600">${escapeHtml(message)}</p>
            </div>
        `;
        statusLabel.textContent = "Sem horários";
    }

    function errorState(message) {
        panel.innerHTML = `
            <div class="rounded-3xl border border-red-400/20 bg-red-400/5 p-6 text-sm text-red-200 md:col-span-2 2xl:col-span-3">
                <strong class="block font-black">Não foi possível carregar a agenda.</strong>
                <span class="mt-2 block text-red-200/70">${escapeHtml(message)}</span>
            </div>
        `;
        statusLabel.textContent = "Erro";
    }

    function renderPanel(data) {
        const days = Array.isArray(data.days) ? data.days : [];
        const proName = data.professional?.name || "Profissional";

        weekLabel.textContent = data.week
            ? `${data.week.start} até ${data.week.end} · ${proName}`
            : proName;

        if (!days.length) {
            emptyState();
            return;
        }

        panel.innerHTML = "";
        let totalSlots = 0;

        days.forEach((day) => {
            const slots = Array.isArray(day.slots) ? day.slots : [];
            totalSlots += slots.length;

            const card = document.createElement("article");
            card.className = "rounded-3xl border border-white/10 bg-zinc-950/45 p-5";

            const slotsHtml = slots.length
                ? slots.map((slot) => `
                    <button
                        type="button"
                        class="schedule-slot min-h-11 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm font-black text-zinc-200 transition hover:border-[#f4600d]/50 hover:bg-[#f4600d] hover:text-zinc-950 disabled:cursor-wait disabled:opacity-50"
                        data-start-at="${escapeHtml(slot.start_at)}"
                        data-date-label="${escapeHtml(day.full_label || day.label)}"
                        data-start="${escapeHtml(slot.start)}"
                        data-end="${escapeHtml(slot.end)}"
                    >
                        ${escapeHtml(slot.start)}
                    </button>
                `).join("")
                : `
                    <div class="col-span-full rounded-xl border border-dashed border-white/10 px-3 py-4 text-center text-xs text-zinc-600">
                        Sem horários
                    </div>
                `;

            card.innerHTML = `
                <div class="mb-4 flex items-center justify-between gap-3">
                    <div>
                        <span class="text-xs font-black uppercase tracking-[.14em] text-[#f58b46]">
                            ${escapeHtml(day.weekday)}
                        </span>
                        <h3 class="mt-1 text-xl font-black text-white">
                            ${escapeHtml(day.label)}
                        </h3>
                    </div>
                    <span class="rounded-xl bg-white/5 px-2.5 py-1 text-xs text-zinc-500">
                        ${slots.length} livre${slots.length === 1 ? "" : "s"}
                    </span>
                </div>
                <div class="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-3">
                    ${slotsHtml}
                </div>
            `;

            panel.appendChild(card);
        });

        statusLabel.textContent = `${totalSlots} horário${totalSlots === 1 ? "" : "s"} disponível${totalSlots === 1 ? "" : "is"}`;

        panel.querySelectorAll(".schedule-slot").forEach((button) => {
            button.addEventListener("click", () => bookSlot(button));
        });

        if (!totalSlots) {
            statusLabel.textContent = "Sem horários";
        }
    }

    async function loadPanel() {
        if (!service.value || !professional.value) {
            emptyState("Escolha o serviço e o profissional.");
            return;
        }

        const thisLoad = ++loadSequence;
        loadingState();

        try {
            const params = new URLSearchParams({
                service_id: service.value,
                professional_id: professional.value,
            });

            const response = await fetch(`/api/painel-agenda?${params.toString()}`, {
                headers: {
                    "Accept": "application/json",
                },
            });

            const data = await response.json().catch(() => ({}));

            if (thisLoad !== loadSequence) {
                return;
            }

            if (!response.ok) {
                if (response.status === 409 && data.active_appointment) {
                    window.location.assign("/dashboard");
                    return;
                }
                throw new Error(data.error || "Não foi possível carregar a agenda.");
            }

            renderPanel(data);
        } catch (error) {
            if (thisLoad !== loadSequence) {
                return;
            }
            errorState(error.message || "Erro ao carregar a agenda.");
        }
    }

    async function bookSlot(button) {
        if (bookingInProgress || button.disabled) {
            return;
        }

        const startAt = button.dataset.startAt;
        const dateLabel = button.dataset.dateLabel;
        const start = button.dataset.start;
        const end = button.dataset.end;
        const proName = professional.options[professional.selectedIndex]?.textContent?.trim() || "profissional";

        const confirmed = window.confirm(
            `Confirmar ${dateLabel}, das ${start} às ${end}, com ${proName}?`
        );

        if (!confirmed) {
            return;
        }

        bookingInProgress = true;
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = "...";

        try {
            const response = await fetch("/api/agendar-rapido", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-CSRF-Token": csrfToken,
                },
                body: JSON.stringify({
                    service_id: Number(service.value),
                    professional_id: Number(professional.value),
                    start_at: startAt,
                }),
            });

            const data = await response.json().catch(() => ({}));

            if (!response.ok) {
                throw new Error(data.error || "Não foi possível confirmar o agendamento.");
            }

            if (feedback && feedbackText) {
                const vipText = data.is_vip ? " · ★ VIP" : "";
                feedbackText.textContent = `${data.service} com ${data.professional.name} em ${data.date}, ${data.start}–${data.end}${vipText}.`;
                feedback.classList.remove("hidden");
            }

            window.setTimeout(() => {
                window.location.assign("/dashboard");
            }, 1200);
        } catch (error) {
            window.alert(error.message || "Não foi possível confirmar o agendamento.");
            bookingInProgress = false;
            button.disabled = false;
            button.textContent = originalText;
            await loadPanel();
        }
    }

    service.addEventListener("change", loadPanel);
    professional.addEventListener("change", loadPanel);

    if (typeof window.io === "function") {
        try {
            const socket = window.io();
            socket.on("schedule_changed", (payload = {}) => {
                const selectedProfessionalId = Number(professional.value);
                const changedProfessionalId = Number(payload.professional_id || 0);

                if (!changedProfessionalId || changedProfessionalId === selectedProfessionalId) {
                    loadPanel();
                }
            });
        } catch (_) {
            // A agenda continua funcional mesmo sem atualização em tempo real.
        }
    }

    loadPanel();
})();

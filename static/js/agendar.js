document.addEventListener("DOMContentLoaded", () => {
    /*
     * Impede inicialização duplicada caso o arquivo seja incluído
     * duas vezes por engano no template.
     */
    if (window.__agendaSemanalInicializada) {
        return;
    }

    window.__agendaSemanalInicializada = true;


    // ========================================================
    // ELEMENTOS
    // ========================================================

    const service =
        document.getElementById("service");

    const panel =
        document.getElementById("schedulePanel");

    const statusLabel =
        document.getElementById("statusLabel");

    const weekLabel =
        document.getElementById("weekLabel");

    const feedback =
        document.getElementById("bookingFeedback");

    const feedbackText =
        document.getElementById("bookingFeedbackText");

    const csrfMeta =
        document.querySelector(
            'meta[name="csrf-token"]'
        );


    // Só executa na página de agenda.
    if (!service || !panel) {
        return;
    }


    const csrfToken =
        csrfMeta
            ? csrfMeta.content
            : "";


    // ========================================================
    // ESTADO
    // ========================================================

    let isLoading = false;
    let refreshQueued = false;
    let refreshTimer = null;


    // ========================================================
    // UTILITÁRIOS
    // ========================================================

    function escapeHtml(value) {
        return String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }


    function setStatus(text) {
        if (statusLabel) {
            statusLabel.textContent = text;
        }
    }


    function showInitialLoading() {
        setStatus("Atualizando...");

        panel.innerHTML = `
            <div class="rounded-2xl border border-dashed border-white/10 p-6 text-sm text-zinc-500 md:col-span-2 xl:col-span-3">
                Carregando agenda semanal...
            </div>
        `;
    }


    function showError(message) {
        setStatus("Erro");

        panel.innerHTML = `
            <div class="rounded-2xl border border-rose-400/20 bg-rose-400/10 p-5 text-sm text-rose-200 md:col-span-2 xl:col-span-3">
                ${escapeHtml(message)}
            </div>
        `;
    }


    function showFeedback(message) {
        if (!feedback || !feedbackText) {
            return;
        }

        feedbackText.textContent = message;

        feedback.classList.remove("hidden");

        window.setTimeout(() => {
            feedback.classList.add("hidden");
        }, 2500);
    }


    // ========================================================
    // RENDERIZAÇÃO
    // ========================================================

    function renderPanel(data) {
        if (!data || !Array.isArray(data.days)) {
            throw new Error(
                "Resposta inválida da agenda."
            );
        }


        if (weekLabel && data.week) {
            weekLabel.textContent =
                `Semana disponível: ${data.week.start} até ${data.week.end}`;
        }


        panel.innerHTML = "";

        let totalSlots = 0;


        data.days.forEach((day) => {
            const slots =
                Array.isArray(day.slots)
                    ? day.slots
                    : [];

            totalSlots += slots.length;


            const card =
                document.createElement("article");

            card.className =
                "rounded-3xl border border-white/10 bg-zinc-950/45 p-5";


            const buttonsHtml =
                slots.length
                    ? slots.map((slot) => `
                        <button
                            type="button"
                            class="schedule-slot min-h-12 rounded-xl border border-white/10 bg-white/5 px-3 py-3 text-sm font-black text-zinc-200 transition hover:border-barber-400/50 hover:bg-barber-400 hover:text-zinc-950 disabled:cursor-wait disabled:opacity-50"
                            data-start-at="${escapeHtml(slot.start_at)}"
                            data-date="${escapeHtml(day.full_label)}"
                            data-start="${escapeHtml(slot.start)}"
                            data-end="${escapeHtml(slot.end)}"
                        >
                            ${escapeHtml(slot.start)}
                        </button>
                    `).join("")
                    : `
                        <div class="col-span-full rounded-xl border border-dashed border-white/10 px-3 py-4 text-center text-xs text-zinc-600">
                            Sem horários livres
                        </div>
                    `;


            card.innerHTML = `
                <div class="mb-4 flex items-center justify-between gap-3">
                    <div>
                        <span class="text-xs font-black uppercase tracking-[.14em] text-barber-300">
                            ${escapeHtml(day.weekday)}
                        </span>

                        <h3 class="mt-1 text-xl font-black text-white">
                            ${escapeHtml(day.label)}
                        </h3>
                    </div>

                    <span class="rounded-xl bg-white/5 px-2.5 py-1 text-xs text-zinc-500">
                        ${slots.length}
                        ${slots.length === 1 ? "vaga" : "vagas"}
                    </span>
                </div>

                <div class="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-3">
                    ${buttonsHtml}
                </div>
            `;


            panel.appendChild(card);
        });


        setStatus(
            `${totalSlots} ${
                totalSlots === 1
                    ? "horário livre"
                    : "horários livres"
            }`
        );


        panel
            .querySelectorAll(".schedule-slot")
            .forEach((button) => {
                button.addEventListener(
                    "click",
                    () => bookSlot(button)
                );
            });
    }


    // ========================================================
    // CARREGAMENTO
    // ========================================================

    async function loadPanel(options = {}) {
        const showLoading =
            options.showLoading === true;


        /*
         * Não cria várias requisições ao mesmo tempo.
         * Se chegar uma atualização enquanto outra está rodando,
         * agenda apenas uma nova atualização depois.
         */
        if (isLoading) {
            refreshQueued = true;
            return;
        }


        if (!service.value) {
            showError(
                "Selecione um serviço."
            );
            return;
        }


        isLoading = true;
        refreshQueued = false;


        /*
         * O texto de carregamento aparece apenas na primeira abertura
         * ou quando o usuário troca o serviço.
         *
         * Atualizações automáticas não apagam o painel existente.
         */
        if (showLoading) {
            showInitialLoading();
        } else {
            setStatus("Atualizando...");
        }


        try {
            const params =
                new URLSearchParams({
                    service_id: service.value
                });


            const response =
                await fetch(
                    `/api/painel-agenda?${params.toString()}`,
                    {
                        method: "GET",
                        headers: {
                            "Accept":
                                "application/json"
                        },
                        cache: "no-store"
                    }
                );


            let data;

            try {
                data = await response.json();
            } catch (_) {
                throw new Error(
                    `O servidor retornou HTTP ${response.status} sem JSON válido.`
                );
            }


            if (!response.ok) {
                throw new Error(
                    data.error
                    || `Erro HTTP ${response.status}.`
                );
            }


            renderPanel(data);

        } catch (error) {
            console.error(
                "Erro ao carregar agenda:",
                error
            );

            /*
             * Se já existe um painel na tela durante uma atualização
             * automática, não ficamos substituindo-o por "carregando".
             */
            if (
                showLoading
                || !panel.querySelector("article")
            ) {
                showError(
                    error.message
                    || "Não foi possível carregar a agenda."
                );
            } else {
                setStatus(
                    "Falha ao atualizar"
                );
            }

        } finally {
            isLoading = false;


            if (refreshQueued) {
                refreshQueued = false;

                window.setTimeout(
                    () => {
                        loadPanel({
                            showLoading: false
                        });
                    },
                    250
                );
            }
        }
    }


    // ========================================================
    // AGENDAMENTO
    // ========================================================

    async function bookSlot(button) {
        if (
            !button
            || button.disabled
            || !service.value
        ) {
            return;
        }


        const startAt =
            button.dataset.startAt;

        const dateLabel =
            button.dataset.date;

        const start =
            button.dataset.start;


        if (!startAt) {
            window.alert(
                "Horário inválido."
            );
            return;
        }


        const confirmed =
            window.confirm(
                `Confirmar ${dateLabel} às ${start}?`
            );


        if (!confirmed) {
            return;
        }


        const oldText =
            button.textContent;


        button.disabled = true;
        button.textContent =
            "Marcando...";


        try {
            const response =
                await fetch(
                    "/api/agendar-rapido",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json",

                            "X-CSRF-Token":
                                csrfToken
                        },

                        body:
                            JSON.stringify({
                                service_id:
                                    service.value,

                                start_at:
                                    startAt
                            })
                    }
                );


            let data;

            try {
                data = await response.json();
            } catch (_) {
                throw new Error(
                    `O servidor retornou HTTP ${response.status} sem JSON válido.`
                );
            }


            if (!response.ok) {
                throw new Error(
                    data.error
                    || "Esse horário não está mais disponível."
                );
            }


            showFeedback(
                `${data.date} às ${data.start} · ${data.service}`
            );


            /*
             * Atualiza a agenda depois da marcação,
             * mas não mostra a tela "Carregando agenda semanal".
             */
            await loadPanel({
                showLoading: false
            });

        } catch (error) {
            console.error(
                "Erro ao agendar:",
                error
            );

            window.alert(
                error.message
                || "Não foi possível realizar o agendamento."
            );


            await loadPanel({
                showLoading: false
            });

        } finally {
            /*
             * O botão pode ter sido removido pelo novo render.
             * Só tenta restaurar se ele ainda estiver no DOM.
             */
            if (document.body.contains(button)) {
                button.disabled = false;
                button.textContent =
                    oldText;
            }
        }
    }


    // ========================================================
    // EVENTOS
    // ========================================================

    service.addEventListener(
        "change",
        () => {
            loadPanel({
                showLoading: true
            });
        }
    );


    /*
     * Socket.IO:
     * debounce para impedir uma enxurrada de reloads.
     */
    if (window.io) {
        const socket = io();


        const scheduleRefresh = () => {
            window.clearTimeout(
                refreshTimer
            );

            refreshTimer =
                window.setTimeout(
                    () => {
                        loadPanel({
                            showLoading: false
                        });
                    },
                    300
                );
        };


        socket.on(
            "schedule_changed",
            scheduleRefresh
        );


        socket.on(
            "slot_updated",
            scheduleRefresh
        );
    }


    /*
     * Atualização periódica.
     * 60 segundos é suficiente para remover horários passados
     * sem bombardear o servidor.
     */
    window.setInterval(
        () => {
            loadPanel({
                showLoading: false
            });
        },
        60000
    );


    // Uma única carga inicial.
    loadPanel({
        showLoading: true
    });
});

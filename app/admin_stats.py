from collections import defaultdict
from datetime import datetime, timedelta
import csv
import io

from flask import (
    Blueprint,
    Response,
    abort,
    render_template,
    request,
)
from flask_login import current_user, login_required

from .models import Appointment


bp = Blueprint(
    "admin_stats",
    __name__,
    url_prefix="/admin",
)


PERIOD_OPTIONS = {
    "7": 7,
    "30": 30,
    "90": 90,
    "365": 365,
    "all": None,
}

PERIOD_LABELS = {
    "7": "Últimos 7 dias",
    "30": "Últimos 30 dias",
    "90": "Últimos 90 dias",
    "365": "Últimos 12 meses",
    "all": "Todo o período",
}

WEEKDAY_NAMES = {
    0: "Segunda",
    1: "Terça",
    2: "Quarta",
    3: "Quinta",
    4: "Sexta",
    5: "Sábado",
    6: "Domingo",
}

WEEKDAY_SHORT = {
    0: "Seg",
    1: "Ter",
    2: "Qua",
    3: "Qui",
    4: "Sex",
    5: "Sáb",
    6: "Dom",
}


def _admin_only():
    if (
        not current_user.is_authenticated
        or not getattr(
            current_user,
            "is_admin",
            False,
        )
    ):
        abort(403)


def _status_group(status):
    """
    O painel atual usa:
      scheduled -> Agendado
      cancelled -> Cancelado
      qualquer outro status -> Concluído

    A estatística segue exatamente a mesma regra para não divergir
    do que o administrador já vê na listagem.
    """
    if status == "scheduled":
        return "scheduled"

    if status == "cancelled":
        return "cancelled"

    return "completed"


def _date_window(period_key):
    now = datetime.now()

    days = PERIOD_OPTIONS[
        period_key
    ]

    if days is None:
        first = (
            Appointment.query
            .order_by(
                Appointment.start_at.asc()
            )
            .first()
        )

        if first:
            start = datetime.combine(
                first.start_at.date(),
                datetime.min.time(),
            )
        else:
            start = datetime.combine(
                now.date(),
                datetime.min.time(),
            )

    else:
        start_date = (
            now.date()
            - timedelta(
                days=days - 1
            )
        )

        start = datetime.combine(
            start_date,
            datetime.min.time(),
        )

    end = datetime.combine(
        now.date(),
        datetime.max.time(),
    )

    return (
        now,
        start,
        end,
    )


def _build_statistics(period_key):
    now, start, end = (
        _date_window(period_key)
    )

    appointments = (
        Appointment.query
        .filter(
            Appointment.start_at >= start,
            Appointment.start_at <= end,
        )
        .order_by(
            Appointment.start_at.asc()
        )
        .all()
    )

    daily = defaultdict(
        lambda: {
            "total": 0,
            "scheduled": 0,
            "cancelled": 0,
            "completed": 0,
            "revenue_cents": 0,
            "cancelled_value_cents": 0,
        }
    )

    weekday = {
        day: {
            "total": 0,
            "completed": 0,
            "cancelled": 0,
            "revenue_cents": 0,
        }
        for day in range(7)
    }

    clients = {}

    total = 0
    completed = 0
    cancelled = 0
    scheduled = 0
    revenue_cents = 0
    cancelled_value_cents = 0

    for appointment in appointments:
        group = _status_group(
            appointment.status
        )

        service = appointment.service

        price_cents = int(
            getattr(
                service,
                "price_cents",
                0,
            )
            or 0
        )

        appointment_date = (
            appointment
            .start_at
            .date()
        )

        row = daily[
            appointment_date
        ]

        row["total"] += 1
        row[group] += 1

        week_index = (
            appointment
            .start_at
            .weekday()
        )

        weekday[
            week_index
        ]["total"] += 1

        weekday[
            week_index
        ][group] = (
            weekday[
                week_index
            ].get(
                group,
                0,
            )
            + 1
        )

        total += 1

        if group == "completed":
            completed += 1

            revenue_cents += (
                price_cents
            )

            row[
                "revenue_cents"
            ] += price_cents

            weekday[
                week_index
            ][
                "revenue_cents"
            ] += price_cents

        elif group == "cancelled":
            cancelled += 1

            cancelled_value_cents += (
                price_cents
            )

            row[
                "cancelled_value_cents"
            ] += price_cents

        else:
            scheduled += 1

        user = appointment.user

        user_id = appointment.user_id

        if user_id not in clients:
            clients[user_id] = {
                "id": user_id,
                "name": (
                    getattr(
                        user,
                        "name",
                        "Cliente",
                    )
                    or "Cliente"
                ),
                "email": (
                    getattr(
                        user,
                        "email",
                        "",
                    )
                    or ""
                ),
                "total": 0,
                "completed": 0,
                "cancelled": 0,
                "scheduled": 0,
                "revenue_cents": 0,
            }

        client = clients[
            user_id
        ]

        client["total"] += 1
        client[group] += 1

        if group == "completed":
            client[
                "revenue_cents"
            ] += price_cents

    unique_clients = len(
        clients
    )

    returning_clients = sum(
        1
        for client in clients.values()
        if client["total"] >= 2
    )

    finished = (
        completed
        + cancelled
    )

    completion_rate = (
        round(
            (
                completed
                / finished
            )
            * 100,
            1,
        )
        if finished
        else 0
    )

    cancellation_rate = (
        round(
            (
                cancelled
                / finished
            )
            * 100,
            1,
        )
        if finished
        else 0
    )

    client_return_rate = (
        round(
            (
                returning_clients
                / unique_clients
            )
            * 100,
            1,
        )
        if unique_clients
        else 0
    )

    # Próximos horários entram como previsão financeira,
    # e não como faturamento já realizado.
    future_scheduled = (
        Appointment.query
        .filter(
            Appointment.start_at > now,
            Appointment.status
                == "scheduled",
        )
        .order_by(
            Appointment.start_at.asc()
        )
        .all()
    )

    projected_cents = sum(
        int(
            getattr(
                appointment.service,
                "price_cents",
                0,
            )
            or 0
        )
        for appointment
        in future_scheduled
    )

    daily_rows = []

    if daily:
        first_day = min(
            daily.keys()
        )

        last_day = max(
            daily.keys()
        )

        cursor = first_day

        while cursor <= last_day:
            values = daily[
                cursor
            ]

            finished_day = (
                values["completed"]
                + values["cancelled"]
            )

            cancel_rate_day = (
                round(
                    (
                        values["cancelled"]
                        / finished_day
                    )
                    * 100,
                    1,
                )
                if finished_day
                else 0
            )

            daily_rows.append(
                {
                    "date": cursor,
                    "label":
                        cursor.strftime(
                            "%d/%m"
                        ),
                    "full_label":
                        cursor.strftime(
                            "%d/%m/%Y"
                        ),
                    "weekday":
                        WEEKDAY_NAMES[
                            cursor.weekday()
                        ],
                    "weekday_short":
                        WEEKDAY_SHORT[
                            cursor.weekday()
                        ],
                    "total":
                        values["total"],
                    "scheduled":
                        values[
                            "scheduled"
                        ],
                    "cancelled":
                        values[
                            "cancelled"
                        ],
                    "completed":
                        values[
                            "completed"
                        ],
                    "revenue_cents":
                        values[
                            "revenue_cents"
                        ],
                    "cancelled_value_cents":
                        values[
                            "cancelled_value_cents"
                        ],
                    "cancellation_rate":
                        cancel_rate_day,
                }
            )

            cursor += timedelta(
                days=1
            )

    weekday_rows = []

    for day in range(7):
        item = weekday[
            day
        ]

        weekday_rows.append(
            {
                "index": day,
                "name":
                    WEEKDAY_NAMES[
                        day
                    ],
                "short":
                    WEEKDAY_SHORT[
                        day
                    ],
                "total":
                    item["total"],
                "completed":
                    item["completed"],
                "cancelled":
                    item["cancelled"],
                "revenue_cents":
                    item[
                        "revenue_cents"
                    ],
            }
        )

    best_weekday = max(
        weekday_rows,
        key=lambda item:
            (
                item[
                    "completed"
                ],
                item[
                    "revenue_cents"
                ],
            ),
        default=None,
    )

    if (
        not best_weekday
        or best_weekday[
            "completed"
        ] == 0
    ):
        best_weekday = None

    completed_days = [
        row
        for row in daily_rows
        if row["completed"] > 0
    ]

    best_date = max(
        completed_days,
        key=lambda item:
            (
                item[
                    "completed"
                ],
                item[
                    "revenue_cents"
                ],
            ),
        default=None,
    )

    best_revenue_date = max(
        completed_days,
        key=lambda item:
            item[
                "revenue_cents"
            ],
        default=None,
    )

    top_clients = sorted(
        clients.values(),
        key=lambda client:
            (
                client["completed"],
                client["total"],
                client[
                    "revenue_cents"
                ],
            ),
        reverse=True,
    )[:10]

    average_ticket_cents = (
        round(
            revenue_cents
            / completed
        )
        if completed
        else 0
    )

    average_daily_revenue_cents = (
        round(
            revenue_cents
            / len(
                completed_days
            )
        )
        if completed_days
        else 0
    )

    # ========================================================
    # DADOS NORMALIZADOS PARA OS GRÁFICOS
    # ========================================================
    # Os gráficos são renderizados diretamente pelo Jinja/Tailwind.
    # Não dependem de Chart.js ou de qualquer biblioteca externa.

    chart_daily_rows = [
        row
        for row in daily_rows
        if row["total"] > 0
    ]

    max_daily_count = max(
        [
            max(
                row["completed"],
                row["cancelled"],
                row["scheduled"],
            )
            for row in chart_daily_rows
        ],
        default=1,
    )

    if max_daily_count <= 0:
        max_daily_count = 1

    max_daily_revenue = max(
        [
            row["revenue_cents"]
            for row in chart_daily_rows
        ],
        default=1,
    )

    if max_daily_revenue <= 0:
        max_daily_revenue = 1

    for row in chart_daily_rows:
        row["completed_percent"] = round(
            row["completed"]
            / max_daily_count
            * 100,
            2,
        )

        row["cancelled_percent"] = round(
            row["cancelled"]
            / max_daily_count
            * 100,
            2,
        )

        row["scheduled_percent"] = round(
            row["scheduled"]
            / max_daily_count
            * 100,
            2,
        )

        row["revenue_percent"] = round(
            row["revenue_cents"]
            / max_daily_revenue
            * 100,
            2,
        )

    max_weekday_completed = max(
        [
            row["completed"]
            for row in weekday_rows
            if row["index"] <= 5
        ],
        default=1,
    )

    if max_weekday_completed <= 0:
        max_weekday_completed = 1

    for row in weekday_rows:
        row["completed_percent"] = round(
            row["completed"]
            / max_weekday_completed
            * 100,
            2,
        )

    return {
        "period_key":
            period_key,
        "period_label":
            PERIOD_LABELS[
                period_key
            ],
        "period_start":
            start,
        "period_end":
            end,
        "appointments":
            appointments,
        "total":
            total,
        "completed":
            completed,
        "cancelled":
            cancelled,
        "scheduled":
            scheduled,
        "unique_clients":
            unique_clients,
        "returning_clients":
            returning_clients,
        "completion_rate":
            completion_rate,
        "cancellation_rate":
            cancellation_rate,
        "client_return_rate":
            client_return_rate,
        "revenue_cents":
            revenue_cents,
        "cancelled_value_cents":
            cancelled_value_cents,
        "average_ticket_cents":
            average_ticket_cents,
        "average_daily_revenue_cents":
            average_daily_revenue_cents,
        "future_scheduled":
            len(
                future_scheduled
            ),
        "projected_cents":
            projected_cents,
        "daily_rows":
            daily_rows,
        "chart_daily_rows":
            chart_daily_rows,
        "weekday_rows":
            weekday_rows,
        "best_weekday":
            best_weekday,
        "best_date":
            best_date,
        "best_revenue_date":
            best_revenue_date,
        "top_clients":
            top_clients,
    }


@bp.get("/estatisticas")
@login_required
def statistics():
    _admin_only()

    period_key = (
        request.args.get(
            "period",
            "30",
        )
        or "30"
    )

    if period_key not in (
        PERIOD_OPTIONS
    ):
        period_key = "30"

    data = _build_statistics(
        period_key
    )

    return render_template(
        "admin_stats.html",
        statistics=data,
        period_options=[
            (
                key,
                PERIOD_LABELS[
                    key
                ],
            )
            for key in (
                "7",
                "30",
                "90",
                "365",
                "all",
            )
        ],
    )


@bp.get("/estatisticas.csv")
@login_required
def statistics_csv():
    _admin_only()

    period_key = (
        request.args.get(
            "period",
            "30",
        )
        or "30"
    )

    if period_key not in (
        PERIOD_OPTIONS
    ):
        period_key = "30"

    data = _build_statistics(
        period_key
    )

    output = io.StringIO()

    writer = csv.writer(
        output,
        delimiter=";",
    )

    writer.writerow(
        [
            "Data",
            "Dia",
            "Agendamentos",
            "Concluídos",
            "Cancelados",
            "Agendados",
            "Taxa cancelamento (%)",
            "Faturamento realizado (R$)",
            "Valor cancelado (R$)",
        ]
    )

    for row in data[
        "daily_rows"
    ]:
        writer.writerow(
            [
                row[
                    "full_label"
                ],
                row[
                    "weekday"
                ],
                row["total"],
                row[
                    "completed"
                ],
                row[
                    "cancelled"
                ],
                row[
                    "scheduled"
                ],
                str(
                    row[
                        "cancellation_rate"
                    ]
                ).replace(
                    ".",
                    ",",
                ),
                f"{row['revenue_cents'] / 100:.2f}".replace(
                    ".",
                    ",",
                ),
                f"{row['cancelled_value_cents'] / 100:.2f}".replace(
                    ".",
                    ",",
                ),
            ]
        )

    csv_content = (
        "\ufeff"
        + output.getvalue()
    )

    return Response(
        csv_content,
        mimetype=(
            "text/csv; "
            "charset=utf-8"
        ),
        headers={
            "Content-Disposition":
                (
                    "attachment; "
                    f"filename=estatisticas_gringo_{period_key}.csv"
                )
        },
    )

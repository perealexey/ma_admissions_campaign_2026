"""
app.py — локальный дашборд приёмной кампании магистратуры ВШЭ (Этап 4).

Запуск: streamlit run app.py

Три разреза (см. промпт задачи, §4.6, + доп. страница сравнения):
  1. По программе — подробные метрики, рейтинг желанности, распределение
     приоритетов, пересечения.
  2. Сравнить программы — две программы рядом, как карточки на маркетплейсе.
  3. По абитуриенту — его программы, типы мест, приоритеты, баллы.

Данные грузятся из приватной папки на Google Drive (не из репозитория —
репозиторий публичный, но данных абитуриентов в нём нет) через сервисный
аккаунт Google. Доступ и ID папки — в st.secrets (см. README/чат по деплою).
Код дашборда не меняется между срезами, актуальный срез подхватывается
автоматически (см. load_long_table).
"""
from __future__ import annotations

import io
import json

import altair as alt
import pandas as pd
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


@st.cache_resource
def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gdrive_service_account"]),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@st.cache_data(ttl=600)
def list_drive_files() -> dict[str, str]:
    """{имя файла: id файла} для всех файлов в папке (folder_id из secrets)."""
    service = get_drive_service()
    folder_id = st.secrets["gdrive_folder_id"]
    files: dict[str, str] = {}
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            files[f["name"]] = f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


@st.cache_data(ttl=600)
def download_drive_file(file_id: str) -> bytes:
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def read_drive_csv(name: str, **kwargs) -> pd.DataFrame:
    files = list_drive_files()
    return pd.read_csv(io.BytesIO(download_drive_file(files[name])), **kwargs)

# Программы, которые всегда показываются первыми в фильтрах и выпадающих
# списках — по договорённости с заказчиком (изначальный список из
# priorityPrograms.html + СПб-программа, добавленная позже как якорь для
# межкампусных сверок).
PRIORITY_PROGRAMS = [
    "Интерактивные медиа и цифровые индустрии",
    "Кинопроизводство",
    "Медиаменеджмент",
    "Современная журналистика",
    "Современные медиаисследования и аналитика",
    "Медиапроизводство и медиааналитика",
]

PLACE_TYPE_LABELS = {"budget": "Бюджет", "commercial": "Платное"}
SEARCH_FIELD_LABELS = {"student_id": "Уникальный код поступающего", "reg_number": "Регистрационный номер"}


@st.cache_data(ttl=600)
def load_long_table() -> tuple[pd.DataFrame, str]:
    """Длинная таблица датирована по срезам (raw-архив сохраняется целиком на
    Drive) — берём самый свежий файл admissions_long_*.parquet по имени."""
    files = list_drive_files()
    names = sorted(n for n in files if n.startswith("admissions_long_") and n.endswith(".parquet"))
    if not names:
        st.error("В папке на Google Drive не найдено ни одного admissions_long_*.parquet")
        st.stop()
    name = names[-1]
    df = pd.read_parquet(io.BytesIO(download_drive_file(files[name])))
    snapshot = name.removeprefix("admissions_long_").removesuffix(".parquet")
    return df, snapshot


@st.cache_data(ttl=600)
def load_metrics():
    # dtype Int64 (nullable) явно: budget_places/commercial_places содержат
    # пропуски (программы-«близнецы» с неоднозначными местами) — без явного
    # dtype pandas молча читает такую колонку как float64 и в таблицах
    # появляется "25.0" вместо "25".
    main = read_drive_csv(
        "campaign_metrics_main.csv",
        dtype={"budget_places": "Int64", "commercial_places": "Int64"},
    )
    ranked = read_drive_csv("campaign_metrics_m4_desirability_ranked.csv")
    no_places = read_drive_csv("campaign_metrics_no_budget_places.csv")
    files = list_drive_files()
    meta = json.loads(download_drive_file(files["campaign_metrics_meta.json"]).decode("utf-8"))
    priority_dist = read_drive_csv("metric_priority_distribution.csv")
    inter_budget = read_drive_csv("metric_program_intersections_budget.csv", index_col=0)
    inter_commercial = read_drive_csv("metric_program_intersections_commercial.csv", index_col=0)
    return main, ranked, no_places, meta, priority_dist, inter_budget, inter_commercial


def with_priority_first(options: list[str]) -> list[str]:
    """Приоритетные программы — в начале списка (в заданном порядке), остальные
    — по алфавиту следом."""
    present_priority = [p for p in PRIORITY_PROGRAMS if p in options]
    rest = sorted(set(options) - set(present_priority))
    return present_priority + rest


def star_label(name: str) -> str:
    return f"⭐ {name}" if name in PRIORITY_PROGRAMS else name


def place_type_radio(label: str, key: str) -> str:
    """st.radio с русскими подписями budget/commercial, возвращает внутреннее
    значение ("budget"/"commercial") для фильтрации данных."""
    return st.radio(label, list(PLACE_TYPE_LABELS), format_func=lambda x: PLACE_TYPE_LABELS[x],
                     horizontal=True, key=key)


def check_password() -> bool:
    """Простой общий пароль на весь дашборд — репозиторий публичный (учебное
    упражнение с git), пароль здесь не про защиту данных как таковых, а про
    то, чтобы дашборд не открывался случайным прохожим. Само значение пароля
    не в коде и не в репозитории: st.secrets читает его из .streamlit/secrets.toml
    локально (в .gitignore) или из Settings → Secrets на Streamlit Cloud."""
    if st.session_state.get("authenticated"):
        return True

    def on_submit():
        if st.session_state.get("password_input") == st.secrets.get("app_password"):
            st.session_state["authenticated"] = True
        else:
            st.session_state["authenticated"] = False

    st.text_input("Пароль", type="password", key="password_input", on_change=on_submit)
    if st.session_state.get("authenticated") is False:
        st.error("Неверный пароль.")
    return False


st.set_page_config(page_title="Приёмная кампания ВШЭ — магистратура", layout="wide")

if not check_password():
    st.stop()

df, snapshot_from_long_table = load_long_table()
main, ranked, no_places, meta, priority_dist, inter_budget, inter_commercial = load_metrics()

st.title("Приёмная кампания магистратуры ВШЭ — Москва + СПб")
st.caption(
    f"Срез метрик: {meta['snapshot_date']} (длинная таблица: {snapshot_from_long_table}) · "
    f"Программ: {meta['n_programs']}"
)

# ------------------------------------------------------------- Базовая статистика
st.header("Базовая статистика")
st.caption("Заявления по программам — как есть, без дополнительных расчётов.")

all_program_names = sorted(main["program_name"].unique())
default_basic = [p for p in PRIORITY_PROGRAMS if p in all_program_names]
selected_basic_programs = st.multiselect(
    "Программы", options=with_priority_first(all_program_names), default=default_basic,
    format_func=star_label, placeholder="Выберите программы",
)

basic_table = main[main["program_name"].isin(selected_basic_programs)][
    ["program_name", "campus", "n_applications_budget", "n_applications_commercial"]
].rename(columns={
    "program_name": "Программа",
    "campus": "Кампус",
    "n_applications_budget": "Заявок на бюджет",
    "n_applications_commercial": "Заявок на платное",
})
st.dataframe(basic_table, use_container_width=True, hide_index=True)

st.divider()

tab_program, tab_compare, tab_applicant = st.tabs(["По программе", "Сравнить программы", "По абитуриенту"])

# ---------------------------------------------------------------- По программе
with tab_program:
    campuses = sorted(main["campus"].unique())
    selected_campuses = st.multiselect("Кампус", campuses, default=campuses, placeholder="Выберите кампус")
    filtered_main = main[main["campus"].isin(selected_campuses)]

    serious_label_budget = f"Приоритет 1–{meta['K_SERIOUS']} (бюджет)"
    serious_label_commercial = f"Приоритет 1–{meta['K_SERIOUS']} (платное)"

    st.subheader("Подробные метрики по программе")

    detail_program_names = sorted(filtered_main["program_name"].unique())
    default_detail = [p for p in PRIORITY_PROGRAMS if p in detail_program_names]
    selected_detail_programs = st.multiselect(
        "Программы", options=with_priority_first(detail_program_names), default=default_detail,
        format_func=star_label, key="detail_programs", placeholder="Выберите программы",
    )

    with st.expander("Подробнее о метриках"):
        st.markdown(
            f"- **Уникальных абитуриентов** — сколько разных людей подали хотя бы одну "
            f"заявку на программу (бюджет и/или платное вместе, без задвоения тех, кто "
            f"подался обоими способами).\n"
            f"- **«{serious_label_budget}» / «{serious_label_commercial}»** — сколько "
            f"заявителей поставили программу в число первых {meta['K_SERIOUS']} приоритетов "
            f"(порог «серьёзности» заявки), отдельно для бюджета и платных мест.\n"
            f"- **Конкурс на место (бюджет)** — заявок на одно бюджетное место.\n"
            f"- **Приоритетных заявителей на место** — заявителей с приоритетом "
            f"1–{meta['K_SERIOUS']} на одно бюджетное место; значение <1 означает, что "
            f"серьёзных заявителей меньше, чем мест."
        )

    detailed_table = filtered_main[filtered_main["program_name"].isin(selected_detail_programs)][
        [
            "program_name", "campus", "n_unique_applicants_total",
            "n_serious_budget", "n_serious_commercial",
            "competition_ratio_budget", "demand_pressure_budget",
        ]
    ].copy()
    detailed_table["competition_ratio_budget"] = detailed_table["competition_ratio_budget"].round(2)
    detailed_table["demand_pressure_budget"] = detailed_table["demand_pressure_budget"].round(2)
    detailed_table = detailed_table.rename(columns={
        "program_name": "Программа",
        "campus": "Кампус",
        "n_unique_applicants_total": "Уникальных абитуриентов",
        "n_serious_budget": serious_label_budget,
        "n_serious_commercial": serious_label_commercial,
        "competition_ratio_budget": "Конкурс на место (бюджет)",
        "demand_pressure_budget": "Приоритетных заявителей на место",
    })
    st.dataframe(
        detailed_table.sort_values("Уникальных абитуриентов", ascending=False),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Рейтинг желанности программ")
    st.caption(
        "Насколько часто абитуриенты (при подаче на бюджет) ставят программу первым "
        "приоритетом. Значение скорректировано так, чтобы программы с небольшим числом "
        "заявок не искажали картину случайным всплеском или провалом — чем меньше заявок "
        "у программы, тем сильнее её показатель подтягивается к среднему по рынку. "
        "Программы без бюджетных мест сюда не входят."
    )
    with st.expander("Подробнее о метрике"):
        st.markdown(
            "Простая доля «поставили первым приоритетом» ненадёжна для программ с малым "
            "числом заявок — случайный всплеск или провал легко исказит картину. Поэтому "
            "оценка каждой программы подтягивается к среднему по рынку значению тем "
            "сильнее, чем меньше у неё данных (метод — эмпирический Байес)."
        )
        st.latex(r"\text{Желанность} = \dfrac{\text{cnt}_{p1} + k \cdot p_0}{n + k}")
        st.markdown(
            f"где `cnt_p1` — число заявителей с приоритетом №1, `n` — всего бюджетных "
            f"заявителей у программы, `p0` ≈ {meta['M4_p0']:.3f} — средняя по рынку доля "
            f"первого приоритета, `k` ≈ {meta['M4_k']:.2f} — сила подтягивания к среднему "
            f"(чем меньше заявок у программы, тем сильнее сдвиг к `p0`)."
        )
    filtered_ranked = ranked[ranked["campus"].isin(selected_campuses)]
    ranked_display = filtered_ranked[
        ["desirability_rank", "program_name", "campus", "n_p1_budget", "n_applications_budget", "desirability"]
    ].copy()
    ranked_display["desirability"] = ranked_display["desirability"].round(3)
    ranked_display = ranked_display.rename(columns={
        "desirability_rank": "Ранг",
        "program_name": "Программа",
        "campus": "Кампус",
        "n_p1_budget": "Заявок с приоритетом №1",
        "n_applications_budget": "Всего заявок на бюджет",
        "desirability": "Желанность",
    })
    st.dataframe(ranked_display, use_container_width=True, hide_index=True)

    filtered_no_places = no_places[no_places["campus"].isin(selected_campuses)]
    no_places_display = filtered_no_places[["program_name", "campus"]].rename(
        columns={"program_name": "Программа", "campus": "Кампус"}
    )
    with st.expander(f"Программы без бюджетных мест, вне рейтинга ({len(no_places_display)})"):
        st.dataframe(no_places_display, use_container_width=True, hide_index=True)

    st.divider()
    program_names = sorted(filtered_main["program_name"].unique())
    selected_program = st.selectbox(
        "Программа — распределение приоритетов и пересечения",
        with_priority_first(program_names),
        format_func=star_label,
    )
    prog_id = filtered_main.loc[filtered_main["program_name"] == selected_program, "program_id"].iloc[0]

    st.markdown("**Распределение приоритетов**")
    dist = priority_dist[priority_dist["program_id"] == prog_id].set_index("priority")
    if len(dist):
        dist_display = dist[["n_budget_applicants", "n_commercial_applicants"]].rename(columns={
            "n_budget_applicants": PLACE_TYPE_LABELS["budget"],
            "n_commercial_applicants": PLACE_TYPE_LABELS["commercial"],
        })
        st.bar_chart(dist_display)
    else:
        st.info("Нет заявок с указанным приоритетом по этой программе.")

    st.markdown("**Топ-10 пересечений (общие абитуриенты)**")
    place_type_choice = place_type_radio("Тип места", key="inter_place_type")
    matrix = inter_budget if place_type_choice == "budget" else inter_commercial
    if selected_program in matrix.index:
        row = matrix.loc[selected_program].drop(selected_program, errors="ignore")
        row = row[row > 0].sort_values(ascending=False).head(10)
        if len(row):
            chart_data = row.rename("count").rename_axis("program").reset_index()
            chart = (
                alt.Chart(chart_data)
                .mark_bar()
                .encode(
                    x=alt.X("count:Q", title="Общих абитуриентов"),
                    y=alt.Y("program:N", sort="-x", title=None,
                            axis=alt.Axis(labelLimit=400)),
                )
                .properties(height=32 * len(chart_data) + 20)
            )
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Нет пересечений с другими программами по этому типу места.")
    else:
        st.info(f"У программы нет заявок типа «{PLACE_TYPE_LABELS[place_type_choice]}» — пересечения не определены.")

# -------------------------------------------------------------- Сравнить программы
with tab_compare:
    st.caption(
        "Сравните две программы рядом — как карточки товаров на маркетплейсе: "
        "места, конкурс, желанность, профиль приоритетов."
    )

    # Два отдельных selectbox вместо multiselect с max_selections=2: у
    # multiselect в Streamlit есть встроенная опция "Select all" (не
    # отключается параметром — проверено по исходникам виджета), которая
    # прямо противоречит ограничению "ровно 2". Два независимых выбора дают
    # то же самое "ровно 2" без этого конфликта в принципе.
    all_names_compare = sorted(main["program_name"].unique())
    options_compare = with_priority_first(all_names_compare)
    col_a, col_b = st.columns(2)
    with col_a:
        program_a = st.selectbox(
            "Программа 1", options_compare, index=None, format_func=star_label,
            placeholder="Выберите программу", key="compare_program_a",
        )
    with col_b:
        program_b = st.selectbox(
            "Программа 2", options_compare, index=None, format_func=star_label,
            placeholder="Выберите программу", key="compare_program_b",
        )

    if program_a is None or program_b is None:
        st.info("Выберите обе программы для сравнения.")
    elif program_a == program_b:
        st.warning("Выберите две разные программы.")
    else:
        selected_compare = [program_a, program_b]
        # У 4 известных программ-«близнецов» (см. scripts/match_supply_demand.py)
        # одно отображаемое имя соответствует двум разным program_id (двум
        # отдельным конкурсам). Сравнение здесь идёт по имени, поэтому для
        # такой программы однозначного выбора нет — раньше это было
        # ограничение только в комментарии, но оно реально ловится (крашится
        # на дублирующихся именах колонок при транспонировании), поэтому
        # явно проверяем и не даём сравнение вместо непонятной ошибки.
        name_counts = main["program_name"].value_counts()
        ambiguous = [p for p in selected_compare if name_counts.get(p, 0) > 1]
        if ambiguous:
            st.error(
                f"«{ambiguous[0]}» — это отображаемое имя сразу двух разных конкурсов "
                "(program_id) с одинаковым названием, сравнение по имени программы для "
                "неё не работает однозначно. Выберите другую программу."
            )
        else:
            # main уже содержит "desirability" (добавлена в campaign_metrics.py);
            # из ranked нужен только "desirability_rank", которого в main нет.
            cmp = main[main["program_name"].isin(selected_compare)].merge(
                ranked[["program_id", "desirability_rank"]],
                on="program_id", how="left",
            ).set_index("program_name")

            # Общие уникальные абитуриенты между парой — считается по всей
            # длинной таблице (бюджет+платное вместе, без задвоения), а не из
            # готовых matrix'ов M8 (те раздельные по типу места). Одно
            # симметричное число на пару, одинаковое в обеих колонках.
            # str(...): program_id в df (из parquet) — строка, а в main (после
            # обратного чтения campaign_metrics_main.csv без explicit dtype)
            # pandas сам решает, что это число, и молча превращает в int64 —
            # без приведения типов сравнение ниже всегда было бы пустым (0).
            id_a = str(cmp.loc[program_a, "program_id"])
            id_b = str(cmp.loc[program_b, "program_id"])
            students_a = set(df.loc[df["program_id"] == id_a, "student_id"])
            students_b = set(df.loc[df["program_id"] == id_b, "student_id"])
            n_common = len(students_a & students_b)

            rows = {
                "Кампус": cmp["campus"],
                "Бюджетных мест": cmp["budget_places"],
                "Платных мест": cmp["commercial_places"],
                "Заявок на бюджет": cmp["n_applications_budget"],
                "Заявок на платное": cmp["n_applications_commercial"],
                "Уникальных абитуриентов": cmp["n_unique_applicants_total"],
                "Общие уникальные абитуриенты": pd.Series(n_common, index=selected_compare),
                serious_label_budget: cmp["n_serious_budget"],
                serious_label_commercial: cmp["n_serious_commercial"],
                "Конкурс на место (бюджет)": cmp["competition_ratio_budget"].round(2),
                "Конкурс на место (платное)": cmp["competition_ratio_commercial"].round(2),
                "Приоритетных заявителей на место": cmp["demand_pressure_budget"].round(2),
                "Желанность": cmp["desirability"].round(3),
                "Ранг желанности (из 125)": cmp["desirability_rank"],
            }
            compare_table = pd.DataFrame(rows).T[selected_compare].fillna("—")
            st.dataframe(compare_table, use_container_width=True)
            st.caption(
                "«—» значит «не определено» (нет бюджетных/платных мест или программа вне "
                "рейтинга желанности) — не путать с нулевым интересом. «Общие уникальные "
                "абитуриенты» — одно число на пару программ (сколько разных людей подались в "
                "обе, бюджет и платное вместе), поэтому оно одинаково в обеих колонках."
            )

            st.divider()
            st.markdown("**Профиль приоритетов**")
            profile_place_type = place_type_radio("Тип места", key="compare_profile_place_type")
            value_col = "n_budget_applicants" if profile_place_type == "budget" else "n_commercial_applicants"
            profile_data = priority_dist[priority_dist["program_name"].isin(selected_compare)][
                ["program_name", "priority", value_col]
            ]
            if len(profile_data):
                profile_chart = (
                    alt.Chart(profile_data)
                    .mark_bar()
                    .encode(
                        x=alt.X("priority:O", title="Приоритет"),
                        y=alt.Y(f"{value_col}:Q", title="Заявителей"),
                    )
                    .properties(width=160, height=200)
                    .facet(column=alt.Column("program_name:N", title=None, sort=selected_compare))
                )
                st.altair_chart(profile_chart)
            else:
                st.info(f"Нет заявок типа «{PLACE_TYPE_LABELS[profile_place_type]}» ни у одной из выбранных программ.")

# --------------------------------------------------------------- По абитуриенту
with tab_applicant:
    st.caption("Найдите абитуриента по его коду, чтобы увидеть все его заявки.")
    search_field = st.radio(
        "Искать по", list(SEARCH_FIELD_LABELS), format_func=lambda x: SEARCH_FIELD_LABELS[x], horizontal=True
    )
    query = st.text_input(f"Введите: {SEARCH_FIELD_LABELS[search_field]}")

    if query:
        try:
            query_value = int(query)
        except ValueError:
            st.error("Нужно ввести число.")
        else:
            student_rows = df[df[search_field] == query_value]
            if student_rows.empty:
                st.warning("Абитуриент с таким кодом не найден в текущем срезе.")
            else:
                display_cols = [
                    "program_name", "campus", "place_type", "priority",
                    "entry_test_1_name", "entry_test_1_score",
                    "entry_test_2_name", "entry_test_2_score",
                    "total_score", "competition_status", "all_grades_positive",
                ]
                student_display = student_rows[display_cols].sort_values(["place_type", "priority"]).copy()
                student_display["place_type"] = student_display["place_type"].map(PLACE_TYPE_LABELS)
                student_display = student_display.rename(columns={
                    "program_name": "Программа",
                    "campus": "Кампус",
                    "place_type": "Тип места",
                    "priority": "Приоритет",
                    "entry_test_1_name": "Испытание 1 (название)",
                    "entry_test_1_score": "Испытание 1 (балл)",
                    "entry_test_2_name": "Испытание 2 (название)",
                    "entry_test_2_score": "Испытание 2 (балл)",
                    "total_score": "Сумма баллов",
                    "competition_status": "Статус",
                    "all_grades_positive": "Все оценки положительные",
                })
                st.dataframe(student_display, use_container_width=True, hide_index=True)
                st.caption(f"Всего заявок у абитуриента: {len(student_rows)} "
                           f"на {student_rows['program_id'].nunique()} программ(-ы).")

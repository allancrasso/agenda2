# streamlit_agenda.py
# -*- coding: utf-8 -*-
"""
Agenda Streamlit + Calendar + Links
- Banco: SQLite em ~/.streamlit_agenda.db
- Abas: Agenda (list), Calendar (monthly view), Links, Config/Notificações
- Funcionalidades: CRUD tasks/links, prioridade, reordenação, marcar concluído,
  notificações (plyer optional), abrir pastas locais (quando rodando localmente)
"""

from pathlib import Path
import os
import sqlite3
from datetime import datetime, date, time, timedelta
import calendar as _pycalendar
import webbrowser
import platform
import subprocess

import streamlit as st

# Try plyer for OS notifications (optional)
try:
    from plyer import notification
    HAS_PLYER = True
except Exception:
    HAS_PLYER = False

# ---------------- Config ----------------
DB_PATH = os.path.join(os.path.expanduser("~"), ".streamlit_agenda.db")
DEFAULT_CHECK_WINDOW_MINUTES = 60

# ---------------- Database helpers ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        folder_path TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        due_iso TEXT NOT NULL,
        recurrence TEXT DEFAULT 'once',
        folder_path TEXT,
        priority INTEGER DEFAULT 0,
        sort_index INTEGER DEFAULT 0,
        last_notified_date TEXT,
        completed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def run_query(query, args=(), fetch=False):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    cur.execute(query, args)
    rv = None
    if fetch:
        rv = cur.fetchall()
    conn.commit()
    conn.close()
    return rv

# Links CRUD
def add_link(name, url, folder_path=None):
    run_query("INSERT INTO links (name, url, folder_path) VALUES (?,?,?)", (name, url, folder_path))

def get_links():
    return run_query("SELECT id, name, url, folder_path FROM links ORDER BY created_at DESC", fetch=True)

def update_link(link_id, name, url, folder_path):
    run_query("UPDATE links SET name=?, url=?, folder_path=? WHERE id=?", (name, url, folder_path, link_id))

def delete_link(link_id):
    run_query("DELETE FROM links WHERE id=?", (link_id,))

# Tasks CRUD
def add_task(title, description, due_dt: datetime, recurrence='once', folder_path=None, priority=0):
    # use timestamp as default sort_index
    si = int(datetime.now().timestamp() * 1000)
    run_query(
        "INSERT INTO tasks (title, description, due_iso, recurrence, folder_path, priority, sort_index) VALUES (?,?,?,?,?,?,?)",
        (title, description, due_dt.isoformat(), recurrence, folder_path, int(priority), si)
    )

def get_tasks(order_by_custom=True):
    if order_by_custom:
        return run_query(
            "SELECT id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed FROM tasks ORDER BY sort_index ASC, priority DESC, due_iso ASC",
            fetch=True
        )
    else:
        return run_query(
            "SELECT id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed FROM tasks ORDER BY priority DESC, due_iso ASC",
            fetch=True
        )

def update_task(task_id, title, description, due_dt: datetime, recurrence, folder_path, priority):
    run_query("UPDATE tasks SET title=?, description=?, due_iso=?, recurrence=?, folder_path=?, priority=? WHERE id=?",
              (title, description, due_dt.isoformat(), recurrence, folder_path, int(priority), task_id))

def delete_task(task_id):
    run_query("DELETE FROM tasks WHERE id=?", (task_id,))

def set_task_notified_date(task_id, yyyy_mm_dd):
    run_query("UPDATE tasks SET last_notified_date=? WHERE id=?", (yyyy_mm_dd, task_id))

def set_task_completed(task_id, completed=True):
    run_query("UPDATE tasks SET completed=? WHERE id=?", (1 if completed else 0, task_id))

def swap_sort_index(task_id, other_id):
    vals = run_query("SELECT id, sort_index FROM tasks WHERE id IN (?,?)", (task_id, other_id), fetch=True)
    if len(vals) != 2:
        return
    a_id, a_idx = vals[0]
    b_id, b_idx = vals[1]
    run_query("UPDATE tasks SET sort_index=? WHERE id=?", (b_idx, a_id))
    run_query("UPDATE tasks SET sort_index=? WHERE id=?", (a_idx, b_id))

# ---------------- Utilities ----------------
from urllib.parse import urlparse
def is_valid_url(s):
    try:
        p = urlparse(s)
        return p.scheme in ('http', 'https') and p.netloc != ''
    except Exception:
        return False

def open_folder_local(path):
    if not path:
        return False
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
        return True
    except Exception as e:
        st.error(f"Erro ao abrir pasta: {e}")
        return False

def notify_os(title, message):
    if HAS_PLYER:
        try:
            notification.notify(title=title, message=message, timeout=8)
            return True
        except Exception:
            pass
    # fallback: message in UI
    st.info(f"NOTIFICAÇÃO: **{title}** — {message}")
    return False

def parse_iso_or_flex(s):
    # try various common formats
    if not s:
        return None
    fmt_try = [ "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
               "%Y-%m-%d" ]
    for f in fmt_try:
        try:
            return datetime.strptime(s, f)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

# ---------------- Notification checker ----------------
def check_due_tasks(window_minutes=DEFAULT_CHECK_WINDOW_MINUTES):
    now = datetime.now()
    window_end = now + timedelta(minutes=window_minutes)
    tasks = get_tasks(order_by_custom=False)
    upcoming = []
    for t in tasks:
        task_id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed = t
        try:
            due_dt = parse_iso_or_flex(due_iso) or datetime.fromisoformat(due_iso)
        except Exception:
            continue
        notify_flag = False
        if completed:
            continue
        if recurrence == 'once':
            if now <= due_dt <= window_end:
                if not last_notified_date:
                    notify_flag = True
        elif recurrence == 'daily':
            scheduled_today = datetime.combine(date.today(), due_dt.time())
            if now <= scheduled_today <= window_end:
                if last_notified_date != date.today().isoformat():
                    notify_flag = True
        if notify_flag:
            upcoming.append((task_id, title, description, due_dt, folder_path, priority))
    for task_id, title, description, due_dt, folder_path, priority in upcoming:
        msg = (description or '') + (f"\nPasta: {folder_path}" if folder_path else '')
        notify_os(f"Lembrete: {title} — {due_dt.strftime('%Y-%m-%d %H:%M')}", msg)
        set_task_notified_date(task_id, date.today().isoformat())
    return upcoming

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="Agenda Streamlit", layout="wide")
init_db()

st.title("📆 Agenda / Links (Streamlit)")

# tabs: Agenda, Calendar, Links, Config
tabs = st.tabs(["Agenda", "Calendar", "Links", "Config / Notificações"])

# ---------------- Agenda Tab ----------------
with tabs[0]:
    st.header("Adicionar nova tarefa")
    with st.form("add_task_form", clear_on_submit=True):
        col1, col2 = st.columns([3,2])
        with col1:
            title = st.text_input("Título", max_chars=200)
            description = st.text_area("Descrição (opcional)")
        with col2:
            due_date = st.date_input("Data (due date)", value=date.today())
            due_time = st.time_input("Hora", value=datetime.now().time().replace(second=0, microsecond=0))
            recurrence = st.selectbox("Recorrência", ['once','daily'], help="Escolha 'daily' para repetir diariamente (usa o horário).")
            priority_label = st.selectbox("Prioridade (rótulo)", ["Baixa","Média","Alta"])
            priority_map = {"Baixa": 1, "Média": 5, "Alta": 10}
            priority = priority_map[priority_label]
            folder_path = st.text_input("Pasta relacionada (opcional)", placeholder="Caminho local (abertura apenas local).")
        submitted = st.form_submit_button("Adicionar tarefa")
        if submitted:
            if not title:
                st.warning("Informe título e data/hora.")
            else:
                due_dt = datetime.combine(due_date, due_time)
                add_task(title.strip(), description.strip() or None, due_dt, recurrence, folder_path.strip() or None, priority)
                st.success("Tarefa adicionada.")
                st.experimental_rerun()

    st.markdown("---")
    st.header("Tarefas — lista e ações")
    show_completed = st.checkbox("Mostrar tarefas concluídas", value=False)
    tasks = get_tasks()
    rows = []
    for t in tasks:
        if not show_completed and t[9] == 1:
            continue
        rows.append(t)

    if not rows:
        st.info("Nenhuma tarefa cadastrada.")
    else:
        for t in rows:
            task_id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed = t
            due_dt = parse_iso_or_flex(due_iso) or (datetime.fromisoformat(due_iso) if due_iso else None)
            due_display = due_dt.strftime("%Y-%m-%d %H:%M") if due_dt else due_iso
            priority_label = "Alta" if priority >= 10 else ("Média" if priority >=5 else "Baixa")
            cols = st.columns([4,1,1,1,1,1])
            with cols[0]:
                st.markdown(f"**{title}**  {'✅' if completed else ''}")
                if description:
                    st.write(description)
                st.caption(f"Due: {due_display}  ·  Recorrência: {recurrence}  ·  Prioridade: {priority_label}  ·  Notificado: {last_notified_date or '—'}")
            # Move up / down
            if cols[1].button("⬆️", key=f"up_{task_id}"):
                all_tasks = get_tasks()
                idxs = [r[0] for r in all_tasks]
                try:
                    pos = idxs.index(task_id)
                    if pos > 0:
                        other_id = idxs[pos-1]
                        swap_sort_index(task_id, other_id)
                        st.experimental_rerun()
                except ValueError:
                    pass
            if cols[2].button("⬇️", key=f"down_{task_id}"):
                all_tasks = get_tasks()
                idxs = [r[0] for r in all_tasks]
                try:
                    pos = idxs.index(task_id)
                    if pos < len(idxs)-1:
                        other_id = idxs[pos+1]
                        swap_sort_index(task_id, other_id)
                        st.experimental_rerun()
                except ValueError:
                    pass
            # Edit
            if cols[3].button("✏️ Editar", key=f"edit_{task_id}"):
                st.session_state.edit_task_id = task_id
                st.experimental_rerun()
            # Complete toggle
            if cols[4].button("✅ Concluir" if not completed else "↺ Desmarcar", key=f"complete_{task_id}"):
                set_task_completed(task_id, not completed)
                st.experimental_rerun()
            # Delete
            if cols[5].button("🗑️ Excluir", key=f"del_{task_id}"):
                delete_task(task_id)
                st.experimental_rerun()
            # Open folder if exists
            if folder_path:
                try:
                    st.write(f"Pasta: `{folder_path}`")
                    if st.button("Abrir pasta", key=f"openp_{task_id}"):
                        opened = open_folder_local(folder_path)
                        if opened:
                            st.success("Tentativa de abrir pasta executada (local).")
                        else:
                            st.error("Não foi possível abrir a pasta localmente.")
                except Exception:
                    pass
            st.markdown("---")

    # Edit form (if requested)
    if "edit_task_id" in st.session_state:
        edit_id = st.session_state.get("edit_task_id")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT title,description,due_iso,recurrence,folder_path,priority FROM tasks WHERE id=?", (edit_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            e_title, e_description, e_due_iso, e_recurrence, e_folder, e_priority = row
            st.subheader("Editar tarefa")
            with st.form(f"edit_task_form_{edit_id}"):
                etitle = st.text_input("Título", value=e_title)
                edesc = st.text_area("Descrição", value=e_description or "")
                try:
                    edue_dt = parse_iso_or_flex(e_due_iso) or datetime.fromisoformat(e_due_iso)
                    edate = st.date_input("Data (due date)", value=edue_dt.date())
                    etime = st.time_input("Hora", value=edue_dt.time())
                except Exception:
                    edate = st.date_input("Data (due date)", value=date.today())
                    etime = st.time_input("Hora", value=datetime.now().time().replace(second=0,microsecond=0))
                erec = st.selectbox("Recorrência", ['once','daily'], index=0 if e_recurrence=='once' else 1)
                pr_map = {1:"Baixa", 5:"Média", 10:"Alta"}
                default_label = pr_map.get(e_priority, "Média")
                epriority_label = st.selectbox("Prioridade", ["Baixa","Média","Alta"], index=["Baixa","Média","Alta"].index(default_label))
                epriority = {"Baixa":1,"Média":5,"Alta":10}[epriority_label]
                efolder = st.text_input("Pasta relacionada (opcional)", value=e_folder or "")
                btn_save = st.form_submit_button("Salvar alterações")
                btn_cancel = st.form_submit_button("Cancelar edição")
                if btn_save:
                    new_due = datetime.combine(edate, etime)
                    update_task(edit_id, etitle.strip(), edesc.strip() or None, new_due, erec, efolder.strip() or None, epriority)
                    st.success("Tarefa atualizada.")
                    del st.session_state["edit_task_id"]
                    st.experimental_rerun()
                if btn_cancel:
                    del st.session_state["edit_task_id"]
                    st.experimental_rerun()

# ---------------- Calendar Tab ----------------
def tasks_by_day_map(year, month):
    rows = get_tasks(order_by_custom=False)
    m = {}
    for r in rows:
        task_id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed = r
        due_dt = parse_iso_or_flex(due_iso)
        if due_dt is None:
            try:
                due_dt = datetime.fromisoformat(due_iso)
            except Exception:
                continue
        if due_dt.year == year and due_dt.month == month:
            day = due_dt.day
            m.setdefault(day, []).append((task_id, title, description, due_dt, recurrence, priority, folder_path, completed))
    return m

with tabs[1]:
    st.header("📅 Calendário — visão mensal")
    col1, col2, col3 = st.columns([1,1,3])
    if "cal_year" not in st.session_state:
        st.session_state.cal_year = datetime.now().year
    if "cal_month" not in st.session_state:
        st.session_state.cal_month = datetime.now().month

    with col1:
        if st.button("◀️ Mês anterior"):
            y, m = st.session_state.cal_year, st.session_state.cal_month - 1
            if m < 1:
                m = 12; y -= 1
            st.session_state.cal_year, st.session_state.cal_month = y, m
            st.experimental_rerun()
    with col2:
        if st.button("Próximo mês ▶️"):
            y, m = st.session_state.cal_year, st.session_state.cal_month + 1
            if m > 12:
                m = 1; y += 1
            st.session_state.cal_year, st.session_state.cal_month = y, m
            st.experimental_rerun()
    with col3:
        st.markdown(f"### {st.session_state.cal_year} — {_pycalendar.month_name[st.session_state.cal_month]}")
        # quick jump (today)
        if st.button("Ir para hoje"):
            st.session_state.cal_year = datetime.now().year
            st.session_state.cal_month = datetime.now().month
            st.experimental_rerun()

    year = st.session_state.cal_year
    month = st.session_state.cal_month
    mapping = tasks_by_day_map(year, month)

    cal = _pycalendar.Calendar(firstweekday=0)
    month_matrix = cal.monthdayscalendar(year, month)

    # header weekdays
    weekday_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    cols_head = st.columns(7)
    for i, name in enumerate(weekday_names):
        with cols_head[i]:
            st.markdown(f"**{name}**")

    for week in month_matrix:
        cols = st.columns(7)
        for i, day in enumerate(week):
            with cols[i]:
                if day == 0:
                    st.write("")  # empty
                else:
                    day_tasks = mapping.get(day, [])
                    high = sum(1 for t in day_tasks if t[5] >= 10)
                    med  = sum(1 for t in day_tasks if 5 <= t[5] < 10)
                    low  = sum(1 for t in day_tasks if 1 <= t[5] < 5)
                    st.markdown(f"**{day}**")
                    if high:
                        st.markdown(f"<small>🔴 {high}</small>", unsafe_allow_html=True)
                    if med:
                        st.markdown(f"<small>🟠 {med}</small>", unsafe_allow_html=True)
                    if low:
                        st.markdown(f"<small>🟢 {low}</small>", unsafe_allow_html=True)
                    for t in day_tasks[:3]:
                        tid, title, desc, due_dt, rec, pr, folder, completed = t
                        time_str = due_dt.strftime("%H:%M")
                        st.write(f"- {time_str} • {title}" if title else f"- {time_str}")
                    if st.button(f"Ver {day}", key=f"viewday_{year}_{month}_{day}"):
                        st.session_state.selected_calendar_day = datetime(year, month, day)
                        st.experimental_rerun()
    st.markdown("---")
    if "selected_calendar_day" in st.session_state:
        sel = st.session_state.selected_calendar_day
        st.subheader(f"Compromissos em {sel.strftime('%Y-%m-%d')}")
        day_tasks = mapping.get(sel.day, [])
        if not day_tasks:
            st.info("Nenhum compromisso neste dia.")
        else:
            for t in day_tasks:
                tid, title, desc, due_dt, rec, pr, folder, completed = t
                pr_label = "Alta" if pr>=10 else ("Média" if pr>=5 else "Baixa")
                cols = st.columns([4,1,1])
                with cols[0]:
                    st.markdown(f"**{due_dt.strftime('%H:%M')} — {title}**  {'✅' if completed else ''}")
                    if desc:
                        st.write(desc)
                    st.caption(f"Recorrência: {rec}  ·  Prioridade: {pr_label}  ·  Pasta: {folder or '—'}")
                with cols[1]:
                    if st.button("✏️ Editar", key=f"cal_edit_{tid}"):
                        st.session_state.edit_task_id = tid
                        st.experimental_rerun()
                with cols[2]:
                    if st.button("🗑️ Excluir", key=f"cal_del_{tid}"):
                        delete_task(tid)
                        st.success("Tarefa excluída.")
                        st.session_state.selected_calendar_day = sel
                        st.experimental_rerun()
        st.markdown("### Adicionar tarefa neste dia")
        with st.form("add_task_calendar_form", clear_on_submit=False):
            atitle = st.text_input("Título", key="cal_new_title")
            adesc = st.text_area("Descrição", key="cal_new_desc")
            default_date = sel.date()
            adate = st.date_input("Data", value=default_date, key="cal_new_date")
            atime = st.time_input("Hora", value=datetime.now().time().replace(second=0,microsecond=0), key="cal_new_time")
            arecur = st.selectbox("Recorrência", ['once','daily'], key="cal_new_recur")
            aprio_label = st.selectbox("Prioridade", ["Baixa","Média","Alta"], key="cal_new_prio")
            aprio_map = {"Baixa":1,"Média":5,"Alta":10}
            aprio_val = aprio_map[aprio_label]
            afolder = st.text_input("Pasta (opcional)", key="cal_new_folder")
            sub = st.form_submit_button("Adicionar compromisso")
            if sub:
                if not atitle:
                    st.warning("Informe um título.")
                else:
                    new_dt = datetime.combine(adate, atime)
                    add_task(atitle.strip(), adesc.strip() or None, new_dt, arecur, afolder.strip() or None, aprio_val)
                    st.success("Tarefa adicionada.")
                    st.session_state.selected_calendar_day = sel
                    st.experimental_rerun()

# ---------------- Links Tab ----------------
with tabs[2]:
    st.header("Links")
    with st.form("add_link_form", clear_on_submit=True):
        lcol1, lcol2 = st.columns([3,1])
        with lcol1:
            name = st.text_input("Nome do link")
            url = st.text_input("URL (https://...)")
            folder = st.text_input("Pasta relacionada (opcional)")
        with lcol2:
            add = st.form_submit_button("Adicionar link")
        if add:
            if not name or not url:
                st.warning("Informe nome e URL.")
            else:
                if not is_valid_url(url):
                    st.warning("A URL não parece válida, mas será salva (se desejar evitar, corrija).")
                add_link(name.strip(), url.strip(), folder.strip() or None)
                st.success("Link adicionado")
                st.experimental_rerun()

    st.markdown("---")
    st.subheader("Lista de links")
    links = get_links()
    if not links:
        st.info("Nenhum link salvo.")
    else:
        for l in links:
            lid, lname, lurl, lfolder = l
            cols = st.columns([4,1,1,1])
            with cols[0]:
                st.markdown(f"**{lname}**")
                st.write(lurl)
                if lfolder:
                    st.caption(f"Pasta: {lfolder}")
            if cols[1].button("Abrir URL", key=f"openlink_{lid}"):
                try:
                    webbrowser.open(lurl)
                except Exception as e:
                    st.error(f"Erro ao abrir URL: {e}")
            if cols[2].button("Editar", key=f"editlink_{lid}"):
                st.session_state.edit_link_id = lid
                st.experimental_rerun()
            if cols[3].button("Excluir", key=f"dellink_{lid}"):
                delete_link(lid)
                st.experimental_rerun()
            if lfolder and st.button("Abrir pasta", key=f"openlinkfolder_{lid}"):
                opened = open_folder_local(lfolder)
                if opened:
                    st.success("Tentativa de abrir pasta executada (local).")
                else:
                    st.error("Não foi possível abrir a pasta localmente.")
            st.markdown("---")

    # Edit link if requested
    if "edit_link_id" in st.session_state:
        lid = st.session_state.get("edit_link_id")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name,url,folder_path FROM links WHERE id=?", (lid,))
        row = cur.fetchone()
        conn.close()
        if row:
            oname, ourl, ofolder = row
            st.subheader("Editar link")
            with st.form(f"edit_link_form_{lid}"):
                name2 = st.text_input("Nome", value=oname)
                url2 = st.text_input("URL", value=ourl)
                folder2 = st.text_input("Pasta (opcional)", value=ofolder or "")
                save = st.form_submit_button("Salvar")
                cancel = st.form_submit_button("Cancelar")
                if save:
                    update_link(lid, name2.strip(), url2.strip(), folder2.strip() or None)
                    st.success("Link atualizado.")
                    del st.session_state["edit_link_id"]
                    st.experimental_rerun()
                if cancel:
                    del st.session_state["edit_link_id"]
                    st.experimental_rerun()

# ---------------- Config / Notifications Tab ----------------
with tabs[3]:
    st.header("Notificações & Configurações")
    st.write("Verifique tarefas que vencem nos próximos N minutos e receba uma notificação (plyer) ou mensagens na interface.")
    window_minutes = st.number_input("Janela de busca (minutos)", min_value=1, max_value=24*60, value=DEFAULT_CHECK_WINDOW_MINUTES)
    if st.button("Checar lembretes agora"):
        upcoming = check_due_tasks(window_minutes)
        if upcoming:
            st.success(f"{len(upcoming)} lembrete(s) exibido(s).")
            for t in upcoming:
                st.write(f"- **{t[1]}** — {t[3].strftime('%Y-%m-%d %H:%M')} — prioridade {t[5]}")
        else:
            st.info("Nenhuma tarefa a notificar neste intervalo.")

    st.markdown("---")
    st.write("Opções avançadas / observações")
    st.write("""
    - A notificação automática em background **não** é ativada por padrão neste app Streamlit (threads em Streamlit podem ser instáveis dependendo do ambiente).  
    - Use 'Checar lembretes agora' para forçar avaliação local e receber notificações.
    - Abrir pastas funciona somente quando rodando localmente (com acesso ao sistema de arquivos).
    - Banco de dados local: `~/.streamlit_agenda.db`
    - Para deploy no Streamlit Cloud, remova `plyer` de requirements.txt (notificações desktop não funcionarão no servidor).
    """)

# End of file

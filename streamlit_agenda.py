# streamlit_agenda.py
# -*- coding: utf-8 -*-
"""
Agenda / Links em Streamlit com SQLite
- CRUD de Links e Tarefas
- Prioridade + ordenação por prioridade, devido (due) e sort_index (reordenação manual)
- Recorrência 'once' e 'daily'
- Marcar concluído / reset notificação
- Checar lembretes e enviar notificação via plyer se disponível
"""

import os
import sqlite3
from datetime import datetime, date, time, timedelta
import webbrowser
import platform
import subprocess

import streamlit as st

# Try plyer for OS notifications
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
        recurrence TEXT DEFAULT 'once', -- 'once' or 'daily'
        folder_path TEXT,
        priority INTEGER DEFAULT 0,     -- higher is more important
        sort_index INTEGER DEFAULT 0,   -- custom ordering (lower appears first)
        last_notified_date TEXT,        -- YYYY-MM-DD of last notification
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
    run_query(
        "INSERT INTO tasks (title, description, due_iso, recurrence, folder_path, priority, sort_index) VALUES (?,?,?,?,?,?,?)",
        (title, description, due_dt.isoformat(), recurrence, folder_path, int(priority), int(datetime.now().timestamp()))
    )

def get_tasks(order_by_custom=True):
    # order: sort_index asc (custom), priority desc, due_iso asc
    if order_by_custom:
        return run_query("SELECT id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed FROM tasks ORDER BY sort_index ASC, priority DESC, due_iso ASC", fetch=True)
    else:
        return run_query("SELECT id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed FROM tasks ORDER BY priority DESC, due_iso ASC", fetch=True)

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
    # swap the sort_index values of two tasks
    vals = run_query("SELECT id, sort_index FROM tasks WHERE id IN (?,?)", (task_id, other_id), fetch=True)
    if len(vals) != 2:
        return
    a_id, a_idx = vals[0]
    b_id, b_idx = vals[1]
    run_query("UPDATE tasks SET sort_index=? WHERE id=?", (b_idx, a_id))
    run_query("UPDATE tasks SET sort_index=? WHERE id=?", (a_idx, b_id))

# ---------------- Utilities ----------------
def is_valid_url(s):
    try:
        from urllib.parse import urlparse
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
    # prefer plyer; fallback to Streamlit message
    if HAS_PLYER:
        try:
            notification.notify(title=title, message=message, timeout=8)
            return True
        except Exception as e:
            st.warning(f"plyer notify falhou: {e}")
    # fallback: display in Streamlit UI
    st.info(f"NOTIFICAÇÃO: **{title}** — {message}")
    return False

def parse_datetime_from_inputs(date_obj, time_obj):
    if isinstance(date_obj, datetime):
        d = date_obj.date()
    else:
        d = date_obj
    if isinstance(time_obj, datetime):
        t = time_obj.time()
    else:
        t = time_obj
    return datetime.combine(d, t)

# ---------------- Notification checker ----------------
def check_due_tasks(window_minutes=DEFAULT_CHECK_WINDOW_MINUTES):
    now = datetime.now()
    window_end = now + timedelta(minutes=window_minutes)
    tasks = get_tasks(order_by_custom=False)  # simpler list
    upcoming = []
    for t in tasks:
        task_id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed = t
        try:
            due_dt = datetime.fromisoformat(due_iso)
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
    # send notifications and mark
    for task_id, title, description, due_dt, folder_path, priority in upcoming:
        msg = (description or '') + (f"\nPasta: {folder_path}" if folder_path else '')
        notify_os(f"Lembrete: {title} — {due_dt.strftime('%Y-%m-%d %H:%M')}", msg)
        set_task_notified_date(task_id, date.today().isoformat())
    return upcoming

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="Agenda Streamlit", layout="wide")
init_db()

st.title("📆 Agenda / Links")

tabs = st.tabs(["Agenda", "Links", "Config / Notificações"])

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
            folder_path = st.text_input("Pasta relacionada (opcional)", placeholder="Digite caminho local (ex: C:\\Users\\Allan\\Docs)")
        submitted = st.form_submit_button("Adicionar tarefa")
        if submitted:
            if not title:
                st.warning("Informe título e data/hora.")
            else:
                due_dt = parse_datetime_from_inputs(due_date, due_time)
                add_task(title.strip(), description.strip() or None, due_dt, recurrence, folder_path.strip() or None, priority)
                st.success("Tarefa adicionada.")
                st.experimental_rerun()

    st.markdown("---")
    st.header("Tarefas — lista e ações")
    show_completed = st.checkbox("Mostrar tarefas concluídas", value=False)
    tasks = get_tasks()
    # filter completed if needed
    rows = []
    for t in tasks:
        if not show_completed and t[9] == 1:
            continue
        rows.append(t)

    if not rows:
        st.info("Nenhuma tarefa cadastrada.")
    else:
        # Render each task with action buttons
        for t in rows:
            task_id, title, description, due_iso, recurrence, folder_path, priority, sort_index, last_notified_date, completed = t
            due_display = due_iso.replace('T',' ')
            priority_label = "Alta" if priority >= 10 else ("Média" if priority >=5 else "Baixa")
            cols = st.columns([4,1,1,1,1,1])
            with cols[0]:
                st.markdown(f"**{title}**  {'✅' if completed else ''}")
                if description:
                    st.write(description)
                st.caption(f"Due: {due_display}  ·  Recorrência: {recurrence}  ·  Prioridade: {priority_label}  ·  Notificado: {last_notified_date or '—'}")
            # Move up / down
            if cols[1].button("⬆️", key=f"up_{task_id}"):
                # find previous by sort_index
                # get all tasks ordered by sort_index
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
            # Open folder below (if exists)
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
        # load values
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
                # parse due
                try:
                    edue_dt = datetime.fromisoformat(e_due_iso)
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
                    new_due = parse_datetime_from_inputs(edate, etime)
                    update_task(edit_id, etitle.strip(), edesc.strip() or None, new_due, erec, efolder.strip() or None, epriority)
                    st.success("Tarefa atualizada.")
                    del st.session_state["edit_task_id"]
                    st.experimental_rerun()
                if btn_cancel:
                    del st.session_state["edit_task_id"]
                    st.experimental_rerun()

# ---------------- Links Tab ----------------
with tabs[1]:
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
with tabs[2]:
    st.header("Notificações & Configurações")
    st.write("Verifique tarefas que vencem nos próximos N minutos e receba uma notificação (plyer) ou mensagens na interface.")
    window_minutes = st.number_input("Janela de busca (minutos)", min_value=1, max_value=24*60, value=DEFAULT_CHECK_WINDOW_MINUTES)
    if st.button("Checar lembretes agora"):
        upcoming = check_due_tasks(window_minutes)
        if upcoming:
            st.success(f"{len(upcoming)} lembrete(s) exibido(s).")
            for t in upcoming:
                st.write(f"- **{t[1]}** — {t[3].strftime('%Y-%m-%d %H:%M')} — {t[5]}")
        else:
            st.info("Nenhuma tarefa a notificar neste intervalo.")

    st.markdown("---")
    st.write("Opções avançadas / observações")
    st.write("""
    - A notificação automática em background **não** é ativada por padrão neste app Streamlit (porque execução de threads em Streamlit pode ser instável dependendo do ambiente).  
    - Use 'Checar lembretes agora' para forçar avaliação local e receber notificações.
    - Abrir pastas funciona somente quando rodando localmente (com acesso ao sistema de arquivos).
    - Banco de dados: `~/.streamlit_agenda.db`
    """)

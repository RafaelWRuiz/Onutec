import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
from io import BytesIO
import os
DB_FILE = os.getenv("DB_FILE", "onutec.db")
# =========================
# Config
# =========================
st.set_page_config(page_title="Onutec | Dashboard Administrativo", layout="wide")

DB_FILE = "onutec.db"  # em produção (Render) use: DB_FILE = "/data/onutec.db"
USER_OK = "rafael"
PASS_OK = "onutec123"

# =========================
# Banco de Dados
# =========================
def run_query(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    if fetch:
        data = cur.fetchall()
        conn.close()
        return data
    conn.close()

def init_db():
    run_query("""CREATE TABLE IF NOT EXISTS comites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    periodo TEXT NOT NULL
                )""")
    run_query("""CREATE TABLE IF NOT EXISTS paises (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    comite_id INTEGER,
                    ocupado INTEGER DEFAULT 0,
                    FOREIGN KEY (comite_id) REFERENCES comites(id)
                )""")
    run_query("""CREATE TABLE IF NOT EXISTS inscricoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    aluno1_nome TEXT NOT NULL,
                    aluno1_whatsapp TEXT,
                    aluno1_serie TEXT NOT NULL,
                    aluno1_curso TEXT NOT NULL,
                    aluno2_nome TEXT NOT NULL,
                    aluno2_whatsapp TEXT,
                    aluno2_serie TEXT NOT NULL,
                    aluno2_curso TEXT NOT NULL,
                    periodo TEXT NOT NULL,
                    comite_id INTEGER,
                    pais_id INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (comite_id) REFERENCES comites(id),
                    FOREIGN KEY (pais_id) REFERENCES paises(id)
                )""")

def ensure_schema():
    # garante coluna created_at (caso DB antigo)
    cols = {row[1] for row in run_query("PRAGMA table_info(inscricoes)", fetch=True)}
    if "created_at" not in cols:
        run_query("ALTER TABLE inscricoes ADD COLUMN created_at TEXT")

init_db()
ensure_schema()

# =========================
# Auth (login simples)
# =========================
def require_login():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if st.session_state.logged_in:
        cols = st.columns([6,1])
        with cols[1]:
            if st.button("Sair"):
                st.session_state.logged_in = False
                st.rerun()
        return

    st.title("Login - Onutec (Admin)")
    with st.form("login"):
        u = st.text_input("Usuário", key="login_user")
        p = st.text_input("Senha", type="password", key="login_pass")
        ok = st.form_submit_button("Entrar")
        if ok:
            if u == USER_OK and p == PASS_OK:
                st.session_state.logged_in = True
                st.success("Acesso liberado.")
                st.rerun()
            else:
                st.error("Credenciais inválidas.")
    st.stop()

require_login()

# =========================
# Helpers
# =========================
def get_inscricoes_df(where_sql="", params=()):
    rows = run_query(f"""
        SELECT i.id, i.periodo, c.nome AS comite, p.nome AS pais,
               i.aluno1_nome, i.aluno1_serie, i.aluno1_curso, i.aluno1_whatsapp,
               i.aluno2_nome, i.aluno2_serie, i.aluno2_curso, i.aluno2_whatsapp
        FROM inscricoes i
        LEFT JOIN comites c ON i.comite_id = c.id
        LEFT JOIN paises  p ON i.pais_id   = p.id
        {where_sql}
        ORDER BY i.id DESC
    """, params, fetch=True)
    cols = ["ID","Período","Comitê","País",
            "Aluno1 Nome","Aluno1 Série","Aluno1 Curso","Aluno1 WhatsApp",
            "Aluno2 Nome","Aluno2 Série","Aluno2 Curso","Aluno2 WhatsApp"]
    return pd.DataFrame(rows, columns=cols)

def to_excel(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Inscricoes")
    return output.getvalue()

def try_delete_comite(comite_id: int):
    dep_paises = run_query("SELECT COUNT(*) FROM paises WHERE comite_id=?", (comite_id,), fetch=True)[0][0]
    dep_insc   = run_query("SELECT COUNT(*) FROM inscricoes WHERE comite_id=?", (comite_id,), fetch=True)[0][0]
    if dep_paises > 0 or dep_insc > 0:
        return False, f"Não é possível apagar: há {dep_paises} país(es) e {dep_insc} inscrição(ões) vinculados."
    run_query("DELETE FROM comites WHERE id=?", (comite_id,))
    return True, "Comitê apagado com sucesso."

def try_delete_pais(pais_id: int):
    dep_insc = run_query("SELECT COUNT(*) FROM inscricoes WHERE pais_id=?", (pais_id,), fetch=True)[0][0]
    if dep_insc > 0:
        return False, f"Não é possível apagar: há {dep_insc} inscrição(ões) usando este país."
    run_query("DELETE FROM paises WHERE id=?", (pais_id,))
    return True, "País apagado com sucesso."

def try_delete_inscricao(insc_id: int):
    row = run_query("SELECT pais_id FROM inscricoes WHERE id=?", (insc_id,), fetch=True)
    if not row:
        return False, "Inscrição não encontrada."
    pais_id = row[0][0]
    run_query("DELETE FROM inscricoes WHERE id=?", (insc_id,))
    if pais_id is not None:
        restam = run_query("SELECT COUNT(*) FROM inscricoes WHERE pais_id=?", (pais_id,), fetch=True)[0][0]
        if restam == 0:
            run_query("UPDATE paises SET ocupado=0 WHERE id=?", (pais_id,))
    return True, "Inscrição apagada e país liberado (se aplicável)."

# =========================
# Título + filtros
# =========================
st.title("Dashboard Administrativo - Onutec")
st.caption("Filtros (Períodos, Comitê, País)")

periodos = sorted({x[0] for x in run_query("SELECT DISTINCT periodo FROM comites", fetch=True)} |
                  {x[0] for x in run_query("SELECT DISTINCT periodo FROM inscricoes", fetch=True)})

comites_all = [x[0] for x in run_query("SELECT DISTINCT nome FROM comites ORDER BY nome", fetch=True)]
paises_all  = [x[0] for x in run_query("""
    SELECT DISTINCT p.nome FROM paises p 
    LEFT JOIN comites c ON p.comite_id=c.id
    ORDER BY p.nome
""", fetch=True)]

col_f1, col_f2, col_f3, col_f4 = st.columns([1.2,1.2,1.2,.9])
with col_f1:
    sel_periodo = st.multiselect("Períodos", periodos, default=[], key="f_periodos")
with col_f2:
    sel_comite  = st.multiselect("Comitês", comites_all, default=[], key="f_comites")
with col_f3:
    sel_pais    = st.multiselect("Países", paises_all, default=[], key="f_paises")
with col_f4:
    st.write(""); st.write("")
    if st.button("Limpar filtros", key="btn_limpar"):
        st.rerun()

# WHERE para inscrições (baseado nos filtros)
where = []
params = []
if sel_periodo:
    where.append(f"i.periodo IN ({','.join(['?']*len(sel_periodo))})")
    params += sel_periodo
if sel_comite:
    where.append(f"c.nome IN ({','.join(['?']*len(sel_comite))})")
    params += sel_comite
if sel_pais:
    where.append(f"p.nome IN ({','.join(['?']*len(sel_pais))})")
    params += sel_pais
where_sql = ("WHERE " + " AND ".join(where)) if where else ""

st.divider()

# =========================
# KPIs
# =========================
df_ins = get_inscricoes_df(where_sql, tuple(params))
k_comites = df_ins["Comitê"].nunique()
k_paises  = df_ins["País"].nunique()
k_insc    = len(df_ins)

# Ocupação (usa comitês do filtro, se houver; senão todos)
if sel_comite:
    comites_ids = [x[0] for x in run_query(
        f"SELECT id FROM comites WHERE nome IN ({','.join(['?']*len(sel_comite))})",
        tuple(sel_comite), fetch=True)]
else:
    comites_ids = [x[0] for x in run_query("SELECT id FROM comites", fetch=True)]

if comites_ids:
    marks = ",".join(["?"]*len(comites_ids))
    total_paises_escopo = run_query(f"SELECT COUNT(*) FROM paises WHERE comite_id IN ({marks})",
                                    tuple(comites_ids), fetch=True)[0][0]
    ocupados_escopo     = run_query(f"SELECT COUNT(*) FROM paises WHERE ocupado=1 AND comite_id IN ({marks})",
                                    tuple(comites_ids), fetch=True)[0][0]
    taxa_ocup = int((ocupados_escopo/total_paises_escopo)*100) if total_paises_escopo else 0
else:
    taxa_ocup = 0

k1, k2, k3, k4 = st.columns(4)
k1.metric("Comitês", k_comites)
k2.metric("Países", k_paises)
k3.metric("Inscrições", k_insc)
k4.metric("Ocupação", f"{taxa_ocup}%")

st.divider()

# =========================
# Linha: Inscrições | Status Comitê
# =========================
c1, c2 = st.columns(2)

with c1:
    st.subheader("Inscrições")
    if df_ins.empty:
        st.info("Nenhuma inscrição encontrada com os filtros atuais.")
    else:
        head = st.columns([0.3, 0.9, 1.1, 1.1, 1.6, 1.6, 0.7])
        head[0].markdown("**ID**"); head[1].markdown("**Período**")
        head[2].markdown("**Comitê**"); head[3].markdown("**País**")
        head[4].markdown("**Aluno 1**"); head[5].markdown("**Aluno 2**")
        head[6].markdown("**Ações**")

        for _, row in df_ins.iterrows():
            col_id, col_per, col_com, col_pais, col_a1, col_a2, col_btn = st.columns([0.3, 0.9, 1.1, 1.1, 1.6, 1.6, 0.7])
            with col_id:  st.write(int(row["ID"]))
            with col_per: st.write(row["Período"])
            with col_com: st.write(row["Comitê"])
            with col_pais:st.write(row["País"])
            with col_a1:  st.write(f"{row['Aluno1 Nome']} ({row['Aluno1 Série']}, {row['Aluno1 Curso']})")
            with col_a2:  st.write(f"{row['Aluno2 Nome']} ({row['Aluno2 Série']}, {row['Aluno2 Curso']})")
            with col_btn:
                if st.button("Apagar", key=f"delins_{int(row['ID'])}"):
                    ok, msg = try_delete_inscricao(int(row["ID"]))
                    if ok: st.success(msg); st.rerun()
                    else:  st.error(msg)

with c2:
    st.subheader("Status Comitê")
    if comites_ids:
        marks = ",".join(["?"]*len(comites_ids))
        pais_rows = run_query(f"""
            SELECT c.nome AS comite, 
                   CASE WHEN p.ocupado=1 THEN 'Ocupado' ELSE 'Livre' END AS status, 
                   COUNT(*) AS qtd
            FROM paises p
            LEFT JOIN comites c ON p.comite_id=c.id
            WHERE p.comite_id IN ({marks})
            GROUP BY c.nome, status
            ORDER BY c.nome
        """, tuple(comites_ids), fetch=True)
        if pais_rows:
            df_stat = pd.DataFrame(pais_rows, columns=["Comitê","Status","Qtd"])
            tabela = df_stat.pivot_table(index="Comitê", columns="Status", values="Qtd", fill_value=0)
            st.dataframe(tabela, use_container_width=True)
        else:
            st.info("Sem países cadastrados nos comitês selecionados.")
    else:
        st.info("Cadastre comitês e países para visualizar o status.")

st.divider()

# =========================
# Gerenciar Comitês (segue filtro)
# =========================
st.subheader("Gerenciar Comitês")

with st.form("form_comite", clear_on_submit=True):
    cc1, cc2, cc3 = st.columns([1.4, 1, .8])
    with cc1:
        nome_comite = st.text_input("Nome do Comitê", key="f_nome_comite")
    with cc2:
        base_periodos = ["Manhã","Tarde","Noite"]
        idx_default = base_periodos.index(sel_periodo[0]) if len(sel_periodo)==1 and sel_periodo[0] in base_periodos else 0
        periodo_comite = st.selectbox("Período", base_periodos, index=idx_default, key="f_periodo_comite")
    with cc3:
        salvar_comite = st.form_submit_button("Adicionar")
    if salvar_comite:
        if nome_comite.strip():
            run_query("INSERT INTO comites (nome, periodo) VALUES (?,?)",
                      (nome_comite.strip(), periodo_comite))
            st.success("Comitê adicionado!")
            st.rerun()
        else:
            st.warning("Informe o nome do comitê.")

where_c = []
params_c = []
if sel_periodo:
    where_c.append(f"periodo IN ({','.join(['?']*len(sel_periodo))})"); params_c += sel_periodo
if sel_comite:
    where_c.append(f"nome IN ({','.join(['?']*len(sel_comite))})"); params_c += sel_comite
where_sql_c = ("WHERE " + " AND ".join(where_c)) if where_c else ""
df_comites = pd.DataFrame(
    run_query(f"SELECT id, nome, periodo FROM comites {where_sql_c} ORDER BY periodo, nome",
              tuple(params_c), fetch=True),
    columns=["ID","Nome","Período"]
)

if df_comites.empty:
    st.info("Nenhum comitê no filtro atual.")
else:
    head_c = st.columns([0.3, 1.4, 1.0, 0.7])
    head_c[0].markdown("**ID**"); head_c[1].markdown("**Nome**")
    head_c[2].markdown("**Período**"); head_c[3].markdown("**Ações**")

    for _, row in df_comites.iterrows():
        col_a, col_b, col_cx, col_d = st.columns([0.3, 1.4, 1.0, 0.7])
        with col_a: st.write(row["ID"])
        with col_b: st.write(row["Nome"])
        with col_cx: st.write(row["Período"])
        with col_d:
            if st.button("Apagar", key=f"delc_{int(row['ID'])}"):
                ok, msg = try_delete_comite(int(row["ID"]))
                if ok: st.success(msg); st.rerun()
                else:  st.error(msg)

st.divider()

# =========================
# Gerenciar Países (segue filtro)
# =========================
st.subheader("Gerenciar Países")

where_cc = []
params_cc = []
if sel_periodo:
    where_cc.append(f"periodo IN ({','.join(['?']*len(sel_periodo))})"); params_cc += sel_periodo
if sel_comite:
    where_cc.append(f"nome IN ({','.join(['?']*len(sel_comite))})"); params_cc += sel_comite
where_sql_cc = ("WHERE " + " AND ".join(where_cc)) if where_cc else ""
comites_opts = run_query(f"SELECT id, nome, periodo FROM comites {where_sql_cc} ORDER BY periodo, nome",
                         tuple(params_cc), fetch=True)

with st.form("form_pais", clear_on_submit=True):
    pc1, pc2, pc3 = st.columns([1.4, 1.4, .8])
    with pc1:
        nome_pais = st.text_input("Nome do País", key="f_nome_pais")
    with pc2:
        if comites_opts:
            opts_fmt = [f"{n} - {p} (ID {i})" for (i,n,p) in comites_opts]
            default_idx = 0
            if len(sel_comite) == 1:
                for idx, (_, n, _) in enumerate(comites_opts):
                    if n == sel_comite[0]: default_idx = idx; break
            opt = st.selectbox("Comitê", opts_fmt, index=default_idx, key="f_sel_comite_pais")
            comite_id_sel = int(opt.split("ID ")[1].strip(")"))
        else:
            st.info("Nenhum comitê dentro do filtro atual.")
            comite_id_sel = None
    with pc3:
        salvar_pais = st.form_submit_button("Adicionar País")

    if salvar_pais:
        if not nome_pais.strip():
            st.warning("Informe o nome do país.")
        elif comite_id_sel is None:
            st.warning("Selecione um comitê.")
        else:
            run_query("INSERT INTO paises (nome, comite_id, ocupado) VALUES (?,?,0)",
                      (nome_pais.strip(), comite_id_sel))
            st.success("País adicionado!")
            st.rerun()

# lista de países filtrada
where_p = []
params_p = []
if sel_periodo:
    where_p.append(f"c.periodo IN ({','.join(['?']*len(sel_periodo))})"); params_p += sel_periodo
if sel_comite:
    where_p.append(f"c.nome IN ({','.join(['?']*len(sel_comite))})"); params_p += sel_comite
if sel_pais:
    where_p.append(f"p.nome IN ({','.join(['?']*len(sel_pais))})"); params_p += sel_pais
where_sql_p = ("WHERE " + " AND ".join(where_p)) if where_p else ""

df_paises = pd.DataFrame(
    run_query(f"""
        SELECT p.id, p.nome, c.nome, c.periodo
        FROM paises p LEFT JOIN comites c ON p.comite_id=c.id
        {where_sql_p}
        ORDER BY c.periodo, c.nome, p.nome
    """, tuple(params_p), fetch=True),
    columns=["ID","País","Comitê","Período"]
)

if df_paises.empty:
    st.info("Nenhum país no filtro atual.")
else:
    head_p = st.columns([0.3, 1.2, 1.2, 1.0, 0.7])
    head_p[0].markdown("**ID**"); head_p[1].markdown("**País**")
    head_p[2].markdown("**Comitê**"); head_p[3].markdown("**Período**")
    head_p[4].markdown("**Ações**")

    for _, row in df_paises.iterrows():
        col1, col2, col3, col4, col5 = st.columns([0.3, 1.2, 1.2, 1.0, 0.7])
        with col1: st.write(int(row["ID"]))
        with col2: st.write(row["País"])
        with col3: st.write(row["Comitê"])
        with col4: st.write(row["Período"])
        with col5:
            if st.button("Apagar", key=f"delp_{int(row['ID'])}"):
                ok, msg = try_delete_pais(int(row["ID"]))
                if ok: st.success(msg); st.rerun()
                else:  st.error(msg)

st.divider()

# =========================
# Exportar inscrições (Excel)
# =========================
def exportar(df):
    return to_excel(df)

with st.expander("Exportar inscrições (Excel)"):
    st.download_button(
        "Baixar Excel",
        data=exportar(df_ins),
        file_name=f"inscricoes_onutec_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="btn_export"
    )

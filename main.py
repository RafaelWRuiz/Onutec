import os
import sqlite3
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

# --- esconder menu/toolbar/footer do Streamlit ---
st.markdown("""
<style>
/* menu (3 pontinhos) */
#MainMenu {visibility: hidden;}
/* botão de header em alguns temas */
header [data-testid="baseButton-headerNoPadding"]{ display:none !important; }
/* toolbar nova */
div[data-testid="stToolbar"]{ display:none !important; }
/* footer */
footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ========= Config básica =========
st.set_page_config(page_title="Onutec", layout="wide")
DB_FILE = os.getenv("DB_FILE", "onutec.db")   # no Render (Starter) use /data/onutec.db
USER_OK = os.getenv("ADMIN_USER", "rafael")
PASS_OK = os.getenv("ADMIN_PASS", "onutec123")

# ========= DB helpers (SQLite) =========
def run_query(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    data = cur.fetchall() if fetch else None
    cur.close()
    conn.close()
    return data

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
    # índice único: impede duas inscrições para o mesmo país
    run_query("CREATE UNIQUE INDEX IF NOT EXISTS uniq_insc_pais ON inscricoes(pais_id) WHERE pais_id IS NOT NULL")

def ensure_schema():
    cols = {r[1] for r in run_query("PRAGMA table_info(inscricoes)", fetch=True)}
    if "created_at" not in cols:
        run_query("ALTER TABLE inscricoes ADD COLUMN created_at TEXT")
    # re-garante índice (caso o banco exista de antes)
    run_query("CREATE UNIQUE INDEX IF NOT EXISTS uniq_insc_pais ON inscricoes(pais_id) WHERE pais_id IS NOT NULL")

init_db()
ensure_schema()

# ========= Utilitários =========
def to_excel(df: pd.DataFrame) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Inscricoes")
    return out.getvalue()

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

# ========= Transação de inscrição (robusta contra corrida) =========
def inscrever_dupla_tx(periodo, comite_id, pais_id,
                       a1_nome, a1_whats, a1_serie, a1_curso,
                       a2_nome, a2_whats, a2_serie, a2_curso):
    """
    Executa a inscrição e reserva do país numa transação atômica.
    Retorna (ok: bool, payload_ou_msg: dict|str)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Exclusão mútua até commit/rollback
        conn.execute("BEGIN IMMEDIATE")

        # Reconfirma que o país segue livre
        cur = conn.execute("SELECT ocupado FROM paises WHERE id=? LIMIT 1", (pais_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return False, "País inválido."
        if row[0] == 1:
            conn.rollback()
            return False, "Esse país acabou de ser escolhido por outra dupla. Por favor, selecione outro."

        # Marca país e grava inscrição
        conn.execute("UPDATE paises SET ocupado=1 WHERE id=?", (pais_id,))
        conn.execute("""
            INSERT INTO inscricoes (
                aluno1_nome, aluno1_whatsapp, aluno1_serie, aluno1_curso,
                aluno2_nome, aluno2_whatsapp, aluno2_serie, aluno2_curso,
                periodo, comite_id, pais_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (a1_nome, a1_whats, a1_serie, a1_curso,
              a2_nome, a2_whats, a2_serie, a2_curso,
              periodo, comite_id, pais_id, now))

        # Monta recibo
        insc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        comite_nome = conn.execute("SELECT nome FROM comites WHERE id=?", (comite_id,)).fetchone()[0]
        pais_nome   = conn.execute("SELECT nome FROM paises  WHERE id=?", (pais_id,)).fetchone()[0]

        conn.commit()
        return True, {
            "id": insc_id, "periodo": periodo,
            "comite": comite_nome, "pais": pais_nome,
            "a1": a1_nome, "a2": a2_nome
        }

    except sqlite3.IntegrityError:
        conn.rollback()
        return False, "Esse país acabou de ser escolhido por outra dupla. Por favor, selecione outro."
    except Exception as e:
        conn.rollback()
        return False, f"Ocorreu um erro ao salvar: {e}"
    finally:
        conn.close()

# ========= PÁGINA: formulário público =========
def pagina_inscricao():
    st.title("Inscrição em Dupla – Onutec")
    st.info("Preencha os dados. A inscrição é em dupla e cada dupla escolhe **1 país disponível** do comitê.")

    # ============= Tela pós-inscrição (esconde formulário) =============
    if st.session_state.get("insc_ok"):
        r = st.session_state["insc_resumo"]
        st.success("✅ Inscrição realizada com sucesso!")
        st.markdown(f"""
**Protocolo:** #{r['id']}  
**Período:** {r['periodo']}  
**Comitê:** {r['comite']}  
**País:** {r['pais']}  
**Dupla:** {r['a1']} e {r['a2']}
""")
        st.divider()
        if st.button("➕ Fazer nova inscrição", key="nova_insc"):
            for key in [
                "a1_nome","a1_whats","a1_serie","a1_curso",
                "a2_nome","a2_whats","a2_serie","a2_curso",
                "sel_pais_id","pending_pais_id","insc_ok","insc_resumo","submitting"
            ]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
        return

    # ====== Seleções dinâmicas fora do form (atualizam a lista) ======
    periodo = st.selectbox("Período", ["Manhã", "Tarde", "Noite"], key="sel_periodo")

    comites = run_query(
        "SELECT id, nome FROM comites WHERE periodo=? ORDER BY nome",
        (periodo,), fetch=True
    )
    comites_disp = []
    for cid, nome in comites:
        livres = run_query("SELECT COUNT(*) FROM paises WHERE comite_id=? AND ocupado=0", (cid,), fetch=True)[0][0]
        if livres > 0:
            comites_disp.append((cid, nome))
    if not comites_disp:
        st.warning("No momento não há comitês com países disponíveis nesse período.")
        return

    map_com = {f"{n} (ID {i})": i for i, n in comites_disp}
    label_com = st.selectbox("Comitê", list(map_com.keys()), key="sel_comite")
    comite_id = map_com[label_com]

    # Países LIVRES do comitê (carregados fora do form)
    rows_p = run_query(
        "SELECT id, nome FROM paises WHERE comite_id=? AND ocupado=0 ORDER BY nome",
        (comite_id,), fetch=True
    )
    if not rows_p:
        st.warning("Este comitê ficou sem países livres. Escolha outro comitê.")
        return

    # ----- mapeamentos -----
    id_to_name = {pid: nome for pid, nome in rows_p}
    free_ids = list(id_to_name.keys())

    # guardamos o último país escolhido (congela a opção no clique)
    def _on_change_pais():
        st.session_state["pending_pais_id"] = st.session_state.get("sel_pais_id")

    # se o último escolhido sumiu dos livres (porque ficou ocupado),
    # reinsere provisoriamente para não cair no 1º item
    pending = st.session_state.get("pending_pais_id")
    options_ids = free_ids.copy()
    if pending is not None and pending not in options_ids:
        options_ids.append(pending)

    # rótulo do select; se estava pendente e não está livre, marcamos como indisponível
    def _fmt(pid: int) -> str:
        nome = id_to_name.get(pid, f"ID {pid}")
        if pending is not None and pid == pending and pid not in free_ids:
            return f"{nome} (ID {pid}) — ⚠️ agora indisponível"
        return f"{nome} (ID {pid})"

    st.caption("⚠️ A lista de países pode mudar se outra dupla confirmar a inscrição primeiro.")
    st.divider()

    # ====== Formulário (valor do select é o ID estável) ======
    with st.form("form_inscricao", clear_on_submit=False):
        default_idx = 0
        if pending is not None and pending in options_ids:
            default_idx = options_ids.index(pending)

        sel_pid = st.selectbox(
            "País",
            options=options_ids,
            index=default_idx,
            format_func=_fmt,
            key="sel_pais_id",
            on_change=_on_change_pais,
        )
        # inicializa pending se ainda não existe
        if "pending_pais_id" not in st.session_state:
            st.session_state["pending_pais_id"] = sel_pid

        st.subheader("Aluno 1")
        a1_nome  = st.text_input("Nome completo do Aluno 1*", key="a1_nome")
        a1_whats = st.text_input("WhatsApp (opcional)", key="a1_whats")
        a1_serie = st.text_input("Série* (ex.: 2º Ano)", key="a1_serie")
        a1_curso = st.text_input("Curso* (ex.: DS, Nutrição)", key="a1_curso")

        st.subheader("Aluno 2")
        a2_nome  = st.text_input("Nome completo do Aluno 2*", key="a2_nome")
        a2_whats = st.text_input("WhatsApp (opcional)", key="a2_whats")
        a2_serie = st.text_input("Série* (ex.: 2º Ano)", key="a2_serie")
        a2_curso = st.text_input("Curso* (ex.: DS, Nutrição)", key="a2_curso")

        st.caption("* Campos obrigatórios")

        send_disabled = st.session_state.get("submitting", False)
        submitted = st.form_submit_button("Enviar Inscrição", disabled=send_disabled)

    if not submitted:
        return

    # trava anti duplo clique
    st.session_state["submitting"] = True

    # validações
    faltando = []
    if not a1_nome.strip():  faltando.append("Aluno 1 - Nome")
    if not a1_serie.strip(): faltando.append("Aluno 1 - Série")
    if not a1_curso.strip(): faltando.append("Aluno 1 - Curso")
    if not a2_nome.strip():  faltando.append("Aluno 2 - Nome")
    if not a2_serie.strip(): faltando.append("Aluno 2 - Série")
    if not a2_curso.strip(): faltando.append("Aluno 2 - Curso")
    if faltando:
        st.error("Preencha: " + ", ".join(faltando))
        st.session_state["submitting"] = False
        return

    # usamos SEMPRE o pending (valor congelado no clique)
    pais_id = st.session_state.get("pending_pais_id", sel_pid)

    # ============= Transação atômica: ocupa país SE e SOMENTE SE estiver livre =============
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    try:
        # tenta marcar como ocupado apenas se estava 0 (livre)
        cur.execute("UPDATE paises SET ocupado=1 WHERE id=? AND ocupado=0", (pais_id,))
        if cur.rowcount == 0:
            conn.rollback()
            st.error("Esse país acabou de ser escolhido por outra dupla. Por favor, selecione outro.")
            st.session_state["submitting"] = False
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO inscricoes (
                aluno1_nome, aluno1_whatsapp, aluno1_serie, aluno1_curso,
                aluno2_nome, aluno2_whatsapp, aluno2_serie, aluno2_curso,
                periodo, comite_id, pais_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (a1_nome.strip(), a1_whats.strip(), a1_serie.strip(), a1_curso.strip(),
              a2_nome.strip(), a2_whats.strip(), a2_serie.strip(), a2_curso.strip(),
              periodo, comite_id, pais_id, now))
        insc_id = cur.lastrowid

        comite_nome = cur.execute("SELECT nome FROM comites WHERE id=?", (comite_id,)).fetchone()[0]
        pais_nome   = cur.execute("SELECT nome FROM paises  WHERE id=?", (pais_id,)).fetchone()[0]

        conn.commit()

        st.session_state["insc_ok"] = True
        st.session_state["insc_resumo"] = {
            "id": insc_id, "periodo": periodo, "comite": comite_nome,
            "pais": pais_nome, "a1": a1_nome, "a2": a2_nome
        }
        st.session_state["submitting"] = False
        st.rerun()

    except Exception as e:
        conn.rollback()
        st.error(f"Ocorreu um erro: {e}")
        st.session_state["submitting"] = False
    finally:
        cur.close()
        conn.close()



# ========= PÁGINA: admin (com login) =========
def require_login():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if st.session_state.logged_in:
        colx = st.columns([6,1])[1]
        with colx:
            if st.button("Sair"):
                st.session_state.logged_in = False
                st.rerun()
        return
    st.title("Login - Onutec (Admin)")
    with st.form("login"):
        u = st.text_input("Usuário", key="login_user")
        p = st.text_input("Senha", type="password", key="login_pass")
        if st.form_submit_button("Entrar"):
            if u == USER_OK and p == PASS_OK:
                st.session_state.logged_in = True
                st.success("Acesso liberado.")
                st.rerun()
            else:
                st.error("Credenciais inválidas.")
    st.stop()

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

def pagina_admin():
    require_login()

    st.title("Dashboard Administrativo - Onutec")
    st.caption("Filtros (Períodos, Comitê, País)")

    periodos = sorted({x[0] for x in run_query("SELECT DISTINCT periodo FROM comites", fetch=True)} |
                      {x[0] for x in run_query("SELECT DISTINCT periodo FROM inscricoes", fetch=True)})
    comites_all = [x[0] for x in run_query("SELECT DISTINCT nome FROM comites ORDER BY nome", fetch=True)]
    paises_all  = [x[0] for x in run_query("""SELECT DISTINCT p.nome FROM paises p 
                                              LEFT JOIN comites c ON p.comite_id=c.id
                                              ORDER BY p.nome""", fetch=True)]

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

    where, params = [], []
    if sel_periodo:
        where.append(f"i.periodo IN ({','.join(['?']*len(sel_periodo))})"); params += sel_periodo
    if sel_comite:
        where.append(f"c.nome IN ({','.join(['?']*len(sel_comite))})"); params += sel_comite
    if sel_pais:
        where.append(f"p.nome IN ({','.join(['?']*len(sel_pais))})"); params += sel_pais
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    st.divider()

    df_ins = get_inscricoes_df(where_sql, tuple(params))
    k_comites = df_ins["Comitê"].nunique()
    k_paises  = df_ins["País"].nunique()
    k_insc    = len(df_ins)

    if sel_comite:
        comites_ids = [x[0] for x in run_query(
            f"SELECT id FROM comites WHERE nome IN ({','.join(['?']*len(sel_comite))})",
            tuple(sel_comite), fetch=True)]
    else:
        comites_ids = [x[0] for x in run_query("SELECT id FROM comites", fetch=True)]

    if comites_ids:
        marks = ",".join(["?"]*len(comites_ids))
        total_p = run_query(f"SELECT COUNT(*) FROM paises WHERE comite_id IN ({marks})",
                            tuple(comites_ids), fetch=True)[0][0]
        ocup_p  = run_query(f"SELECT COUNT(*) FROM paises WHERE ocupado=1 AND comite_id IN ({marks})",
                            tuple(comites_ids), fetch=True)[0][0]
        taxa = int((ocup_p/total_p)*100) if total_p else 0
    else: 
        taxa = 0

    k1,k2,k3,k4 = st.columns(4)
    k1.metric("Comitês", k_comites); k2.metric("Países", k_paises)
    k3.metric("Inscrições", k_insc); k4.metric("Ocupação", f"{taxa}%")

    st.divider()

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Inscrições")
        if df_ins.empty:
            st.info("Nenhuma inscrição com os filtros atuais.")
        else:
            head = st.columns([0.3, 0.9, 1.1, 1.1, 1.6, 1.6, 0.7])
            for i, txt in enumerate(["ID","Período","Comitê","País","Aluno 1","Aluno 2","Ações"]):
                head[i].markdown(f"**{txt}**")
            for _, r in df_ins.iterrows():
                col_id, col_per, col_com, col_pais, col_a1, col_a2, col_btn = st.columns([0.3,0.9,1.1,1.1,1.6,1.6,0.7])
                col_id.write(int(r["ID"]))
                col_per.write(r["Período"]); col_com.write(r["Comitê"]); col_pais.write(r["País"])
                col_a1.write(f"{r['Aluno1 Nome']} ({r['Aluno1 Série']}, {r['Aluno1 Curso']})")
                col_a2.write(f"{r['Aluno2 Nome']} ({r['Aluno2 Série']}, {r['Aluno2 Curso']})")
                if col_btn.button("Apagar", key=f"dli_{int(r['ID'])}"):
                    ok, msg = try_delete_inscricao(int(r["ID"]))
                    if ok: st.success(msg); st.rerun()
                    else:  st.error(msg)

    with c2:
        st.subheader("Status Comitê")
        if comites_ids:
            marks = ",".join(["?"]*len(comites_ids))
            rows = run_query(f"""
                SELECT c.nome AS comite, 
                       CASE WHEN p.ocupado=1 THEN 'Ocupado' ELSE 'Livre' END AS status, 
                       COUNT(*) AS qtd
                FROM paises p
                LEFT JOIN comites c ON p.comite_id=c.id
                WHERE p.comite_id IN ({marks})
                GROUP BY c.nome, status
                ORDER BY c.nome
            """, tuple(comites_ids), fetch=True)
            if rows:
                df_stat = pd.DataFrame(rows, columns=["Comitê","Status","Qtd"])
                tabela  = df_stat.pivot_table(index="Comitê", columns="Status", values="Qtd", fill_value=0)
                st.dataframe(tabela, use_container_width=True)
            else:
                st.info("Sem países cadastrados nos comitês selecionados.")
        else:
            st.info("Cadastre comitês e países para ver o status.")

    st.divider()

    st.subheader("Gerenciar Comitês")
    with st.form("form_comite", clear_on_submit=True):
        cc1, cc2, cc3 = st.columns([1.4,1,.8])
        nome_comite = cc1.text_input("Nome do Comitê", key="f_nome_comite")
        idx_default = 0
        base_periodos = ["Manhã","Tarde","Noite"]
        if "f_periodos" in st.session_state and len(st.session_state["f_periodos"])==1:
            try: idx_default = base_periodos.index(st.session_state["f_periodos"][0])
            except: pass
        periodo_comite = cc2.selectbox("Período", base_periodos, index=idx_default, key="f_periodo_comite")
        if cc3.form_submit_button("Adicionar"):
            if nome_comite.strip():
                run_query("INSERT INTO comites (nome, periodo) VALUES (?,?)",
                          (nome_comite.strip(), periodo_comite))
                st.success("Comitê adicionado!"); st.rerun()
            else:
                st.warning("Informe o nome do comitê.")

    where_c, params_c = [], []
    if st.session_state.get("f_periodos"): where_c.append(f"periodo IN ({','.join(['?']*len(st.session_state['f_periodos']))})") or params_c.extend(st.session_state["f_periodos"])
    if st.session_state.get("f_comites"):  where_c.append(f"nome IN ({','.join(['?']*len(st.session_state['f_comites']))})") or params_c.extend(st.session_state["f_comites"])
    where_sql_c = ("WHERE " + " AND ".join(where_c)) if where_c else ""
    df_comites = pd.DataFrame(run_query(f"SELECT id, nome, periodo FROM comites {where_sql_c} ORDER BY periodo, nome",
                                        tuple(params_c), fetch=True), columns=["ID","Nome","Período"])
    if df_comites.empty:
        st.info("Nenhum comitê no filtro atual.")
    else:
        head_c = st.columns([0.3,1.4,1.0,0.7])
        for i, txt in enumerate(["ID","Nome","Período","Ações"]): head_c[i].markdown(f"**{txt}**")
        for _, r in df_comites.iterrows():
            c1,c2,c3,c4 = st.columns([0.3,1.4,1.0,0.7])
            c1.write(r["ID"]); c2.write(r["Nome"]); c3.write(r["Período"])
            if c4.button("Apagar", key=f"delc_{int(r['ID'])}"):
                ok, msg = try_delete_comite(int(r["ID"]))
                if ok: st.success(msg); st.rerun()
                else:  st.error(msg)

    st.divider()

    st.subheader("Gerenciar Países")
    # opções para select (respeitando filtro)
    where_cc, params_cc = [], []
    if st.session_state.get("f_periodos"): where_cc.append(f"periodo IN ({','.join(['?']*len(st.session_state['f_periodos']))})") or params_cc.extend(st.session_state["f_periodos"])
    if st.session_state.get("f_comites"):  where_cc.append(f"nome IN ({','.join(['?']*len(st.session_state['f_comites']))})") or params_cc.extend(st.session_state["f_comites"])
    where_sql_cc = ("WHERE " + " AND ".join(where_cc)) if where_cc else ""
    comites_opts = run_query(f"SELECT id, nome, periodo FROM comites {where_sql_cc} ORDER BY periodo, nome",
                             tuple(params_cc), fetch=True)

    with st.form("form_pais", clear_on_submit=True):
        pc1, pc2, pc3 = st.columns([1.4,1.4,.8])
        nome_pais = pc1.text_input("Nome do País", key="f_nome_pais")
        if comites_opts:
            opts_fmt = [f"{n} - {p} (ID {i})" for (i,n,p) in comites_opts]
            default_idx = 0
            f_comites = st.session_state.get("f_comites", [])
            if len(f_comites)==1:
                for idx, (_, n, _) in enumerate(comites_opts):
                    if n == f_comites[0]: default_idx = idx; break
            opt = pc2.selectbox("Comitê", opts_fmt, index=default_idx, key="f_sel_comite_pais")
            comite_id_sel = int(opt.split("ID ")[1].strip(")"))
        else:
            pc2.info("Nenhum comitê no filtro atual.")
            comite_id_sel = None
        if pc3.form_submit_button("Adicionar País"):
            if not nome_pais.strip():
                st.warning("Informe o nome do país.")
            elif comite_id_sel is None:
                st.warning("Selecione um comitê.")
            else:
                run_query("INSERT INTO paises (nome, comite_id, ocupado) VALUES (?,?,0)",
                          (nome_pais.strip(), comite_id_sel))
                st.success("País adicionado!"); st.rerun()

    where_p, params_p = [], []
    if st.session_state.get("f_periodos"): where_p.append(f"c.periodo IN ({','.join(['?']*len(st.session_state['f_periodos']))})") or params_p.extend(st.session_state["f_periodos"])
    if st.session_state.get("f_comites"):  where_p.append(f"c.nome IN ({','.join(['?']*len(st.session_state['f_comites']))})") or params_p.extend(st.session_state["f_comites"])
    if st.session_state.get("f_paises"):   where_p.append(f"p.nome IN ({','.join(['?']*len(st.session_state['f_paises']))})") or params_p.extend(st.session_state["f_paises"])
    where_sql_p = ("WHERE " + " AND ".join(where_p)) if where_p else ""
    df_paises = pd.DataFrame(run_query(f"""
        SELECT p.id, p.nome, c.nome, c.periodo
        FROM paises p LEFT JOIN comites c ON p.comite_id=c.id
        {where_sql_p}
        ORDER BY c.periodo, c.nome, p.nome
    """, tuple(params_p), fetch=True), columns=["ID","País","Comitê","Período"])
    if df_paises.empty:
        st.info("Nenhum país no filtro atual.")
    else:
        head_p = st.columns([0.3,1.2,1.2,1.0,0.7])
        for i, txt in enumerate(["ID","País","Comitê","Período","Ações"]): head_p[i].markdown(f"**{txt}**")
        for _, r in df_paises.iterrows():
            c1,c2,c3,c4,c5 = st.columns([0.3,1.2,1.2,1.0,0.7])
            c1.write(int(r["ID"])); c2.write(r["País"]); c3.write(r["Comitê"]); c4.write(r["Período"])
            if c5.button("Apagar", key=f"delp_{int(r['ID'])}"):
                ok, msg = try_delete_pais(int(r["ID"]))
                if ok: st.success(msg); st.rerun()
                else:  st.error(msg)

    st.divider()
    with st.expander("Exportar inscrições (Excel)"):
        st.download_button(
            "Baixar Excel",
            data=to_excel(df_ins),
            file_name=f"inscricoes_onutec_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_export",
        )

# ========= Roteador =========
def main():
    # lê o parâmetro de página com fallback p/ versões antigas
    if hasattr(st, "query_params"):
        page = st.query_params.get("page", "inscricao")
    else:
        page = st.experimental_get_query_params().get("page", ["inscricao"])[0]
    page = page.lower() if isinstance(page, str) else "inscricao"

    # roteamento (sem botões/links visíveis)
    if page in ("admin", "dashboard", "painel"):
        pagina_admin()
    else:
        pagina_inscricao()

if __name__ == "__main__":
    main()

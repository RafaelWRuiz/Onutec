import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import os
DB_FILE = os.getenv("DB_FILE", "onutec.db")
# =========================
# Config (página pública)
# =========================
st.set_page_config(page_title="Onutec | Inscrição", layout="wide")
st.title("Inscrição em Dupla – Onutec")

DB_FILE = "onutec.db"  # em produção (Render) use: DB_FILE = "/data/onutec.db"

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
    cols = {row[1] for row in run_query("PRAGMA table_info(inscricoes)", fetch=True)}
    if "created_at" not in cols:
        run_query("ALTER TABLE inscricoes ADD COLUMN created_at TEXT")

init_db()
ensure_schema()

# =========================
# Utilitários
# =========================
def comites_com_paises_livres(periodo: str):
    comites = run_query("SELECT id, nome FROM comites WHERE periodo=? ORDER BY nome", (periodo,), fetch=True)
    out = []
    for cid, nome in comites:
        livres = run_query("SELECT COUNT(*) FROM paises WHERE comite_id=? AND ocupado=0", (cid,), fetch=True)[0][0]
        if livres > 0:
            out.append((cid, nome))
    return out

def paises_livres_do_comite(comite_id: int):
    return run_query("SELECT id, nome FROM paises WHERE comite_id=? AND ocupado=0 ORDER BY nome", (comite_id,), fetch=True)

# =========================
# Formulário
# =========================
st.info("Preencha os dados abaixo. A inscrição é em dupla e cada dupla escolhe **1 país disponível** no comitê selecionado.")

# 1) Período
periodo = st.selectbox("Período", ["Manhã", "Tarde", "Noite"], key="sel_periodo")

# 2) Comitê (apenas com países livres naquele período)
comites_disp = comites_com_paises_livres(periodo)
if not comites_disp:
    st.warning("No momento, não há comitês com países disponíveis nesse período.")
    st.stop()

mapa_comites = {f"{nome} (ID {cid})": cid for cid, nome in comites_disp}
sel_comite_label = st.selectbox("Comitê", list(mapa_comites.keys()), key="sel_comite")
comite_id = mapa_comites[sel_comite_label]

# 3) País (apenas livres do comitê escolhido)
paises_disp = paises_livres_do_comite(comite_id)
if not paises_disp:
    st.warning("Este comitê ficou sem países livres. Escolha outro comitê.")
    st.stop()

mapa_paises = {f"{nome} (ID {pid})": pid for pid, nome in paises_disp}
sel_pais_label = st.selectbox("País", list(mapa_paises.keys()), key="sel_pais")
pais_id = mapa_paises[sel_pais_label]

st.divider()

# 4) Dados da dupla (keys únicas)
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

enviar = st.button("Enviar Inscrição", key="btn_enviar")

# =========================
# Envio
# =========================
if enviar:
    faltando = []
    if not a1_nome.strip():  faltando.append("Aluno 1 - Nome")
    if not a1_serie.strip(): faltando.append("Aluno 1 - Série")
    if not a1_curso.strip(): faltando.append("Aluno 1 - Curso")
    if not a2_nome.strip():  faltando.append("Aluno 2 - Nome")
    if not a2_serie.strip(): faltando.append("Aluno 2 - Série")
    if not a2_curso.strip(): faltando.append("Aluno 2 - Curso")

    if faltando:
        st.error("Preencha os campos obrigatórios: " + ", ".join(faltando))
        st.stop()

    # checa se o país ainda está livre
    ainda_livre = run_query("SELECT ocupado FROM paises WHERE id=?", (pais_id,), fetch=True)
    if not ainda_livre or ainda_livre[0][0] == 1:
        st.error("Opa! Esse país acabou de ser escolhido por outra dupla. Selecione outro país, por favor.")
        st.rerun()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_query("""INSERT INTO inscricoes 
                 (aluno1_nome, aluno1_whatsapp, aluno1_serie, aluno1_curso,
                  aluno2_nome, aluno2_whatsapp, aluno2_serie, aluno2_curso,
                  periodo, comite_id, pais_id, created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (a1_nome.strip(), a1_whats.strip(), a1_serie.strip(), a1_curso.strip(),
               a2_nome.strip(), a2_whats.strip(), a2_serie.strip(), a2_curso.strip(),
               periodo, comite_id, pais_id, now))

    run_query("UPDATE paises SET ocupado=1 WHERE id=?", (pais_id,))

    comite_nome = run_query("SELECT nome FROM comites WHERE id=?", (comite_id,), fetch=True)[0][0]
    pais_nome   = run_query("SELECT nome FROM paises WHERE id=?", (pais_id,), fetch=True)[0][0]
    insc_id     = run_query("SELECT MAX(id) FROM inscricoes", fetch=True)[0][0]

    st.success("Inscrição enviada com sucesso!")
    st.write(f"**Protocolo:** #{insc_id}")
    st.write(f"**Período:** {periodo}")
    st.write(f"**Comitê:** {comite_nome}")
    st.write(f"**País:** {pais_nome}")
    st.write(f"**Dupla:** {a1_nome} e {a2_nome}")
    st.toast("Inscrição registrada. País reservado para a sua dupla.")

    for key in ["a1_nome","a1_whats","a1_serie","a1_curso",
                "a2_nome","a2_whats","a2_serie","a2_curso"]:
        if key in st.session_state: del st.session_state[key]

    st.rerun()

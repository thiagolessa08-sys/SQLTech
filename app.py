from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from functools import wraps
import sqlite3, os, requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sqltech-orcamento-2026-xK9m")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "municipal.db")

# ── Credenciais ──────────────────────────────────────────────────────────
USERS = {
    "marcio.amorim@sqltech.com.br": {
        "password": "Sqltech123",
        "name": "Márcio Amorim"
    }
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = USERS.get(email)
        if user and user["password"] == password:
            session["logged_in"]  = True
            session["user_email"] = email
            session["user_name"]  = user["name"]
            return redirect(url_for("index"))
        error = "E-mail ou senha incorretos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

def query(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows

@app.route("/api/anos")
def anos():
    r = query('SELECT DISTINCT "Número Ano" AS ano FROM receita ORDER BY ano')
    return jsonify([row["ano"] for row in r])

@app.route("/api/kpis")
def kpis():
    ano = request.args.get("ano", 2024, type=int)
    rec = query('SELECT SUM("Valor Arrecadação Receita") AS arrecadado, SUM("Valor Projeto Receita") AS projetado FROM receita WHERE "Número Ano"=?', (ano,))
    desp = query('SELECT SUM("Valor Mês Empenhado") AS empenhado, SUM("Valor Mês Liquidado") AS liquidado, SUM("Valor Mês Pago") AS pago FROM despesa WHERE "Número Ano"=?', (ano,))
    return jsonify({**rec[0], **desp[0], "ano": ano})

@app.route("/api/receita-mensal")
def receita_mensal():
    ano = request.args.get("ano", 2024, type=int)
    rows = query('SELECT "Número Mês" AS mes, "Descrição Mês" AS nome, SUM("Valor Arrecadação Receita") AS total FROM receita WHERE "Número Ano"=? GROUP BY mes, nome ORDER BY mes', (ano,))
    return jsonify(rows)

@app.route("/api/despesa-funcao")
def despesa_funcao_dash():
    ano = request.args.get("ano", 2024, type=int)
    rows = query('SELECT "Descrição Função" AS funcao, SUM("Valor Mês Empenhado") AS empenhado, SUM("Valor Mês Pago") AS pago FROM despesa WHERE "Número Ano"=? GROUP BY funcao ORDER BY empenhado DESC NULLS LAST', (ano,))
    return jsonify(rows)

@app.route("/api/receita-categoria")
def receita_categoria_dash():
    ano = request.args.get("ano", 2024, type=int)
    rows = query('SELECT "Descrição Categoria Econômica Receita" AS categoria, SUM("Valor Arrecadação Receita") AS total FROM receita WHERE "Número Ano"=? GROUP BY categoria ORDER BY total DESC', (ano,))
    return jsonify(rows)

# TELA RECEITA
@app.route("/api/receita/kpis")
def receita_kpis():
    ano = request.args.get("ano", 2024, type=int)
    mes = request.args.get("mes", 0, type=int)
    w  = f'"Número Ano"={ano}' + (f' AND "Número Mês"={mes}' if mes else '')
    wp = f'"Número Ano"={ano-1}' + (f' AND "Número Mês"={mes}' if mes else '')
    wa = f'"Número Ano"={ano}' + (f' AND "Número Mês"<={mes}' if mes else '')
    wpa= f'"Número Ano"={ano-1}' + (f' AND "Número Mês"<={mes}' if mes else '')
    cur  = query(f'SELECT SUM("Valor Projeto Receita") as orcado, SUM("Valor Projeto Receita")+COALESCE(SUM("Valor Alteração Orçamentária Receita"),0) as orcado_atualizado, SUM("Valor Arrecadação Receita") as arrecadado FROM receita WHERE {w}')
    prev = query(f'SELECT SUM("Valor Arrecadação Receita") as arrecadado_prev FROM receita WHERE {wp}')
    acum = query(f'SELECT SUM("Valor Arrecadação Receita") as acumulado FROM receita WHERE {wa}')
    pacum= query(f'SELECT SUM("Valor Arrecadação Receita") as acumulado_prev FROM receita WHERE {wpa}')
    return jsonify({**cur[0], **prev[0], "acumulado": acum[0]["acumulado"], "acumulado_prev": pacum[0]["acumulado_prev"]})

@app.route("/api/receita/historico")
def receita_historico():
    rows = query('SELECT "Número Ano" AS ano, "Número Mês" AS mes, SUM("Valor Arrecadação Receita") AS total FROM receita GROUP BY ano, mes ORDER BY ano, mes')
    return jsonify(rows)

@app.route("/api/receita/origem")
def receita_origem():
    ano = request.args.get("ano", 2024, type=int)
    mes = request.args.get("mes", 0, type=int)
    w = f'"Número Ano"={ano}' + (f' AND "Número Mês"={mes}' if mes else '')
    rows = query(f'SELECT "Descrição Origem Receita" AS origem, SUM("Valor Arrecadação Receita") AS arrecadado FROM receita WHERE {w} GROUP BY origem ORDER BY arrecadado DESC')
    return jsonify(rows)

# TELA DESPESA
@app.route("/api/despesa/kpis")
def despesa_kpis():
    ano = request.args.get("ano", 2024, type=int)
    mes = request.args.get("mes", 0, type=int)
    w  = f'"Número Ano"={ano}' + (f' AND "Número Mês"={mes}' if mes else '')
    wp = f'"Número Ano"={ano-1}' + (f' AND "Número Mês"={mes}' if mes else '')
    cur  = query(f'SELECT SUM("Valor Dotação Inicial") as dot_inicial, SUM("Valor Dotação Inicial")+COALESCE(SUM("Valor Alteração Orçamentaria Despesa"),0) as dot_atualizada, SUM("Valor Mês Empenhado") as empenhado, SUM("Valor Mês Liquidado") as liquidado, SUM("Valor Mês Pago") as pago FROM despesa WHERE {w}')
    prev = query(f'SELECT SUM("Valor Dotação Inicial") as dot_inicial_prev, SUM("Valor Mês Empenhado") as empenhado_prev, SUM("Valor Mês Liquidado") as liquidado_prev, SUM("Valor Mês Pago") as pago_prev FROM despesa WHERE {wp}')
    return jsonify({**cur[0], **prev[0]})

@app.route("/api/despesa/secretaria")
def despesa_secretaria():
    ano = request.args.get("ano", 2024, type=int)
    rows = query(f'SELECT "Descrição Unidade Orçamentária" AS secretaria, SUM("Valor Dotação Inicial") AS dot_inicial, SUM("Valor Dotação Inicial")+COALESCE(SUM("Valor Alteração Orçamentaria Despesa"),0) AS dot_atualizada, SUM("Valor Mês Empenhado") AS empenhado, SUM("Valor Mês Liquidado") AS liquidado, SUM("Valor Mês Pago") AS pago FROM despesa WHERE "Número Ano"={ano} GROUP BY secretaria ORDER BY dot_atualizada DESC NULLS LAST')
    return jsonify(rows)

@app.route("/api/despesa/categoria")
def despesa_categoria():
    ano = request.args.get("ano", 2024, type=int)
    rows = query(f'SELECT "Descrição Categoria" AS categoria, SUM("Valor Dotação Inicial") AS dotacao FROM despesa WHERE "Número Ano"={ano} GROUP BY categoria ORDER BY dotacao DESC')
    return jsonify(rows)

@app.route("/api/despesa/modalidade")
def despesa_modalidade():
    ano = request.args.get("ano", 2024, type=int)
    rows = query(f'SELECT "Descrição Modalidade" AS modalidade, SUM("Valor Dotação Inicial") AS dotacao FROM despesa WHERE "Número Ano"={ano} GROUP BY modalidade ORDER BY dotacao DESC')
    return jsonify(rows)

@app.route("/api/despesa/elemento")
def despesa_elemento():
    ano = request.args.get("ano", 2024, type=int)
    rows = query(f'SELECT "Descrição Elemento Despesa" AS elemento, SUM("Valor Dotação Inicial") AS dotacao, SUM("Valor Mês Empenhado") AS empenhado FROM despesa WHERE "Número Ano"={ano} GROUP BY elemento ORDER BY dotacao DESC NULLS LAST LIMIT 10')
    return jsonify(rows)

@app.route("/api/chat", methods=["POST"])
def chat():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY não configurada"}), 500
    payload = request.json
    resp = requests.post("https://api.anthropic.com/v1/messages",
        headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},
        json=payload, timeout=60)
    return jsonify(resp.json()), resp.status_code

@app.route("/api/chat/context")
def chat_context():
    anos_rec  = query('SELECT DISTINCT "Número Ano" AS ano FROM receita ORDER BY ano')
    anos_desp = query('SELECT DISTINCT "Número Ano" AS ano FROM despesa ORDER BY ano')
    rec_total = query('SELECT SUM("Valor Arrecadação Receita") AS total, SUM("Valor Projeto Receita") AS orcado FROM receita')
    desp_total= query('SELECT SUM("Valor Mês Empenhado") AS empenhado, SUM("Valor Mês Liquidado") AS liquidado, SUM("Valor Mês Pago") AS pago FROM despesa')
    rec_count = query('SELECT COUNT(*) AS cnt FROM receita')
    desp_count= query('SELECT COUNT(*) AS cnt FROM despesa')
    rec_2024  = query('SELECT SUM("Valor Arrecadação Receita") AS total FROM receita WHERE "Número Ano"=2024')
    desp_2024 = query('SELECT SUM("Valor Mês Empenhado") AS empenhado FROM despesa WHERE "Número Ano"=2024')
    # Dados mensais por ano
    mensal = {}
    for ano in [r["ano"] for r in anos_rec]:
        rows = query(f'SELECT "Número Mês" AS mes, "Descrição Mês" AS nome, SUM("Valor Arrecadação Receita") AS total FROM receita WHERE "Número Ano"={ano} GROUP BY mes, nome ORDER BY mes')
        mensal[str(ano)] = [{"mes": r["mes"], "nome": r["nome"], "total": r["total"]} for r in rows]
    # Despesa mensal (empenhado) por ano
    desp_mensal = {}
    for ano in [r["ano"] for r in anos_desp]:
        rows = query(f'SELECT "Número Mês" AS mes, "Descrição Mês" AS nome, SUM("Valor Mês Empenhado") AS empenhado FROM despesa WHERE "Número Ano"={ano} GROUP BY mes, nome ORDER BY mes')
        desp_mensal[str(ano)] = [{"mes": r["mes"], "nome": r["nome"], "empenhado": r["empenhado"]} for r in rows]
    # Receita por categoria 2024
    cat_2024 = query('SELECT "Descrição Categoria Econômica Receita" AS categoria, SUM("Valor Arrecadação Receita") AS total FROM receita WHERE "Número Ano"=2024 GROUP BY categoria ORDER BY total DESC')
    # Despesa por secretaria 2024
    sec_2024 = query('SELECT "Descrição Unidade Orçamentária" AS secretaria, SUM("Valor Mês Empenhado") AS empenhado FROM despesa WHERE "Número Ano"=2024 GROUP BY secretaria ORDER BY empenhado DESC NULLS LAST LIMIT 10')
    return jsonify({
        "anos_receita": [r["ano"] for r in anos_rec],
        "anos_despesa": [r["ano"] for r in anos_desp],
        "receita_total_arrecadado": rec_total[0]["total"],
        "receita_total_orcado": rec_total[0]["orcado"],
        "despesa_total_empenhado": desp_total[0]["empenhado"],
        "despesa_total_liquidado": desp_total[0]["liquidado"],
        "despesa_total_pago": desp_total[0]["pago"],
        "receita_registros": rec_count[0]["cnt"],
        "despesa_registros": desp_count[0]["cnt"],
        "receita_2024": rec_2024[0]["total"],
        "despesa_2024": desp_2024[0]["empenhado"],
        "receita_mensal": mensal,
        "despesa_mensal": desp_mensal,
        "receita_categoria_2024": [{"categoria": r["categoria"], "total": r["total"]} for r in cat_2024],
        "despesa_secretaria_2024": [{"secretaria": r["secretaria"], "empenhado": r["empenhado"]} for r in sec_2024]
    })

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/receita")
@login_required
def receita_page():
    return render_template("receita.html")

@app.route("/despesa")
@login_required
def despesa_page():
    return render_template("despesa.html")

@app.route("/chat")
@login_required
def chat_page():
    return render_template("chat.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

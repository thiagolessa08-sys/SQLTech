from flask import Flask, jsonify, render_template, request
import sqlite3, os, requests

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "municipal.db")

def query(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


# ── KPIs ──────────────────────────────────────────────────────────────────────
@app.route("/api/kpis")
def kpis():
    ano = request.args.get("ano", 2024, type=int)
    rec = query("""
        SELECT
          SUM("Valor Arrecadação Receita") AS arrecadado,
          SUM("Valor Projeto Receita")     AS projetado
        FROM receita WHERE "Número Ano" = ?
    """, (ano,))
    desp = query("""
        SELECT
          SUM("Valor Mês Empenhado")  AS empenhado,
          SUM("Valor Mês Liquidado")  AS liquidado,
          SUM("Valor Mês Pago")       AS pago
        FROM despesa WHERE "Número Ano" = ?
    """, (ano,))
    return jsonify({**rec[0], **desp[0], "ano": ano})


# ── RECEITA MENSAL ─────────────────────────────────────────────────────────────
@app.route("/api/receita-mensal")
def receita_mensal():
    ano = request.args.get("ano", 2024, type=int)
    rows = query("""
        SELECT "Número Mês" AS mes, "Descrição Mês" AS nome,
               SUM("Valor Arrecadação Receita") AS total
        FROM receita WHERE "Número Ano" = ?
        GROUP BY mes, nome ORDER BY mes
    """, (ano,))
    return jsonify(rows)


# ── DESPESA POR FUNÇÃO ─────────────────────────────────────────────────────────
@app.route("/api/despesa-funcao")
def despesa_funcao():
    ano = request.args.get("ano", 2024, type=int)
    rows = query("""
        SELECT "Descrição Função" AS funcao,
               SUM("Valor Mês Empenhado") AS empenhado,
               SUM("Valor Mês Pago")      AS pago
        FROM despesa WHERE "Número Ano" = ?
        GROUP BY funcao ORDER BY empenhado DESC NULLS LAST
    """, (ano,))
    return jsonify(rows)


# ── RECEITA POR CATEGORIA ──────────────────────────────────────────────────────
@app.route("/api/receita-categoria")
def receita_categoria():
    ano = request.args.get("ano", 2024, type=int)
    rows = query("""
        SELECT "Descrição Categoria Econômica Receita" AS categoria,
               SUM("Valor Arrecadação Receita") AS total
        FROM receita WHERE "Número Ano" = ?
        GROUP BY categoria ORDER BY total DESC
    """, (ano,))
    return jsonify(rows)


# ── ANOS DISPONÍVEIS ───────────────────────────────────────────────────────────
@app.route("/api/anos")
def anos():
    r = query('SELECT DISTINCT "Número Ano" AS ano FROM receita ORDER BY ano')
    return jsonify([row["ano"] for row in r])


# ── PROXY ANTHROPIC (evita expor key no front) ────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY não configurada"}), 500

    payload = request.json
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json=payload,
        timeout=60,
    )
    return jsonify(resp.json()), resp.status_code


# ── FRONTEND ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

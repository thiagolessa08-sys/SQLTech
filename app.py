from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from functools import wraps
import sqlite3, os, requests, json, re, time
from datetime import datetime
import pandas as pd

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sqltech-orcamento-2026-xK9m")
DB_PATH      = os.path.join(os.path.dirname(__file__), "data", "municipal.db")
DB_RULES_PATH = os.path.join(os.path.dirname(__file__), "data", "db_rules.txt")

# ── Regras do Banco (db_rules.txt) ───────────────────────────────────────
def load_db_rules() -> str:
    """Carrega o arquivo de regras do banco. Retorna string vazia se não encontrado."""
    try:
        with open(DB_RULES_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"[db_rules] Aviso: não foi possível carregar db_rules.txt — {e}")
        return ""

def append_db_rule(rule_text: str) -> bool:
    """Acrescenta uma nova regra ao arquivo db_rules.txt. Retorna True se ok."""
    try:
        with open(DB_RULES_PATH, encoding="utf-8", mode="a") as f:
            f.write(f"\n\n# Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Via API\n")
            f.write(rule_text.strip() + "\n")
        return True
    except Exception as e:
        print(f"[db_rules] Erro ao escrever regra: {e}")
        return False

# ── Sybase IQ Agent ──────────────────────────────────────────────────────
AGENT_URL     = os.environ.get("AGENT_URL", "").rstrip("/")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
SYBASE_SCHEMA = "pref_aruja_sp"

def _agent_headers():
    return {"Content-Type": "application/json", "X-API-Key": AGENT_API_KEY}

def sybase_available():
    return bool(AGENT_URL and AGENT_API_KEY)

def sybase_health():
    """Retorna dict com status do agente ou erro."""
    if not sybase_available():
        return {"ok": False, "erro": "AGENT_URL ou AGENT_API_KEY não configurados"}
    try:
        r = requests.get(f"{AGENT_URL}/health", headers=_agent_headers(), timeout=8)
        return r.json() if r.status_code == 200 else {"ok": False, "erro": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

def sybase_tables():
    """Lista tabelas/views do banco via agent. Retorna lista de strings."""
    if not sybase_available():
        return []
    try:
        r = requests.get(f"{AGENT_URL}/tables", headers=_agent_headers(), timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        # A API pode retornar {"tables": [...]} ou diretamente [...]
        if isinstance(data, dict):
            raw = data.get("tables", data.get("data", []))
        else:
            raw = data
        # Normaliza cada item para string limpa
        result = []
        for item in raw:
            if isinstance(item, dict):
                name = (item.get("table_name") or item.get("name") or
                        item.get("TABLE_NAME") or str(item)).strip()
            else:
                name = str(item).strip()
            if name:
                result.append(name)
        return result
    except Exception:
        return []

def sybase_schema(table):
    """Retorna colunas de uma tabela via agent."""
    if not sybase_available():
        return []
    try:
        r = requests.get(f"{AGENT_URL}/schema/{table}", headers=_agent_headers(), timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def sybase_query(sql, limit=500):
    """Executa SELECT no Sybase via agent. Retorna lista de dicts."""
    if not sybase_available():
        raise RuntimeError("Agente Sybase não configurado (AGENT_URL/AGENT_API_KEY ausentes)")
    try:
        r = requests.post(
            f"{AGENT_URL}/query",
            headers=_agent_headers(),
            json={"sql": sql, "limit": limit},
            timeout=60
        )
        if r.status_code != 200:
            raise RuntimeError(f"Agent retornou HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        # Converte array de arrays → lista de dicts
        cols = data.get("columns", [])
        rows = data.get("rows", [])
        return [dict(zip(cols, row)) for row in rows]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Erro ao consultar Sybase: {e}")

def is_sybase_query(sql):
    """Detecta se a query é para o Sybase (referencia o schema ou tabela qualificada)."""
    s = sql.upper()
    return SYBASE_SCHEMA.upper() in s or re.search(r'\bSYS\b', s) is not None

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
    # Apaga bases temporárias (não-seed) do banco
    try:
        con = sqlite3.connect(DB_PATH)
        temp_bases = con.execute("SELECT name FROM _bases_catalog WHERE is_seed=0").fetchall()
        for (name,) in temp_bases:
            con.execute(f"DROP TABLE IF EXISTS base_{name}")
        con.execute("DELETE FROM _bases_catalog WHERE is_seed=0")
        con.commit()
        con.close()
    except Exception as e:
        print(f"[logout] Erro ao limpar bases temporárias: {e}")
    session.clear()
    return redirect(url_for("login_page"))

def query(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows

# ── Bases de Dados ────────────────────────────────────────────────────────
SEEDS_DIR = os.path.join(os.path.dirname(__file__), "data", "seeds")

def init_catalog():
    con = sqlite3.connect(DB_PATH)
    con.execute('''CREATE TABLE IF NOT EXISTS _bases_catalog (
        name TEXT PRIMARY KEY,
        label TEXT,
        filename TEXT,
        rows INTEGER,
        cols TEXT,
        uploaded_at TEXT,
        is_seed INTEGER DEFAULT 0
    )''')
    # Adiciona coluna is_seed caso o banco já exista sem ela
    try:
        con.execute("ALTER TABLE _bases_catalog ADD COLUMN is_seed INTEGER DEFAULT 0")
    except Exception:
        pass
    con.commit()
    con.close()

def init_seeds():
    """Importa automaticamente todos os arquivos de data/seeds/ como bases permanentes."""
    if not os.path.isdir(SEEDS_DIR):
        return
    con = sqlite3.connect(DB_PATH)
    for filename in os.listdir(SEEDS_DIR):
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("csv", "xlsx", "xls"):
            continue
        name = re.sub(r"[^a-z0-9]", "_", filename.rsplit(".", 1)[0].lower())[:40]
        # Só importa se ainda não existir como seed
        existing = con.execute("SELECT name FROM _bases_catalog WHERE name=? AND is_seed=1", (name,)).fetchone()
        if existing:
            continue
        filepath = os.path.join(SEEDS_DIR, filename)
        try:
            df = pd.read_csv(filepath, encoding="utf-8", sep=None, engine="python") if ext == "csv" else pd.read_excel(filepath)
            df.to_sql(f"base_{name}", con, if_exists="replace", index=False)
            label = filename.rsplit(".", 1)[0].replace("_", " ").title()
            cols_info = json.dumps([{"col": c, "type": str(df[c].dtype)} for c in df.columns])
            con.execute("INSERT OR REPLACE INTO _bases_catalog VALUES (?,?,?,?,?,?,1)",
                (name, label, filename, len(df), cols_info, datetime.now().strftime("%Y-%m-%d %H:%M")))
            print(f"[seeds] Importado: {filename} ({len(df)} linhas)")
        except Exception as e:
            print(f"[seeds] Erro ao importar {filename}: {e}")
    con.commit()
    con.close()

def cleanup_orphan_seeds():
    """Remove seeds do banco cujo arquivo CSV/Excel não existe mais em data/seeds/."""
    if not os.path.isdir(SEEDS_DIR):
        return
    # Nomes válidos = arquivos que ainda existem na pasta
    valid_names = set()
    for filename in os.listdir(SEEDS_DIR):
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in ("csv", "xlsx", "xls"):
            name = re.sub(r"[^a-z0-9]", "_", filename.rsplit(".", 1)[0].lower())[:40]
            valid_names.add(name)
    try:
        con = sqlite3.connect(DB_PATH)
        orphans = con.execute("SELECT name FROM _bases_catalog WHERE is_seed=1").fetchall()
        for (name,) in orphans:
            if name not in valid_names:
                con.execute(f"DROP TABLE IF EXISTS base_{name}")
                con.execute("DELETE FROM _bases_catalog WHERE name=?", (name,))
                print(f"[seeds] Removido seed órfão: {name}")
        con.commit()
        con.close()
    except Exception as e:
        print(f"[seeds] Erro ao limpar seeds órfãos: {e}")

init_catalog()
init_seeds()
cleanup_orphan_seeds()

@app.route("/api/bases")
@login_required
def bases_list():
    rows = query("SELECT name, label, filename, rows, cols, uploaded_at FROM _bases_catalog ORDER BY uploaded_at DESC")
    return jsonify(rows)

@app.route("/api/bases/upload", methods=["POST"])
@login_required
def bases_upload():
    f = request.files.get("file")
    label = request.form.get("label", "").strip()
    if not f or not f.filename:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    filename = f.filename
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("csv", "xlsx", "xls"):
        return jsonify({"error": "Formato não suportado. Use CSV ou Excel (.csv, .xlsx, .xls)"}), 400
    name = re.sub(r"[^a-z0-9]", "_", filename.rsplit(".", 1)[0].lower())[:40]
    existing = query("SELECT name FROM _bases_catalog WHERE name=?", (name,))
    if existing:
        name = name[:36] + "_" + str(int(time.time()))[-3:]
    try:
        if ext == "csv":
            df = pd.read_csv(f, encoding="utf-8", sep=None, engine="python")
        else:
            df = pd.read_excel(f)
    except Exception as e:
        return jsonify({"error": f"Erro ao ler arquivo: {str(e)}"}), 400
    con = sqlite3.connect(DB_PATH)
    df.to_sql(f"base_{name}", con, if_exists="replace", index=False)
    cols_info = json.dumps([{"col": c, "type": str(df[c].dtype)} for c in df.columns])
    con.execute("INSERT OR REPLACE INTO _bases_catalog VALUES (?,?,?,?,?,?)",
        (name, label or filename, filename, len(df), cols_info, datetime.now().strftime("%Y-%m-%d %H:%M")))
    con.commit()
    con.close()
    return jsonify({"ok": True, "name": name, "rows": len(df), "cols": len(df.columns)})

@app.route("/api/bases/<name>/preview")
@login_required
def bases_preview(name):
    name = re.sub(r"[^a-z0-9_]", "_", name)
    try:
        rows = query(f"SELECT * FROM base_{name} LIMIT 10")
        return jsonify(rows)
    except Exception:
        return jsonify({"error": "Base não encontrada"}), 404

@app.route("/api/bases/<name>", methods=["DELETE"])
@login_required
def bases_delete(name):
    name = re.sub(r"[^a-z0-9_]", "_", name)
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(f"DROP TABLE IF EXISTS base_{name}")
        con.execute("DELETE FROM _bases_catalog WHERE name=?", (name,))
        con.commit()
        con.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

def safe_sql(sql):
    """Valida e executa SQL apenas SELECT com segurança."""
    s = sql.strip()
    s_up = s.upper().lstrip()
    if not s_up.startswith("SELECT"):
        raise ValueError("Apenas queries SELECT são permitidas.")
    blocked = ["DROP","DELETE","UPDATE","INSERT","CREATE","ALTER","ATTACH","DETACH","PRAGMA"]
    for kw in blocked:
        if re.search(r'\b' + kw + r'\b', s_up):
            raise ValueError(f"Operação não permitida: {kw}")
    # Garante LIMIT
    if "LIMIT" not in s_up:
        s = s.rstrip(";") + " LIMIT 100"
    return query(s)

_ANNOUNCEMENT_PHRASES = [
    'agora vou', 'vou criar', 'vou buscar', 'vou montar', 'vou mostrar',
    'vou tentar', 'deixe-me', 'agora consulto', 'perfeito! agora',
    'vou elaborar', 'vou preparar', 'vou apresentar', 'vou verificar',
    'primeiro vou', 'para isso vou', 'com base nisso', 'agora podemos',
    'deixe-me tentar', 'vou localizar', 'preciso localizar', 'vou explorar',
    'vou investigar', 'vou pesquisar', 'vou consultar', 'let me', 'trying',
    'vou checar', 'vou analisar antes', 'primeiro preciso',
    'vou ajustar', 'vou refinar', 'vou refazer', 'vou tentar outra',
    'vou rodar', 'vou executar', 'preciso verificar', 'precisa verificar',
    'os dados mostram', 'os dados retornam', 'os resultados mostram',
    'sk_', 'vl_', 'ds_', 'no_ano',  # se aparece SK_/VL_/DS_ no texto, é debugando
]

_REFUSAL_PHRASES = [
    'não há tabelas', 'não tenho tabelas', 'não há dados de despesa',
    'não há dados de receita', 'contém apenas dados de iptu',
    'apenas dados de iptu', 'só tenho dados de iptu', 'somente iptu',
    'não possui tabelas de', 'não estão disponíveis', 'não estão disponíveis neste momento',
    'seria necessário acesso a', 'não há informações sobre despesa',
    'não há informações sobre receita', 'banco não contém',
    'banco de dados não possui', 'não encontrei tabelas de',
    'não existem tabelas', 'sem acesso a tabelas',
]

def _is_announcement(content):
    """Detecta se a resposta é um anúncio sem dados reais (modelo travado)."""
    text = ' '.join(blk.get('text', '') for blk in content if blk.get('type') == 'text').strip()
    if not text:
        return False
    if text.endswith(':'):
        return True
    text_low = text.lower()
    has_announcement = any(p in text_low for p in _ANNOUNCEMENT_PHRASES)
    has_data = '[chart]' in text_low or 'r$' in text_low or len(text) > 400
    return has_announcement and not has_data

def _is_refusal_without_query(content, queries_run):
    """Detecta se o modelo recusou sem executar nenhuma query."""
    if queries_run:
        return False  # já rodou queries — recusa legítima
    text = ' '.join(blk.get('text', '') for blk in content if blk.get('type') == 'text').strip()
    if not text:
        return False
    text_low = text.lower()
    return any(p in text_low for p in _REFUSAL_PHRASES)

def _missing_chart_with_data(content, queries_run):
    """Detecta resposta com dados (queries rodaram, há números) mas sem [CHART]."""
    if not queries_run:
        return False
    text = ' '.join(blk.get('text', '') for blk in content if blk.get('type') == 'text')
    if '[CHART]' in text or '[chart]' in text.lower():
        return False
    has_money    = bool(re.search(r'R\$\s*[\d.,]+', text))
    has_list     = bool(re.search(r'^\s*\d+[\.\)]\s+', text, re.MULTILINE))
    has_bullets  = text.count('•') >= 2 or text.count(':') >= 3
    has_percent  = bool(re.search(r'\d+[,\.]?\d*\s*%', text))
    money_count  = len(re.findall(r'R\$\s*[\d.,]+', text))
    return (money_count >= 2) or (has_list and has_money) or (has_bullets and has_percent)


# ── Auto-chart: detecta padrão tabular nos rows e gera [CHART] ───────────
def _to_float(v):
    """Converte para float aceitando Decimal, int, str numérica. Retorna None se falhar."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        if isinstance(v, str):
            try:
                return float(v.replace('.', '').replace(',', '.'))
            except Exception:
                return None
        return None


# Colunas técnicas que NÃO devem virar series no gráfico
_SKIP_COL_PREFIXES = ('SK_', 'ID_', 'FK_', 'PK_', 'IC_', 'FL_', 'IND_')
_SKIP_COL_EXACT = {'ID', 'SK', 'CD_ORGAO', 'CD_TIPO_NATUREZA_RECEITA',
                   'NR_ANO_EMPENHO', 'CD_NATUREZA_DESPESA', 'CD_GRUPO',
                   'CD_ELEMENTO', 'CD_FONTE_RECURSO', 'CD_UNIDADE_GESTORA',
                   'CD_MODALIDADE', 'FICHA_DESPESA', 'FICHA_RECEITA',
                   'NUM_FICHA', 'CD_EMPENHO', 'CD_LIQUIDACAO', 'CD_PAGAMENTO',
                   'NR_EMPENHO', 'NR_LIQUIDACAO'}
_SKIP_COL_CONTAINS = ('_FLAG', '_ATIVO', '_FICHA', '_HASH', '_VERSAO',
                      '_VIGENTE', '_USUARIO', '_DATA_CARGA')

# Tradução de nomes técnicos → label amigável
_LABEL_MAP = {
    'VL_ARRECADACAO_RECEITA': 'Arrecadação',
    'VL_SALDO_MES_EMPENHADO': 'Empenhado',
    'VL_SALDO_MES_LIQUIDADO': 'Liquidado',
    'VL_SALDO_MES_PAGO': 'Pago',
    'VL_PROJ_LEI': 'Projeto de Lei',
    'VL_ORC_APROV_LEI': 'Orçamento Aprovado',
    'VL_EMENDA': 'Emendas',
    'VL_ALTERACAOORCAMENTARIA': 'Alteração Orçamentária',
    'VL_SUPRIMENTO_FINANCEIRO': 'Suprimento Financeiro',
    'VL_RAP_PROCESSADO_INSCRITO': 'RAP Processado Inscrito',
    'VL_RAP_PROCESSADO_PAGO': 'RAP Processado Pago',
    'VL_RAP_PROCESSADO_CANCELADO': 'RAP Processado Cancelado',
    'VL_RAP_NAO_PROCESSADO_INSCRITO': 'RAP Não Processado Inscrito',
    'VL_RAP_NAO_PROCESSADO_PAGO': 'RAP Não Processado Pago',
    'NO_ANO': 'Ano',
    'NO_MES': 'Mês',
    'DS_MES': 'Mês',
    'DS_ORGAO': 'Órgão',
    'DS_UO': 'Secretaria',
    'DS_FORNECEDOR': 'Fornecedor',
    'DS_NATUREZA_DESPESA': 'Natureza da Despesa',
    'DS_GRUPO': 'Grupo de Despesa',
    'DS_CATEGORIA': 'Categoria',
    'DS_ELEMENTO_DESPESA': 'Elemento',
    'DS_FONTE_RECURSO': 'Fonte',
    'DS_GRUPO_FONTE': 'Grupo de Fonte',
    'DS_FUNCAO': 'Função',
    'DS_SUBFUNCAO': 'Subfunção',
    'DS_PROGRAMA_EXECUCAO': 'Programa',
    'DS_ACAO_EXECUCAO': 'Ação',
    'DS_MODALIDADE': 'Modalidade',
    'DS_UNIDADE_GESTORA': 'Unidade Gestora',
    'DS_TIPO_FORNECEDOR': 'Tipo de Fornecedor',
    'CPF_CNPJ': 'CPF/CNPJ',
    'NOME': 'Contribuinte',
    'Bairro': 'Bairro',
    'Tributo': 'Tributo',
    'Valor_Lancado': 'Lançado',
    'Valor_Pago': 'Pago',
    'Valor_Juros': 'Juros',
    'Valor_Multa': 'Multa',
    'Valor_Correcao': 'Correção',
    'Ano_Mes': 'Mês',
}

def _should_skip_col(col_name):
    """True se a coluna é técnica (chave/ID) e não deve virar série."""
    if not col_name:
        return True
    cu = col_name.upper()
    if cu in _SKIP_COL_EXACT:
        return True
    if any(cu.startswith(p) for p in _SKIP_COL_PREFIXES):
        return True
    return any(c in cu for c in _SKIP_COL_CONTAINS)

def _prettify_col(col_name):
    """Converte nome técnico em label amigável."""
    if not col_name:
        return 'Valor'
    if col_name in _LABEL_MAP:
        return _LABEL_MAP[col_name]
    # Fallback: remove prefixos VL_/DS_/NO_, troca _ por espaço, title case
    s = col_name
    for prefix in ('VL_', 'DS_', 'NO_', 'CD_'):
        if s.upper().startswith(prefix):
            s = s[len(prefix):]
            break
    return s.replace('_', ' ').title()

def _auto_chart_from_rows(rows, query_sql=""):
    """Gera config de gráfico a partir de rows. Retorna dict ou None."""
    if not rows:
        return None
    if len(rows) > 200:
        rows = rows[:200]

    first = rows[0]
    if not isinstance(first, dict):
        return None

    cols = list(first.keys())
    if len(cols) < 2:
        return None

    # REJEITA exploração de dimensão de data/calendário (sem agregação)
    sql_up = (query_sql or "").upper()
    is_calendario = ('DIM_BIORC_DATA_CALENDARIO' in sql_up or
                     'DATA_CALENDARIO' in sql_up)
    has_aggregation = any(kw in sql_up for kw in ['SUM(', 'COUNT(', 'AVG(', 'MIN(', 'MAX(', 'GROUP BY'])
    if is_calendario and not has_aggregation:
        return None  # exploração de calendário não vira gráfico

    # Identifica colunas: texto vs numéricas — IGNORANDO chaves técnicas
    text_cols, num_cols = [], []
    for c in cols:
        if _should_skip_col(c):
            continue  # pula SK_*, ID_*, FK_*, PK_*
        sample = [r.get(c) for r in rows[:10] if r.get(c) is not None]
        if not sample:
            continue
        nums = [_to_float(v) for v in sample]
        if all(n is not None for n in nums):
            num_cols.append(c)
        else:
            text_cols.append(c)

    # Se sobrou pouco, tenta relaxar e incluir colunas técnicas como texto (mas não como valor)
    if not text_cols:
        for c in cols:
            if _should_skip_col(c):
                sample = [r.get(c) for r in rows[:5] if r.get(c) is not None]
                if sample:
                    text_cols.append(c)
                    break

    if not text_cols or not num_cols:
        return None

    label_col = text_cols[0]
    valid_rows = [r for r in rows
                  if r.get(label_col) is not None
                  and any(_to_float(r.get(nc)) is not None for nc in num_cols)]
    if len(valid_rows) < 1:
        return None

    valid_rows = valid_rows[:12]

    labels = [str(r.get(label_col, ''))[:40] for r in valid_rows]

    # QUALIDADE: labels devem ter variação significativa
    # Rejeita se todos iguais, ou se todos têm 1 caractere (provavelmente caracteres soltos)
    unique_labels = set(labels)
    if len(unique_labels) < 2:
        return None  # tudo igual — não dá gráfico
    avg_len = sum(len(l) for l in labels) / max(len(labels), 1)
    if avg_len < 2:
        return None  # labels muito curtos (caracteres soltos)

    # QUALIDADE: valida que pelo menos UMA coluna numérica tem valores grandes
    def has_meaningful_values(col):
        cu = (col or '').upper()
        # Rejeita explicitamente colunas que sabemos serem anos/meses (dimensões)
        if cu in {'NO_ANO', 'NO_MES', 'NO_DIA', 'NO_TRIMESTRE_ANO', 'NO_SEMESTRE_ANO',
                  'NO_ANO_MES', 'ANO', 'MES', 'YEAR', 'MONTH'}:
            return False
        vals = [_to_float(r.get(col)) for r in valid_rows]
        vals = [v for v in vals if v is not None and v != 0]
        if len(vals) < 2:
            return False
        max_v = max(abs(v) for v in vals)
        min_v = min(abs(v) for v in vals if v != 0)
        # Rejeita se TODOS os valores estão na faixa de anos (1900-2100)
        # ou de datas YYYYMMDD (19000101-21001231)
        if all(1900 <= abs(v) <= 2100 for v in vals):
            return False
        if all(19000101 <= abs(v) <= 21001231 for v in vals):
            return False
        if max_v > 1000:
            return True
        return min_v > 0 and (max_v / min_v) > 10

    num_cols = [nc for nc in num_cols if has_meaningful_values(nc)]
    if not num_cols:
        return None

    # QUALIDADE: rejeita se labels parecem códigos de data (YYYYMMDD / YYYYMM puros)
    def looks_like_date_code(s):
        s = str(s or '').strip()
        return s.isdigit() and len(s) in (6, 8) and s[:2] in ('19', '20')
    if all(looks_like_date_code(l) for l in labels):
        return None

    sql_low = (query_sql or "").lower()
    is_temporal = any(w in sql_low for w in ['no_mes', 'ds_mes', 'datepart', 'dateformat'])

    if len(num_cols) >= 2:
        datasets = []
        for nc in num_cols[:3]:
            vals = [(_to_float(r.get(nc)) or 0) for r in valid_rows]
            datasets.append({"label": _prettify_col(nc), "values": vals})
        if not datasets:
            return None
        return {
            "type": "line" if is_temporal else "multiBar",
            "title": "Análise comparativa",
            "labels": labels,
            "datasets": datasets
        }
    else:
        nc = num_cols[0]
        values = [(_to_float(r.get(nc)) or 0) for r in valid_rows]
        if is_temporal:
            chart_type = "line"
        elif len(values) >= 5:
            chart_type = "horizontalBar"
        else:
            chart_type = "bar"
        return {
            "type": chart_type,
            "title": "Análise",
            "labels": labels,
            "values": values,
            "label": _prettify_col(nc)
        }


def _process_response(data, queries_full):
    """Extrai [CHART] do texto, valida cada um, e retorna data com:
    - content[0].text limpo (sem [CHART])
    - data['charts'] = lista de configs válidos
    Se não houver chart válido mas houver rows, gera um automaticamente.
    """
    try:
        content = data.get("content", [])
        text_block = next((b for b in content if b.get("type") == "text"), None)
        if not text_block:
            data["charts"] = []
            data["_chart_debug"] = {"step": "no-text-block"}
            return data

        text = text_block.get("text", "")
        charts = []
        pat = re.compile(r'\[CHART\]([\s\S]*?)\[/CHART\]', re.IGNORECASE)

        # Extrai charts válidos do texto
        for m in pat.finditer(text):
            try:
                cfg = json.loads(m.group(1).strip())
                if isinstance(cfg, dict) and cfg.get("type") and \
                   (cfg.get("values") or cfg.get("datasets")):
                    charts.append(cfg)
            except Exception:
                pass

        # Remove TODOS os [CHART] (válidos e inválidos) do texto
        clean = pat.sub('', text)
        # Remove tags soltas
        clean = re.sub(r'\[CHART\][\s\S]*$', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\[/CHART\]', '', clean, flags=re.IGNORECASE)
        clean = clean.strip()

        # Se nenhum chart válido foi extraído mas há rows → gera automaticamente
        if not charts and queries_full:
            for q in reversed(queries_full):
                cfg = _auto_chart_from_rows(q.get("rows", []), q.get("sql", ""))
                if cfg:
                    charts.append(cfg)
                    break

        text_block["text"] = clean
        data["charts"] = charts
        data["_chart_debug"] = {
            "step": "ok",
            "charts_count": len(charts),
            "auto_generated": len(charts) == 1 and not pat.search(text)
        }
        return data
    except Exception as e:
        data["charts"] = []
        data["_chart_debug"] = {"step": "exception", "error": str(e)[:200]}
        return data


def _inject_chart_into_response(data, queries_full):
    """Wrapper legado — agora delega para _process_response."""
    debug = {"version": "v4-validate-chart", "queries": len(queries_full), "step": "init"}
    try:
        content = data.get("content", [])
        text_block = next((b for b in content if b.get("type") == "text"), None)
        if not text_block:
            debug["step"] = "no-text-block"
            data["_chart_debug"] = debug
            return data

        text = text_block.get("text", "")

        # Procura por [CHART]...[/CHART] no texto e valida o JSON
        chart_pattern = re.compile(r'\[CHART\]([\s\S]*?)\[/CHART\]', re.IGNORECASE)
        matches = list(chart_pattern.finditer(text))

        # Se há match completo com JSON válido → mantém
        valid_existing = False
        for m in matches:
            try:
                parsed = json.loads(m.group(1).strip())
                # Valida estrutura mínima
                if isinstance(parsed, dict) and parsed.get("type") and (parsed.get("values") or parsed.get("datasets")):
                    valid_existing = True
                    break
            except Exception:
                pass

        if valid_existing:
            debug["step"] = "valid-chart-exists"
            data["_chart_debug"] = debug
            return data

        # Limpa qualquer [CHART] inválido / órfão / texto literal mencionando "[CHART]"
        cleaned = chart_pattern.sub('', text)
        # Remove tags soltas sem fechamento
        cleaned = re.sub(r'\[CHART\][\s\S]*?$', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\[/CHART\]', '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.rstrip()

        if not queries_full:
            text_block["text"] = cleaned
            debug["step"] = "no-queries-full"
            data["_chart_debug"] = debug
            return data

        chart_cfg = None
        tried = []
        for q in reversed(queries_full):
            rows = q.get("rows", [])
            tried.append(len(rows))
            cfg = _auto_chart_from_rows(rows, q.get("sql", ""))
            if cfg:
                chart_cfg = cfg
                break

        debug["tried_rows"] = tried
        debug["had_invalid_chart"] = bool(matches)
        if not chart_cfg:
            text_block["text"] = cleaned
            debug["step"] = "no-valid-chart"
            data["_chart_debug"] = debug
            return data

        chart_block = "\n\n[CHART]" + json.dumps(chart_cfg, ensure_ascii=False, default=str) + "[/CHART]"
        text_block["text"] = cleaned + chart_block
        debug["step"] = "injected"
        debug["type"] = chart_cfg.get("type")
        debug["labels_count"] = len(chart_cfg.get("labels", []))
        data["_chart_debug"] = debug
        data["_auto_chart_injected"] = True
        return data
    except Exception as e:
        debug["step"] = "exception"
        debug["error"] = str(e)[:200]
        data["_chart_debug"] = debug
        return data

@app.route("/api/chat", methods=["POST"])
def chat():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY não configurada"}), 500

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Payload inválido ou Content-Type incorreto"}), 400

    hdrs = {"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"}

    # Ferramenta de consulta SQL — exclusivamente Sybase IQ via agent
    query_tool = {
        "name": "query_database",
        "description": (
            f"Executa SELECT no Sybase IQ 16, schema {SYBASE_SCHEMA}. "
            f"REGRAS CRÍTICAS: "
            f"(1) Use TOP N — NUNCA LIMIT. "
            f"(2) Case sensitive: NUNCA use UPPER()/LOWER(), use o valor exato. "
            f"(3) Sempre qualifique: {SYBASE_SCHEMA}.tabela. "
            f"(4) Datas: YEAR(), MONTH(), DATEPART(), formato 'YYYY-MM-DD'. "
            f"(5) Nulos: ISNULL() não IFNULL(). "
            f"(6) Se não conhecer as colunas, execute SELECT TOP 5 * primeiro. "
            f"Retorna até 500 linhas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        f"Query SELECT Sybase IQ. "
                        f"✔ SELECT TOP 50 col FROM {SYBASE_SCHEMA}.tabela WHERE col = 'Valor Exato' "
                        f"✘ NUNCA: LIMIT, UPPER(), LOWER(), GROUP_CONCAT(), IFNULL()"
                    )
                }
            },
            "required": ["sql"]
        }
    }

    try:
        # Limita histórico às últimas 8 trocas (16 mensagens) — economiza tokens
        msgs_raw = list(payload.get("messages", []))
        msgs = msgs_raw[-16:] if len(msgs_raw) > 16 else msgs_raw

        # ── Monta system prompt ──────────────────────────────────────────────
        # FONTE ÚNICA DE VERDADE: db_rules.txt (editável sem deploy).
        # O frontend pode opcionalmente acrescentar contexto dinâmico em "system".
        db_rules = load_db_rules()
        frontend_system = str(payload.get("system", "") or "").strip()

        if db_rules and frontend_system:
            system_text = db_rules + "\n\n" + frontend_system
        else:
            system_text = db_rules or frontend_system

        # System prompt com cache_control — Anthropic prompt caching (5 min TTL)
        system_blocks = [{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"}
        }] if system_text else []

        call = {
            "model":      payload.get("model", "claude-haiku-4-5-20251001"),
            "max_tokens": payload.get("max_tokens", 1800),
            "system":     system_blocks,
            "tools":      [query_tool],
            "messages":   msgs
        }

        queries_run = []
        queries_full = []  # rows completos para auto-chart
        last_text_data = None
        data = {}
        MAX_QUERIES = 3  # limite duro — após 3, ferramenta é removida

        for _ in range(8):  # até 8 iterações de turno
            # Desativa a tool quando atingiu o limite de queries
            if len(queries_run) >= MAX_QUERIES:
                call["tools"] = []

            # ── Chamada à API Anthropic com retry para 529 (overloaded) ──
            resp = None
            backoffs = [0, 8, 16, 24]  # 4 tentativas: 0, 8s, 16s, 24s
            for attempt, wait in enumerate(backoffs):
                if wait:
                    time.sleep(wait)
                try:
                    resp = requests.post("https://api.anthropic.com/v1/messages",
                                         headers=hdrs, json=call, timeout=90)
                except requests.exceptions.Timeout:
                    if attempt == len(backoffs) - 1:
                        return jsonify({"error": "Timeout ao chamar API Anthropic (>90s)"}), 504
                    continue
                except requests.exceptions.ConnectionError as e:
                    if attempt == len(backoffs) - 1:
                        return jsonify({"error": f"Falha de conexão com API Anthropic: {e}"}), 502
                    continue
                # 529 = overloaded — retentar
                if resp.status_code == 529 and attempt < len(backoffs) - 1:
                    continue
                break

            if resp is None:
                return jsonify({"error": "Falha total ao contatar API Anthropic"}), 502

            if resp.status_code != 200:
                try:
                    err_body = resp.json()
                except Exception:
                    err_body = {"error": resp.text[:300]}
                return jsonify(err_body), resp.status_code

            try:
                data = resp.json()
            except Exception as e:
                return jsonify({"error": f"Resposta inválida da API Anthropic: {e}"}), 502

            stop    = data.get("stop_reason")
            content = data.get("content", [])

            has_text = any(blk.get('type') == 'text' and blk.get('text', '').strip()
                           for blk in content)
            if has_text:
                last_text_data = data

            if stop == "tool_use":
                call["messages"].append({"role": "assistant", "content": content})
                results = []
                for blk in content:
                    if blk.get("type") == "tool_use" and blk.get("name") == "query_database":
                        sql = blk.get("input", {}).get("sql", "")
                        try:
                            rows = sybase_query(sql, limit=500)
                            queries_run.append({"sql": sql, "linhas": len(rows), "db": "sybase"})
                            queries_full.append({"sql": sql, "rows": rows})
                            payload_res = json.dumps({"linhas": len(rows), "dados": rows},
                                                     ensure_ascii=False, default=str)
                        except Exception as e:
                            payload_res = json.dumps({"erro": str(e)})
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": blk["id"],
                            "content": payload_res
                        })
                call["messages"].append({"role": "user", "content": results})

            elif stop == "end_turn" and _is_announcement(content):
                # Modelo anunciou algo mas não entregou — forçar execução
                call["messages"].append({"role": "assistant", "content": content})
                push_msg = ("Execute a query e apresente os dados agora." if not queries_run
                            else "Apresente os resultados agora com o gráfico. Não anuncie — entregue direto.")
                call["messages"].append({"role": "user", "content": push_msg})

            elif stop == "end_turn" and _is_refusal_without_query(content, queries_run):
                # Modelo recusou sem tentar — forçar pelo menos um SELECT exploratório
                call["messages"].append({"role": "assistant", "content": content})
                call["messages"].append({"role": "user", "content": (
                    "ATENÇÃO: você DEVE executar uma query antes de dizer que não há dados. "
                    "Use SELECT TOP 5 * para verificar se a tabela existe. "
                    "Execute agora e relate o que encontrou."
                )})
            else:
                # Resposta final
                if queries_run:
                    data["queries_executed"] = queries_run
                data = _process_response(data, queries_full)
                return jsonify(data), resp.status_code

        # ── Fallback após 6 iterações ────────────────────────────────────────
        fallback = last_text_data or data
        if queries_run:
            fallback["queries_executed"] = queries_run

        fallback = _process_response(fallback, queries_full)
        has_chart = bool(fallback.get("charts"))

        text_block = next((b for b in fallback.get("content", []) if b.get("type") == "text"), None)
        if text_block and _is_announcement([text_block]):
            if has_chart:
                text_block["text"] = "Apresento os dados consolidados das consultas realizadas:"
            else:
                lines_summary = ", ".join(f"{q.get('linhas',0)} linhas" for q in queries_run) or "nenhuma"
                text_block["text"] = (
                    f"Não consegui completar a análise nessa rodada — fiz {len(queries_run)} consulta(s) "
                    f"({lines_summary}), mas os dados retornados não permitiram montar uma resposta clara. "
                    f"Tente reformular a pergunta com mais detalhes (ano específico, secretaria, etc.). "
                    f"Os anos com dados disponíveis são 2023–2025."
                )
        return jsonify(fallback), 200

    except Exception as exc:
        # Garante que NUNCA retorna HTML — sempre JSON com mensagem de erro
        import traceback
        print(f"[chat] ERRO NÃO TRATADO: {exc}\n{traceback.format_exc()}")
        return jsonify({"error": f"Erro interno do servidor: {str(exc)}"}), 500

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
    # Schemas das tabelas principais
    try:
        rec_cols  = [r["name"] for r in query("PRAGMA table_info(receita)")]
        desp_cols = [r["name"] for r in query("PRAGMA table_info(despesa)")]
    except Exception:
        rec_cols, desp_cols = [], []

    # Bases de dados adicionais — com amostra + estatísticas
    try:
        bases = query("SELECT name, label, rows, cols FROM _bases_catalog ORDER BY uploaded_at DESC")
        bases_info = []
        for b in bases:
            cols = json.loads(b["cols"] or "[]")
            col_names = [c["col"] for c in cols]
            detail = {"name": b["name"], "label": b["label"],
                      "registros": b["rows"], "colunas": col_names}
            try:
                # Amostra de 15 linhas
                sample = query(f'SELECT * FROM "base_{b["name"]}" LIMIT 15')
                detail["amostra"] = sample
                # Estatísticas de colunas numéricas
                num_cols = [c["col"] for c in cols if any(t in c["type"].lower()
                            for t in ["int","float","num","real","double","decimal"])][:6]
                if num_cols:
                    parts = [f'SUM("{c}") as "s__{c}", AVG("{c}") as "a__{c}", MIN("{c}") as "mi__{c}", MAX("{c}") as "ma__{c}"'
                             for c in num_cols]
                    stats = query(f'SELECT {", ".join(parts)} FROM "base_{b["name"]}"')
                    detail["stats"] = stats[0] if stats else {}
            except Exception as ex:
                detail["erro"] = str(ex)
            bases_info.append(detail)
    except Exception:
        bases_info = []
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
        "despesa_secretaria_2024": [{"secretaria": r["secretaria"], "empenhado": r["empenhado"]} for r in sec_2024],
        "bases_adicionais": bases_info,
        "schema_receita": rec_cols,
        "schema_despesa": desp_cols,
        "sybase_disponivel": sybase_available(),
        "sybase_schema": SYBASE_SCHEMA if sybase_available() else None,
        "sybase_tabelas": _get_sybase_context_tables() if sybase_available() else [],
        "sybase_iptu_schemas": _get_iptu_schemas() if sybase_available() else {}
    })

def _get_sybase_context_tables():
    """Retorna lista completa de tabelas Sybase para o contexto do chat."""
    try:
        return sybase_tables()
    except Exception:
        return []

# Tabelas IPTU prioritárias — schema pré-carregado no contexto
IPTU_PRIORITY_TABLES = ["PREFEITURA_IPTU_LANCADO", "PREFEITURA_IPTU_PAGO"]

def _get_iptu_schemas():
    """Pré-carrega schema das tabelas IPTU prioritárias."""
    result = {}
    for tbl in IPTU_PRIORITY_TABLES:
        try:
            cols = sybase_schema(tbl)
            # sybase_schema retorna lista de dicts ou lista de strings
            if cols and isinstance(cols, list):
                if isinstance(cols[0], dict):
                    col_names = [c.get("column_name") or c.get("name") or str(c) for c in cols]
                else:
                    col_names = [str(c) for c in cols]
                result[tbl] = col_names
        except Exception:
            result[tbl] = []
    return result

@app.route("/api/sybase/health")
@login_required
def api_sybase_health():
    return jsonify(sybase_health())

@app.route("/api/sybase/tables")
@login_required
def api_sybase_tables():
    if not sybase_available():
        return jsonify({"error": "Agente Sybase não configurado"}), 503
    return jsonify(sybase_tables())

@app.route("/api/sybase/schema/<table>")
@login_required
def api_sybase_schema(table):
    if not sybase_available():
        return jsonify({"error": "Agente Sybase não configurado"}), 503
    return jsonify(sybase_schema(table))

@app.route("/api/sybase/query", methods=["POST"])
@login_required
def api_sybase_query():
    if not sybase_available():
        return jsonify({"error": "Agente Sybase não configurado"}), 503
    sql   = request.json.get("sql", "")
    limit = request.json.get("limit", 100)
    try:
        rows = sybase_query(sql, limit=limit)
        return jsonify({"linhas": len(rows), "dados": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ── Endpoints de Regras do Banco ─────────────────────────────────────────
@app.route("/api/db-rules", methods=["GET"])
@login_required
def get_db_rules():
    """Retorna o conteúdo atual do arquivo de regras."""
    content = load_db_rules()
    try:
        mtime = os.path.getmtime(DB_RULES_PATH)
        modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        modified = None
    return jsonify({
        "ok": True,
        "content": content,
        "path": DB_RULES_PATH,
        "modified": modified,
        "size_chars": len(content)
    })

@app.route("/api/db-rules/add", methods=["POST"])
@login_required
def add_db_rule():
    """Adiciona uma nova regra ao arquivo db_rules.txt."""
    body = request.json or {}
    rule_text = body.get("rule", "").strip()
    if not rule_text:
        return jsonify({"ok": False, "error": "Campo 'rule' é obrigatório e não pode ser vazio"}), 400
    if len(rule_text) > 4000:
        return jsonify({"ok": False, "error": "Regra muito longa (máx 4000 chars)"}), 400
    ok = append_db_rule(rule_text)
    if ok:
        return jsonify({"ok": True, "message": "Regra adicionada com sucesso"})
    return jsonify({"ok": False, "error": "Falha ao gravar arquivo db_rules.txt"}), 500

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

@app.route("/bases")
@login_required
def bases_page():
    return render_template("bases.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

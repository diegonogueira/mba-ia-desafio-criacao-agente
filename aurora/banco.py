"""Dados do condomínio em SQLite (estado/condominio.db).

As regras que não podem depender do modelo moram aqui, no esquema e nas
transações:

- ``reserva_ativa_unica``: índice único parcial que impede duas reservas
  ativas da mesma área na mesma data, valendo no instante do INSERT.
- ``codigos_emitidos``: todo código de reserva já usado (inclusive de reservas
  canceladas e das iniciais). A restauração não limpa essa tabela, então um
  código nunca se repete.
- ``confirmacao_id UNIQUE``: uma confirmação aprovada produz no máximo uma
  gravação, mesmo que a tool seja executada de novo.
"""

import json
import secrets
import sqlite3
import string
from contextlib import contextmanager
from functools import cache

from . import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada')),
    confirmacao_id TEXT UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS reserva_ativa_unica
    ON reservas (area, data) WHERE status = 'ativa';
CREATE TABLE IF NOT EXISTS codigos_emitidos (
    codigo TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS visitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL,
    confirmacao_id TEXT UNIQUE
);
"""

_ALFABETO_CODIGO = string.ascii_uppercase + string.digits


def _ler_json(nome: str):
    return json.loads((config.DADOS / nome).read_text(encoding="utf-8"))


@cache
def areas() -> dict[str, dict]:
    """Áreas comuns por id, lidas de dados/areas.json."""
    return {a["id"]: a for a in _ler_json("areas.json")}


@cache
def apartamentos() -> set[str]:
    return {a["numero"] for a in _ler_json("apartamentos.json")}


@contextmanager
def conectar():
    config.ESTADO.mkdir(exist_ok=True)
    con = sqlite3.connect(config.BANCO_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    try:
        yield con
    finally:
        con.close()


@contextmanager
def transacao():
    """Transação de escrita (BEGIN IMMEDIATE): commit no sucesso, rollback no erro."""
    with conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de dados/*.json."""
    with transacao() as con:
        for comando in filter(str.strip, ESQUEMA.split(";")):
            con.execute(comando)
        con.execute("DELETE FROM reservas")
        con.execute("DELETE FROM visitantes")
        for r in _ler_json("reservas.json"):
            con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                (r["codigo"], r["apartamento"], r["area"], r["data"]),
            )
            con.execute("INSERT OR IGNORE INTO codigos_emitidos (codigo) VALUES (?)", (r["codigo"],))
        for v in _ler_json("visitantes.json"):
            con.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (v["apartamento"], v["nome"], v["data"]),
            )


def garantir_banco() -> None:
    """Cria o banco com os dados iniciais se ele ainda não existir."""
    if not config.BANCO_CONDOMINIO.exists():
        restaurar()


def area_ocupada(area: str, data: str) -> bool:
    with conectar() as con:
        linha = con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        ).fetchone()
    return linha is not None


def reservas_do_apartamento(apartamento: str) -> list[dict]:
    with conectar() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, area",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    with conectar() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def _novo_codigo(con: sqlite3.Connection) -> str:
    while True:
        codigo = "RSV-" + "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(6))
        try:
            con.execute("INSERT INTO codigos_emitidos (codigo) VALUES (?)", (codigo,))
            return codigo
        except sqlite3.IntegrityError:
            continue


def criar_reserva(
    apartamento: str, area: str, data: str, confirmacao_id: str | None = None
) -> dict:
    """Grava uma reserva. A exclusividade é do índice ``reserva_ativa_unica``.

    Retorna ``{"status": "criada", "codigo": ...}``, ``{"status": "ocupada"}``
    ou, se esta confirmação já tinha gravado, a mesma reserva de antes.
    """
    try:
        with transacao() as con:
            if confirmacao_id:
                anterior = con.execute(
                    "SELECT codigo FROM reservas WHERE confirmacao_id = ?", (confirmacao_id,)
                ).fetchone()
                if anterior:
                    return {"status": "criada", "codigo": anterior["codigo"]}
            codigo = _novo_codigo(con)
            con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, confirmacao_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (codigo, apartamento, area, data, confirmacao_id),
            )
    except sqlite3.IntegrityError:
        # O índice único recusou: outra reserva ativa da área nesta data
        # entrou antes (inclusive por uma aprovação simultânea).
        return {"status": "ocupada"}
    return {"status": "criada", "codigo": codigo}


def cancelar_reserva(
    apartamento: str, codigo: str | None = None, area: str | None = None, data: str | None = None
) -> list[dict]:
    """Cancela reservas ativas do apartamento que casam com o código ou com área+data."""
    with transacao() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa'"
            " AND (codigo = ? OR (area = ? AND data = ?))",
            (apartamento, codigo, area, data),
        ).fetchall()
        for l in linhas:
            con.execute("UPDATE reservas SET status = 'cancelada' WHERE codigo = ?", (l["codigo"],))
    return [dict(l) for l in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str, confirmacao_id: str) -> None:
    with transacao() as con:
        con.execute(
            "INSERT OR IGNORE INTO visitantes (apartamento, nome, data, confirmacao_id)"
            " VALUES (?, ?, ?, ?)",
            (apartamento, nome, data, confirmacao_id),
        )

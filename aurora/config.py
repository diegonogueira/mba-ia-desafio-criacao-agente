"""Caminhos, nomes e modelo usados pelo assistente."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DADOS = RAIZ / "dados"
ESTADO = RAIZ / "estado"
BANCO_CONDOMINIO = ESTADO / "condominio.db"
BANCO_SESSOES = ESTADO / "sessoes.db"
REGULAMENTO = DADOS / "regulamento.md"

APP_NAME = "residencial_aurora"
USER_ID = "morador"

MODELO = os.getenv("AURORA_MODELO") or "gemini-3.5-flash-lite"

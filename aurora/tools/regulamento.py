"""Consulta ao regulamento por capítulo.

O texto de dados/regulamento.md nunca entra nas instruções de nenhum agente:
o especialista recebe só os títulos e lê um capítulo por vez com
``ler_capitulo``. Ele roda via AgentTool, em sessão própria em memória, então
nem o capítulo lido entra nos eventos da sessão do morador; lá fica só a
resposta final.
"""

import re
from functools import cache

from .. import config

_TITULO = re.compile(r"^## Capítulo ([IVXLC]+): (.+)$", re.MULTILINE)


@cache
def capitulos() -> list[dict]:
    texto = config.REGULAMENTO.read_text(encoding="utf-8")
    marcas = list(_TITULO.finditer(texto))
    resultado = []
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        resultado.append(
            {"numero": i + 1, "romano": m.group(1), "titulo": m.group(2), "texto": texto[m.start():fim].strip()}
        )
    return resultado


def indice() -> str:
    """Só os títulos dos capítulos, para a instrução do especialista."""
    return "\n".join(f"{c['numero']}. Capítulo {c['romano']}: {c['titulo']}" for c in capitulos())


def ler_capitulo(numero: int) -> dict:
    """Devolve o texto de um único capítulo do regulamento interno.

    Args:
      numero: número do capítulo (1 a 14), conforme o índice.
    """
    for c in capitulos():
        if c["numero"] == numero:
            return {"capitulo": f"Capítulo {c['romano']}: {c['titulo']}", "texto": c["texto"]}
    return {"erro": f"Capítulo inexistente. Use um número de 1 a {len(capitulos())}."}

"""O apartamento que as tools usam vem da sessão, nunca de argumento do modelo."""

from google.adk.tools import ToolContext

from .. import banco

CHAVE_APARTAMENTO = "apartamento"


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    """Apartamento gravado no state na criação da sessão (POST /sessoes).

    Nenhuma tool recebe apartamento como parâmetro e nenhuma escreve esta
    chave, então o modelo não tem como trocar o apartamento da sessão.
    """
    apartamento = tool_context.state.get(CHAVE_APARTAMENTO)
    if apartamento not in banco.apartamentos():
        raise PermissionError("Sessão sem apartamento válido.")
    return apartamento

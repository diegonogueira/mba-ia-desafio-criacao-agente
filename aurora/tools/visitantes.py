"""Tools do especialista de visitantes. O apartamento vem da sessão."""

from datetime import date

from google.adk.tools import ToolContext

from .. import banco
from .sessao import apartamento_da_sessao


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados do apartamento do morador desta conversa."""
    return {"visitantes": banco.visitantes_do_apartamento(apartamento_da_sessao(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio para o apartamento do morador.

    Liberar acesso sempre exige que o morador aprove a confirmação pelo
    aplicativo; a tool cuida desse pedido sozinha.

    Args:
      nome: nome completo do visitante.
      data: data da visita no formato AAAA-MM-DD.
    """
    nome = " ".join(nome.split())
    if not nome:
        return {"status": "erro", "mensagem": "Informe o nome do visitante."}
    try:
        date.fromisoformat(data)
    except ValueError:
        return {"status": "erro", "mensagem": "Data inválida. Use o formato AAAA-MM-DD."}
    apartamento = apartamento_da_sessao(tool_context)

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        # Garantia 1: liberar acesso fica pendente até a rota de confirmações.
        tool_context.request_confirmation(
            hint=f"Liberar a entrada de {nome} em {data}. Aprove ou negue pelo aplicativo.",
            payload={"nome": nome, "data": data},
        )
        return {
            "status": "aguardando_confirmacao",
            "mensagem": "Pedido de confirmação enviado ao morador pelo aplicativo.",
        }
    if not confirmacao.confirmed:
        return {"status": "nao_confirmada", "mensagem": "O morador negou. Nenhum acesso foi liberado."}

    banco.autorizar_visitante(apartamento, nome, data, confirmacao_id=tool_context.function_call_id)
    return {
        "status": "autorizado",
        "mensagem": "Entrada liberada e concluída. O morador já aprovou.",
        "nome": nome,
        "data": data,
    }

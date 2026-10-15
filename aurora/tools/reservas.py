"""Tools do especialista de reservas.

Nenhuma tool recebe apartamento: ele vem de ``apartamento_da_sessao``. A agenda
de outros apartamentos só é usada para dizer se a data está livre ou ocupada,
sem código nem apartamento do dono.
"""

from datetime import date

from google.adk.tools import ToolContext

from .. import banco
from .sessao import apartamento_da_sessao

MSG_OCUPADA = "A área já está reservada nesta data. Escolha outra data."


def _validar(area: str, data: str) -> dict | None:
    if area not in banco.areas():
        return {
            "status": "erro",
            "mensagem": f"Área desconhecida. Use um destes ids: {', '.join(banco.areas())}.",
        }
    try:
        date.fromisoformat(data)
    except ValueError:
        return {"status": "erro", "mensagem": "Data inválida. Use o formato AAAA-MM-DD."}
    return None


def _nome_area(area: str) -> str:
    return banco.areas()[area]["nome"]


def consultar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre em uma data.

    Args:
      area: id da área (salao-de-festas, churrasqueira ou quadra).
      data: data no formato AAAA-MM-DD.
    """
    if erro := _validar(area, data):
        return erro
    return {"area": area, "data": data, "disponivel": not banco.area_ocupada(area, data)}


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    apartamento = apartamento_da_sessao(tool_context)
    reservas = [
        {**r, "nome_area": _nome_area(r["area"])}
        for r in banco.reservas_do_apartamento(apartamento)
    ]
    return {"reservas": reservas}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Áreas com taxa geram cobrança e só são gravadas depois que o morador
    aprova a confirmação pelo aplicativo; a tool cuida desse pedido sozinha.

    Args:
      area: id da área (salao-de-festas, churrasqueira ou quadra).
      data: data no formato AAAA-MM-DD.
    """
    if erro := _validar(area, data):
        return erro
    apartamento = apartamento_da_sessao(tool_context)
    taxa = banco.areas()[area]["taxa"]

    if taxa > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            if banco.area_ocupada(area, data):
                return {"status": "indisponivel", "mensagem": MSG_OCUPADA}
            # Garantia 1: a cobrança fica pendente até a rota de confirmações.
            tool_context.request_confirmation(
                hint=(
                    f"Reservar {_nome_area(area)} em {data} gera cobrança de"
                    f" R$ {taxa:.2f}. Aprove ou negue pelo aplicativo."
                ),
                payload={"area": area, "data": data, "taxa": taxa},
            )
            return {
                "status": "aguardando_confirmacao",
                "mensagem": "Pedido de confirmação enviado ao morador pelo aplicativo.",
            }
        if not confirmacao.confirmed:
            return {"status": "nao_confirmada", "mensagem": "O morador negou a cobrança. Nada foi reservado."}

    resultado = banco.criar_reserva(
        apartamento, area, data, confirmacao_id=tool_context.function_call_id if taxa > 0 else None
    )
    if resultado["status"] == "ocupada":
        return {"status": "indisponivel", "mensagem": MSG_OCUPADA}
    return {
        "status": "reservada",
        "mensagem": "Reserva gravada e concluída." + (" O morador já aprovou a cobrança." if taxa > 0 else ""),
        "codigo": resultado["codigo"],
        "area": area,
        "data": data,
        "taxa": taxa,
    }


def cancelar_minha_reserva(
    tool_context: ToolContext, codigo: str = "", area: str = "", data: str = ""
) -> dict:
    """Cancela uma reserva do apartamento do morador desta conversa.

    Informe o código da reserva ou a área e a data.

    Args:
      codigo: código da reserva (ex.: RSV-1234), se o morador souber.
      area: id da área, quando o morador indicar área e data.
      data: data no formato AAAA-MM-DD, quando o morador indicar área e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    if not codigo and not (area and data):
        return {"status": "erro", "mensagem": "Informe o código ou a área e a data da reserva."}
    canceladas = banco.cancelar_reserva(
        apartamento, codigo=codigo or None, area=area or None, data=data or None
    )
    if not canceladas:
        return {
            "status": "nao_encontrada",
            "mensagem": "Não existe reserva ativa do seu apartamento com esses dados. Nada foi cancelado.",
        }
    return {"status": "cancelada", "reservas": canceladas}

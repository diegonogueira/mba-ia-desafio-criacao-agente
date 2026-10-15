"""Comando `aurora-restaurar`: volta reservas e visitantes a dados/*.json.

As sessões (estado/sessoes.db) não são apagadas.
"""

from . import banco


def main() -> None:
    banco.restaurar()
    print("Reservas e visitantes restaurados a partir de dados/.")


if __name__ == "__main__":
    main()

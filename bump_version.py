from __future__ import annotations

import argparse
import re
from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parent / "version.py"
VERSION_PATTERN = re.compile(r'^VERSION = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)


def ler_versao() -> tuple[int, int, int]:
    conteudo = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(conteudo)
    if not match:
        raise ValueError("Nao foi possivel localizar VERSION em version.py")
    return tuple(int(parte) for parte in match.groups())


def escrever_versao(major: int, minor: int, patch: int) -> None:
    conteudo = VERSION_FILE.read_text(encoding="utf-8")
    novo_conteudo, substituicoes = VERSION_PATTERN.subn(
        f'VERSION = "{major}.{minor}.{patch}"',
        conteudo,
        count=1,
    )
    if substituicoes != 1:
        raise ValueError("Nao foi possivel atualizar VERSION em version.py")
    VERSION_FILE.write_text(novo_conteudo, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Atualiza a versao semantica do dashboard.")
    parser.add_argument("tipo", choices=["patch", "minor", "major"], help="Tipo de incremento semantico.")
    args = parser.parse_args()

    major, minor, patch = ler_versao()
    if args.tipo == "patch":
        patch += 1
    elif args.tipo == "minor":
        minor += 1
        patch = 0
    else:
        major += 1
        minor = 0
        patch = 0

    escrever_versao(major, minor, patch)
    print(f"{major}.{minor}.{patch}")


if __name__ == "__main__":
    main()
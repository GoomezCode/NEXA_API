"""Gera o hash argon2id da senha do administrador (ADMIN_PASSWORD_HASH)."""

from __future__ import annotations

import argparse
import getpass
import sys

from argon2 import PasswordHasher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera um hash argon2id para ADMIN_PASSWORD_HASH.")
    parser.add_argument("password", nargs="?", help="Senha (omitida: leitura interativa).")
    args = parser.parse_args(argv)

    password = args.password if args.password is not None else getpass.getpass("Senha: ")
    if not password:
        print("erro: a senha não pode ser vazia", file=sys.stderr)
        return 2

    print(PasswordHasher().hash(password))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

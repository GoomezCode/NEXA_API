"""Endpoints administrativos de usuários de teste (F13-06, SRS §3.7.10)."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import require_admin
from app.core.errors import ApiError, ValidationErrorItem
from app.core.logging import get_logger
from app.core.users import UserService
from app.db.models import UserRow
from app.db.repository import decode_cursor
from app.schemas.users import (
    BulkCreateRequest,
    BulkCreateResponse,
    CleanupResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    UserCreate,
    UserCreateResponse,
    UserPage,
    UserResponse,
    UserUpdate,
)

router = APIRouter(
    prefix="/api/v1/admin/users",
    tags=["Usuários (admin)"],
    dependencies=[Depends(require_admin)],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Token ausente ou inválido."}},
)
logger = get_logger("nexa.users")


def _users(request: Request) -> UserService:
    return cast(UserService, request.app.state.user_service)


def _response(row: UserRow) -> UserResponse:
    return UserResponse(**_public_fields(row))


def _public_fields(row: UserRow) -> dict[str, object]:
    fields = asdict(row)
    fields.pop("password_hash", None)
    return fields


def _created(row: UserRow, password: str) -> UserCreateResponse:
    return UserCreateResponse(**_public_fields(row), password=password)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=UserCreateResponse,
    summary="Cria um usuário de teste",
    description=(
        "Cria um usuário descartável (`role=user`, `is_test=1`). Sem senha, uma "
        "senha forte é gerada e devolvida apenas nesta resposta."
    ),
    responses={
        status.HTTP_201_CREATED: {"description": "Usuário criado; a senha aparece uma única vez."},
        status.HTTP_403_FORBIDDEN: {
            "description": "Requer papel `admin` ou usuários de teste desabilitados."
        },
        status.HTTP_409_CONFLICT: {"description": "Username ou e-mail já utilizado."},
    },
)
async def create_user(
    payload: UserCreate,
    request: Request,
    response: Response,
) -> UserCreateResponse:
    row, password = await _users(request).create_test_user(payload)
    _no_store(response)
    return _created(row, password)


@router.post(
    "/bulk",
    status_code=status.HTTP_201_CREATED,
    response_model=BulkCreateResponse,
    summary="Cria usuários de teste em lote",
    description=(
        "Cria até `TEST_USER_MAX_BULK` usuários com usernames sequenciais. As senhas "
        "são geradas e devolvidas uma única vez."
    ),
    responses={
        status.HTTP_201_CREATED: {
            "description": "Usuários criados; senhas exibidas uma única vez."
        },
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Lote acima do limite."},
    },
)
async def create_users_bulk(
    payload: BulkCreateRequest,
    request: Request,
    response: Response,
) -> BulkCreateResponse:
    created = await _users(request).create_bulk(payload)
    _no_store(response)
    return BulkCreateResponse(users=[_created(row, password) for row, password in created])


@router.post(
    "/cleanup",
    response_model=CleanupResponse,
    summary="Limpa usuários de teste expirados",
    description=(
        "Remove somente `is_test=1`, não protegidos, com `expires_at` vencido. "
        "O admin nunca é candidato."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Quantidade de usuários removidos."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
    },
)
async def cleanup_users(request: Request) -> CleanupResponse:
    removed = await _users(request).cleanup_expired()
    return CleanupResponse(removed=removed)


@router.get(
    "",
    response_model=UserPage,
    summary="Lista usuários",
    description=(
        "Lista usuários com paginação por cursor opaco e filtros por papel, "
        "`is_test` e `is_active`. Limite máximo de 100."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Página de usuários."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Cursor inválido."},
    },
)
async def list_users(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    role: str | None = None,
    is_test: bool | None = None,
    is_active: bool | None = None,
) -> UserPage:
    service = _users(request)
    if role is not None and role not in {"admin", "user", "guest"}:
        raise ApiError(
            "validation_error",
            errors=[ValidationErrorItem(field="role", reason="use admin, user ou guest")],
        )
    try:
        decoded = decode_cursor(cursor) if cursor else None
    except ValueError as exc:
        raise ApiError(
            "validation_error",
            errors=[ValidationErrorItem(field="cursor", reason="cursor inválido")],
        ) from exc

    rows, next_cursor = await service.list_users(
        limit=limit,
        cursor=decoded,
        role=role,
        is_test=is_test,
        is_active=is_active,
    )
    return UserPage(users=[_response(row) for row in rows], next_cursor=next_cursor)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Consulta um usuário",
    description="Devolve os dados públicos de um usuário, sem `password_hash`.",
    responses={
        status.HTTP_200_OK: {"description": "Usuário encontrado."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_404_NOT_FOUND: {"description": "Usuário inexistente."},
    },
)
async def get_user(user_id: str, request: Request) -> UserResponse:
    row = await _users(request).get_by_id(user_id)
    if row is None:
        raise ApiError("not_found")
    return _response(row)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Atualiza um usuário de teste",
    description=(
        "Atualiza username/e-mail/limites/atividade/expiração. Não altera `role`, "
        "`is_test`, `is_protected` nem o admin."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Usuário atualizado."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_404_NOT_FOUND: {"description": "Usuário inexistente."},
        status.HTTP_409_CONFLICT: {"description": "Admin protegido ou username duplicado."},
    },
)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
) -> UserResponse:
    row = await _users(request).update_user(user_id, payload)
    return _response(row)


@router.post(
    "/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
    summary="Redefine a senha de um usuário de teste",
    description=(
        "Gera ou aceita uma nova senha, invalida o cache de autenticação e a devolve "
        "uma única vez. O admin não pode ser alvo."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Senha redefinida."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_404_NOT_FOUND: {"description": "Usuário inexistente."},
        status.HTTP_409_CONFLICT: {"description": "Admin protegido."},
    },
)
async def reset_password(
    user_id: str,
    request: Request,
    response: Response,
    payload: ResetPasswordRequest | None = None,
) -> ResetPasswordResponse:
    row, password = await _users(request).reset_password(
        user_id, payload.password if payload is not None else None
    )
    _no_store(response)
    return ResetPasswordResponse(id=row.id, username=row.username, password=password)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Exclui um usuário de teste",
    description="Exclui somente usuários de teste; o admin retorna `409 admin_protected`.",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Usuário excluído."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_404_NOT_FOUND: {"description": "Usuário inexistente."},
        status.HTTP_409_CONFLICT: {"description": "Admin protegido ou usuário não descartável."},
    },
)
async def delete_user(user_id: str, request: Request) -> Response:
    await _users(request).delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]

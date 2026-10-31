# Documentação — Nexa API

Este índice reúne especificação, implementação, contratos, critérios de aceitação e operação da Nexa API. O [README principal](../README.md) é o guia de instalação e uso; os documentos abaixo detalham contratos e decisões técnicas.

## Estado atual

O código contém o gateway multi-provedor, compatibilidade OpenAI/Anthropic/Ollama, SQLite para contas e chaves BYOK, cadastro configurável e personalização limitada. O pacote ainda reporta `1.1.0`; a release `2.0.0` não foi publicada. A fase F17 de hardening e aceite operacional permanece aberta, e CA-46/CA-47 estão pendentes conforme a matriz.

## Guias

| Documento | Para que serve | Estado/escopo |
|---|---|---|
| [SRS API Chat IA](srs-api-chat-ia-v1.md) | Requisitos, regras, endpoints e critérios CA-01 a CA-47 | Especificação v2.0; critérios de release finais ainda em validação |
| [Plano de implementação](plano-implementacao-api-chat-ia-v1.md) | Fases, arquitetura, execução, segurança e Definition of Done | F12–F16 e F18 implementadas; F17 aberta |
| [Provedores de IA](provedores-ia.md) | Configuração, roteamento, capacidades, fallback e BYOK | Gateway e BYOK implementados no código |
| [Compatibilidade com APIs externas](compatibilidade-apis-externas.md) | Contratos compatíveis OpenAI, Anthropic e Ollama | Implementações F14–F16; release ainda em 1.1.0 |
| [Banco SQLite](database-sqlite.md) | Schema, migrações, usuário ADMIN e performance | Implementado no código; validação de release pendente |
| [Matriz CA → testes](matriz-ca-testes.md) | Rastreabilidade dos critérios para testes | CA-01–CA-45 marcados; CA-46/CA-47 pendentes |
| [Checklist de release](checklist-release.md) | Marcos v1.1.0 e pendências para v2.0 | Release v2.0 ainda aberta |
| [Aceitação manual com Ollama](aceitacao-manual-2026-10-28.md) | Evidência de teste manual contra Ollama real | Registro histórico da v1.1.0, não aceite da v2.0 |

## Como usar os documentos

- Para iniciar a API, configurar variáveis ou executar exemplos: [README principal](../README.md).
- Para entender o contrato esperado: leia o [SRS](srs-api-chat-ia-v1.md).
- Para contribuir com código: consulte o [plano de implementação](plano-implementacao-api-chat-ia-v1.md) e os documentos de domínio relacionados.
- Para avaliar uma publicação: use o [checklist de release](checklist-release.md) junto da [matriz de testes](matriz-ca-testes.md).
- A aceitação manual de 28/10/2026 descreve apenas a versão 1.1.0; não a interprete como validação operacional da versão 2.0.

Os status informam o estado registrado nos documentos e no repositório; não equivalem a uma publicação/tag de release. A tag da API deve ser verificada separadamente no Git.

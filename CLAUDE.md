# CLAUDE.md — fork SHVIA do Strix

Guia para agentes/sessões futuras trabalhando neste repositório. **Leia antes de mexer.**

## O que é

Fork de [`usestrix/strix`](https://github.com/usestrix/strix) (ferramenta de
pentest com IA, Apache-2.0) em [`samirhvbr/strix`](https://github.com/samirhvbr/strix).
O upstream é frequente; mantemos as **nossas** mudanças isoladas para reduzir dor de merge.

## Branches (ver [FORK.md](FORK.md))

- **`master`** = nossa branch principal (default). Features nossas entram aqui.
- **`main`** = espelho do upstream; base para PRs pro upstream. **Não** colocar features nossas aqui.
- Feature nossa → `feat/*` a partir de `master`. Fix pro upstream → `fix/*` a partir de `main`.
- **Sempre `git fetch` antes de começar.** Sincronização com upstream: ver FORK.md.

## Versão

`<upstream>+shvia.<n>` em [`.fork-version`](.fork-version). **Não** editar o `version` do
`pyproject.toml` (conflita com o upstream). Registrar mudanças em [FORK.md](FORK.md).

## Setup / build / testes

```bash
uv sync                        # instala deps (editável). Roda com `uv run strix` ou o wrapper global `strix`.
uv run ruff format . && uv run ruff check .    # formatação + lint (limite 100 colunas)
uv run pytest -q               # suíte (~1139 testes, ~2min)
```

- **Interativo (TUI)** exige Go 1.24+ — **instalado** (`go1.24.4`), roda via `go run` na instalação editável. Runs manuais do `strix-run` abrem a TUI por padrão; `--auto` é sempre headless.
  `uv tool install .` também exige Go (build-hook `scripts/tui_sidecar_hook.py`).
- Rodar precisa de **Docker** (puxa imagem sandbox no 1º run).

## Configuração de execução (nosso launcher)

Não use `.bashrc` para chaves. Use o `.env` (no `.gitignore`) + o launcher:

```bash
cp .env.example .env           # preencha chaves, primário/secundário, budgets
./bin/strix-run <alvo>         # novo scan, agente PRIMÁRIO (TUI nativa; -n força headless)
./bin/strix-run --resume -2    # religa o último run no SECUNDÁRIO (-3 = TERCIÁRIO)
./bin/strix-run pdf <run>      # PDF do relatório LOCAL (sem e-mail/relay)
./bin/strix-run <alvo> --auto  # FILA primário→secundário→terciário (-3): esgotou janela/budget → próximo
```

O `bin/strix-run` resolve modelo + chave do provedor + `--max-budget` a partir do `.env`.
Runs saem em `$STRIX_WORKDIR/strix_runs/<run>/`. Ver o cabeçalho do script para todos os flags.

### Como o failover funciona (fatos do motor, mapeados)

- **`--resume <run>` continua com OUTRO modelo**: o `run.json` não guarda `STRIX_LLM`; o modelo
  vem do env no momento do resume. Por isso religar no secundário é limpo e é a base do `--auto`.
- Modelo é resolvido **por turno** a partir de `run_config.model` (um único string compartilhado);
  agentes são criados com `model=None` (`strix/agents/factory.py`). Um swap in-engine seria possível
  flipando `run_config.model` (`strix/core/runner.py:~327`), **mas escolhemos o orquestrador externo**
  (o launcher) para não divergir do upstream.
- `usage_limit_reached` = "janela esgotada" (429 da assinatura ChatGPT). Nós adicionamos o predicado
  `codex.is_usage_limit_error()` (F3) que o torna **terminal** nas 2 camadas de retry
  (`_is_transient_model_error` em `execution.py` + a policy do SDK em `models.py`, via
  `retry_policies.all(any(...), _not_usage_limit_error)`) e o roteia p/ a **parada resumível** em
  `run_strix_scan` mesmo para provedores não-OpenAI (LiteLLM). O `--auto` detecta o 1º marcador (na
  saída capturada OU no log do run) e faz failover: **RESUME** se já há run, **SCAN NOVO** no secundário
  se o esgotamento foi no preflight (antes de criar run). ⚠️ o preflight chama o modelo fora do run-loop,
  então retenta pelo cliente Codex (`max_retries=2`), não pela policy do F3.

## Patches nossos sobre o upstream (reaplicar se um merge sobrescrever)

- **Fix do import-race** (`strix/llm/warmup.py`, `strix/interface/main.py`): pré-import síncrono
  do SDK `agents` antes da thread de warmup, evitando o `ImportError: ... AgentOutputSchemaBase ...
  (circular import)`. Teste: `tests/test_warmup.py`. Enviado ao upstream como PR #1173.
- **Viewer sem gate de e-mail** (`strix/interface/viewer/server.py`): removido `and auth.is_verified()` de `/api/runs` (lista) e do acesso a runs históricos (`/api/run|vulnerabilities|report|transcript`), pra ver os runs local sem verificação por e-mail. Mantém `self._has_session()` (token de sessão = segurança real). Fork-only. O report POR e-mail (`_handle_*report`, ~l.364) fica gateado (legítimo).
- **Usage-limit terminal (F3)** (`strix/config/codex.py`, `strix/core/execution.py`,
  `strix/config/models.py`, `strix/core/runner.py`): `is_usage_limit_error()` → `usage_limit_reached`
  é terminal (fail-fast) e resumível em qualquer provedor. Testes: `test_execution_transient_retry.py`,
  `test_model_retry.py`, `test_runner_rate_limit.py`. Enviado ao upstream como PR #1174.

## Regras

- **Nada de chaves/segredos no git.** `.env` é ignorado; use-o.
- Mudança validada em `master` → bump em `.fork-version` + linha em `FORK.md`.
- Não reescrever histórico do working copy (o ambiente ~/x faz auto-commit/pull --rebase).

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
uv run pytest -q               # suíte (~1136 testes, ~2min)
```

- **Interativo (TUI)** exige Go 1.24+ (compila a TUI de fonte). Sem Go, use sempre `-n` (headless).
  `uv tool install .` também exige Go (build-hook `scripts/tui_sidecar_hook.py`).
- Rodar precisa de **Docker** (puxa imagem sandbox no 1º run).

## Configuração de execução (nosso launcher)

Não use `.bashrc` para chaves. Use o `.env` (no `.gitignore`) + o launcher:

```bash
cp .env.example .env           # preencha chaves, primário/secundário, budgets
./bin/strix-run <alvo>         # novo scan, agente PRIMÁRIO
./bin/strix-run --resume -2    # religa o último run no SECUNDÁRIO
./bin/strix-run <alvo> --auto  # primário e, se a janela esgotar, cai sozinho pro secundário
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
- `usage_limit_reached` (429 da assinatura ChatGPT) é o marcador de "janela esgotada" no `strix.log`
  — o `--auto` conta ocorrências e faz failover. Não há predicado nativo que distinga isso de um 429
  transitório (todo 429 é retry por `_is_transient_model_error` em `strix/core/execution.py`).

## Patches nossos sobre o upstream (reaplicar se um merge sobrescrever)

- **Fix do import-race** (`strix/llm/warmup.py`, `strix/interface/main.py`): pré-import síncrono
  do SDK `agents` antes da thread de warmup, evitando o `ImportError: ... AgentOutputSchemaBase ...
  (circular import)`. Teste: `tests/test_warmup.py`. Enviado ao upstream como PR #1173.

## Regras

- **Nada de chaves/segredos no git.** `.env` é ignorado; use-o.
- Mudança validada em `master` → bump em `.fork-version` + linha em `FORK.md`.
- Não reescrever histórico do working copy (o ambiente ~/x faz auto-commit/pull --rebase).

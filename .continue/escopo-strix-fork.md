# Escopo — fork SHVIA do Strix

Roadmap das nossas mudanças sobre o upstream. Ver [../FORK.md](../FORK.md) e
[../CLAUDE.md](../CLAUDE.md).

## Objetivo

Manter um fork do Strix adaptado ao nosso uso: configuração por `.env`, seleção
de agente primário/secundário com budget por provedor, e "religar com o outro
agente" (manual e automático) quando a janela/quota do primário esgota — tudo
como orquestrador externo, sem divergir do motor do upstream.

## Fases

- **F0 — Base do fork** ✅ (2026-08-26)
  - Fork público `samirhvbr/strix`; remotes `origin`=fork, `upstream`=usestrix.
  - Branch `master` como principal; `main` = espelho do upstream.
  - Versionamento `+shvia.N` (`.fork-version` + `FORK.md`).
  - Fix do import-race (PR #1173 pro upstream) já incorporado.

- **F1 — Config + launcher** ✅ (2026-08-26)
  - `.env` / `.env.example`: primário/secundário, chaves por provedor, budget por provedor, workdir.
  - `bin/strix-run`: novo scan / resume manual (`-2` = secundário) / `--auto` (failover).
  - `CLAUDE.md`, `.continue/`, `.claude/`.

- **F2 — Robustez do `--auto`** (pendente)
  - Validar failover ao vivo (esgotar o primário e ver o secundário assumir o mesmo run).
  - Cobrir o caso do processo travar no thrash de retries vs. sair resumível.
  - Log/telemetria do failover no próprio run.

- **F3 — (opcional) fast-fail no motor** (pendente)
  - Predicado `is_usage_limit_error()` em `strix/core/execution.py` para o primário falhar
    rápido em `usage_limit_reached` (em vez de martelar 5×+5× retries por minutos).
  - Self-contained → candidato a 2º PR pro upstream.

- **F4 — Budget por provedor no motor** (ideia)
  - Hoje `--max-budget` é um teto global por run. O ledger já tem custo por modelo
    (`strix/report/usage.py`); dá pra bucketizar e aplicar teto por provedor.

## Decisões

- **Orquestrador externo > in-engine** para o failover: o `--resume` já continua o run com
  outro modelo (o `run.json` não fixa o `STRIX_LLM`), então não precisamos patchar o motor.
- **Não** mexer no `version` do `pyproject.toml` (conflito de merge); versão do fork em `.fork-version`.
- ChatGPT só entra por assinatura/OAuth (`strix auth login chatgpt`); demais provedores por API key.

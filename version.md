# Versão — fork SHVIA do Strix

**Versão atual:** `1.5.3+shvia.12`

> **Fonte da verdade (máquina):** [`.fork-version`](.fork-version) — uma linha.
> **Changelog por entrega:** [`FORK.md`](FORK.md).
> Este é o doc de versão no padrão da casa (todo repo tem um `version.md`): guarda a
> **regra de incremento** e espelha a versão corrente. Ao bumpar, mexa nos **três
> juntos**: `.fork-version`, a linha "Versão atual" aqui, e uma linha nova no topo da
> tabela do `FORK.md`.

---

## Por que aqui não é `X.Y.Z` como nos outros repos

Os projetos **próprios** da casa versionam em `X.Y.Z`, com o `version.md` como fonte
da verdade (ver skill-COMMITTER, AUDITOR, SHVIA-WEB): **Z** = cada entrega; **Y** =
mudança estrutural / fase concluída / quebra de contrato; **X** = release estável.

Este repo é um **fork de um projeto externo**
([`usestrix/strix`](https://github.com/usestrix/strix)). Então:

- O `X.Y.Z` **é do upstream** — não editamos o `version` do `pyproject.toml` (senão
  conflita em todo merge do upstream).
- A **nossa** versão é um *local version* PEP 440 por cima da deles:

  ```
  <versão-upstream>+shvia.<n>        ex.: 1.5.3+shvia.12
  ```

## Regra de funcionamento e incremento

| Parte | O que é | Quando muda |
|---|---|---|
| `<versão-upstream>` | A release do upstream em que estamos baseados (coluna "Baseado no upstream" do `FORK.md`) | **Só** ao sincronizar com o upstream (`git merge main`). Nunca à mão. |
| `shvia.<n>` | Contador **monotônico** das nossas entregas sobre essa base | **+1 a cada entrega validada em `master`** que muda comportamento, ferramenta, regra, segurança ou testes. **Uma entrega = uma linha no `FORK.md`.** |

**Mapa para a regra da casa:** o `<versão-upstream>` cobre o `X.Y` (vem do upstream); o
**`shvia.<n>` faz o papel do `Z`** (incremento por entrega). O fork não tem major/minor
próprios — é uma série linear de patches nossos.

**Não bumpa** `n`: correção de redação, formatação/lint, ou mudança que não altera
comportamento nem contrato.

### Como bumpar (checklist)

1. Entrega validada em `master` (rodou / testou).
2. `.fork-version`: `shvia.<n>` → `shvia.<n+1>`.
3. `version.md`: atualiza a linha **"Versão atual"** (no topo).
4. `FORK.md`: linha nova no **topo** da tabela — versão do fork, base do upstream, data, o que mudou.
5. Commit no estilo do fork (conventional-commits, ex.: `feat(strix-run): …` ou `docs(fork): shvia.<n> — …`) + `git push origin master`.

> O **COMMITTER** da casa **não opera** neste repo (sem `.committer.yml`): o
> versionamento aqui é **manual**, feito pelo agente que entrega. Os commits também
> seguem conventional-commits (para os PRs ao upstream saírem limpos), diferente do
> `X.Y.Z - descrição` dos repos próprios.

# Progresso TRF5
- [x] Etapa 1: portals.py (chaves mantidas: trf5-2g=pje2g Turmas Recursais, trf5-tru=pjett 2º Grau e TRU; comentarios corrigidos)
- [x] Etapa 2: portal trf5-legacy (pje.trf5.jus.br) + _legacy_login aceita 301/302 + testes; tests/core e tests/connectors/pje verdes
- [ ] Etapa 3: validacao ao vivo
- legacy login #1 e #2 (trf5-legacy): 302 seguido OK, mas _authenticated recusou: logout do PJe 1.x e home.seam?...identity.logout (nao /pje/logout.seam). Corrigido + teste. Sobra 1 login legacy.
- TRU: login OK (1 login), contadores 0, caixa vazia, push vazio. Legacy: login OK, push vazio; contadores/caixa/processo nao suportados (painel 1.x). Sem processo na conta (acervo 0, busca OAB 0 nos dois)

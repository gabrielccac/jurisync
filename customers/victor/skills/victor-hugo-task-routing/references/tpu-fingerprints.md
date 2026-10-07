# Fingerprints TPU → gatilhos (TRF5 1G, casos do escritório)

## Gatilhos (criam cadeia)

| Movimento/documento PJe | Seção POP |
|---|---|
| `Expedida/certificada a comunicação eletrônica :: Intimação`, `Publicado Intimação`, `Expedição … :: Intimação` | Etapa 0 + ler teor p/ ramificar |
| `Ato ordinatório praticado` + teor com ordem/prazo | §1 |
| `Juntada de Petição de laudo pericial` | §2 (baixar laudo; se viewer-shell, buscar PDF nos anexos ADVBOX) |
| `Julgado procedente o pedido` / `Julgado procedente em parte` + Sentença | §3.1 |
| `Julgado improcedente` + Sentença | §3.2 |
| `Extinto o processo por …` + Sentença | §3.3 |
| `Intimação para Contrarrazões` / intimação p/ contrarrazoar agravo | §5 (`6283998`; JEF: contrarrazões ao recurso inominado) |
| `Juntada de Petição de contestação` (do INSS) | §6 |
| Intimação designando perícia (médica/social) | §7/§8 |
| `Acórdão` + Relatório + Voto | §9 (exige leitura: definitivo × retorno) |
| Decisão sobre embargos | §10 (exige perspectiva: quem opôs × resultado) |
| Sentença com DCB (incapacidade) | §11 |
| `Juntada … contrarrazões/recurso` **própria** (nossa petição) | Compliance: concluir tarefa aberta correspondente |
| `Expedição de Intimação para Obrigação de Fazer` | Etapa 0 + intimação de cumprimento (prazo do teor) |
| `Certidão de Trânsito em Julgado` | Fechamento: conferir cadeia (RPV/implantação em curso?) |

## Ruído (nunca cria tarefa)

`Decorrido prazo` (de terceiros ou sem ordem pendente nossa), juntada de
certidão/e-mail/ofício administrativa, `Conclusos`, `Recebidos`/`Devolvidos
os autos`, `Remetidos` (movimentação interna, salvo `em grau de recurso`
para Turma Recursal = acompanhar), despacho de mero expediente sem ordem,
`Arquivado Definitivamente`, `Baixa Definitiva`, `Distribuído`,
`Classe retificada`, `Pedido de Sustentação Oral` (informativo).

## Regras de direção (gate 2)

- Intimação nomeia advogados/partes: só vira tarefa se forem os nossos
  (OAB PB14663 / PE48647, polos do cliente). Ex.: intimação ao polo passivo
  adverso → monitorar.
- Petição/manifestação **nossa** (contrarrazões, recurso, manifestação) →
  compliance/monitor, não nova tarefa.
- Petição/manifestação **deles** (Fazenda/INSS) → ler: impõe prazo a nós?
  (ex.: contestação → §6) senão monitorar.
- Intimação por referência ("último ato", "ID de origem") → buscar o ato
  antes de classificar.

## Casos LLM-juiz (regras não bastam)

1. Acórdão: conclusão definitiva (cadeia cheia) ou retorno (só ato)?
   Ex. real: "SENTENÇA ANULADA… ausência de audiência" → retorno.
2. Embargos: quem opôs × acolhido/rejeitado → perspectiva do cliente.
3. MS: concedido/denegado no dispositivo.
4. DCB: data expressa vs mera permissão futura (muda fatal/início).

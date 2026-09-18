# Modelos de tarefa e IDs — snapshot

Referência rápida dos modelos cadastrados no Juridiq do escritório Jean Ferreira - Advogados.

**Este arquivo é um retrato de 25/08/2026.** A chamada `list_task_task_templates` (`limit=100`) é que governa — o escritório cadastra modelos novos, e IDs de coluna podem mudar. Use este arquivo para saber o que esperar e para conferir se algo sumiu; use a chamada ao vivo para os valores reais.

`officeId` do escritório: `c3fd6ca0-86e0-4226-8536-41d86db1d4f4`

## Modelos usados pela triagem de publicações

| Título do modelo | columnId | Prioridade | Prazo padrão | Responsáveis | Tag |
|---|---|---|---|---|---|
| `Réplica BPC - (Nome Cliente)` | `eb9fe403-a394-4292-8ff6-76f892b7b556` | Baixa | 5 | Jean, Maria Eduarda, Diogo | BPC-JUD |
| `Impugnação ao Laudo - (Nome Cliente)` | `e3a3de10-b548-4442-8faf-68a5b7991264` | Baixa | 5 | Jean, Maria Eduarda, Diogo | BPC-JUD |
| `Manifestação Laudo Favorável - (Nome do Cliente) ` | `e3a3de10-b548-4442-8faf-68a5b7991264` | Baixa | 5 | Diogo, Jean | BPC-JUD |
| `Manifestação Aceitação Acordo - (Nome Cliente)` | `e3a3de10-b548-4442-8faf-68a5b7991264` | **Alta** | 2 | Jean, Maria Eduarda, Diogo | — |
| `Manifestação - (Nome Cliente)` | `e3a3de10-b548-4442-8faf-68a5b7991264` | Baixa | 5 | Jean, Maria Eduarda, Diogo | — |
| `Remarcação Perícia Judicial - (Nome Cliente) ` | `e3a3de10-b548-4442-8faf-68a5b7991264` | Baixa | 5 | Jean, Maria Eduarda, Diogo | — |
| `Comunicação Perícia Médica Federal - (Nome Cliente)` | `ac4dcde7-59ef-4f0d-b6e7-c4d3aed67cf1` | Baixa | 0 | Jean, Hannah, Bruna | BPC-JUD |
| `Comunicação Perícia Social - (Nome do Cliente)` | `ac4dcde7-59ef-4f0d-b6e7-c4d3aed67cf1` | Baixa | 2 | Jean, Hannah, Bruna | BPC-JUD |
| `Comunicação Sentença Favorável - (Nome do Cliente)` | `28e399c5-58ef-4fc7-a217-9a2acb92c94a` | Baixa | 2 | Jean, Hannah | BPC-JUD |
| `Comunicação Acórdão Favorável - (Nome do Cliente)` | `28e399c5-58ef-4fc7-a217-9a2acb92c94a` | Baixa | 2 | Jean, Hannah | BPC-JUD |
| `Expedição RPV - (Nome Cliente)` | `895714f9-212a-4e2a-a591-3c2944d341a1` | Baixa | 2 | Jean, Maria Eduarda, Diogo | — |
| `Recurso Inominado BPC - (Nome Cliente)` | `45a51a8c-fdb9-4c7c-9292-6b1905f32bef` | Baixa | 5 | Maria Eduarda, Jean | BPC-JUD |
| `Apelação BPC - (Nome do Cliente)` | `45a51a8c-fdb9-4c7c-9292-6b1905f32bef` | Baixa | 5 | Maria Eduarda, Jean | BPC-JUD |
| `Apelação MS - (Nome Cliente)` | `45a51a8c-fdb9-4c7c-9292-6b1905f32bef` | Baixa | 5 | Diogo, Maria Eduarda | Mandado de Segurança |
| `Embargos de Declaração - (Nome Cliente)` | `45a51a8c-fdb9-4c7c-9292-6b1905f32bef` | Baixa | 5 | Maria Eduarda, Jean | — |
| `Inicial BPC - (Nome Cliente)` | `0751480d-7189-43f1-a3ef-6690070a457b` | Baixa | 5 | Diogo, Jean | BPC-JUD |
| `Protocolar Inicial BPC - (Nome Cliente)` | `c59d7d17-c668-4b41-ba91-33c596a5bc87` | Baixa | 5 | Jean, Yasmim | BPC-JUD |

Atenção aos **espaços finais** em `Manifestação Laudo Favorável - (Nome do Cliente) ` e `Remarcação Perícia Judicial - (Nome Cliente) ` — fazem parte do título cadastrado.

**Regra de organização — todo recurso vai para a coluna Recursos** (`45a51a8c-fdb9-4c7c-9292-6b1905f32bef`) do quadro *Jurídico - Prazos*. Vale para os quatro: Recurso Inominado BPC, Apelação BPC, Apelação MS e Embargos de Declaração. Se algum dia um modelo de recurso aparecer em outra coluna na chamada ao vivo, é sinal de cadastro fora do padrão — use o valor real e sinalize no resumo.

**`Apelação MS` × `Apelação BPC`:** mesma coluna, modelos distintos. `Apelação MS` (id `ee5df941-ac77-4644-a04f-1ed94676f0a3`) tem Diogo e Maria Eduarda e leva a tag **Mandado de Segurança** — não BPC-JUD, porque o mandamus ataca a mora do INSS, não o mérito do benefício, e não entra no funil BPC. `Apelação BPC` tem Maria Eduarda e Jean e leva BPC-JUD.

**Atenção ao título:** o modelo de apelação em BPC é `Apelação BPC - (Nome do Cliente)` — com "do". O de MS é `Apelação MS - (Nome Cliente)` — sem. Os títulos dos modelos não seguem um padrão único; reproduza cada um como está cadastrado.

## Outros modelos cadastrados

Não são acionados pela triagem de publicações (pertencem ao fluxo administrativo/GERID ou à triagem documental), mas existem e podem ser úteis para reconhecer duplicidade: `Anexar Documentos`, `Comunicação Exigência`, `Comunicação Indeferimento`, `Comunicação Protocolo ADM`, `Comunicação Protocolo BPC Judicial`, `Cumprimento de Exigência`, `Inicial A.A`, `Juntar Laudo`, `Protocolar BPC ADM`, `Protocolar Manifestação`, `Protocolar Réplica BPC`, `Remarcação de Perícia`, `Triagem de Documentos`.

## Usuários (responsáveis)

| Nome | ID |
|---|---|
| Jean Ferreira | `8d4dba56-3aa9-4bd6-95ba-4f9d4160c1bb` |
| Maria Eduarda - Jud | `64baca28-2123-4fca-8095-4bc7b311ea1a` |
| Diogo Lopes - Jud | `25e582d3-1583-47d9-b5fd-916fe0ebfbf6` |
| Hannah Oliveira - Pós Venda | `e631b150-fc66-46c0-99c3-51d229d5fe3e` |
| Bruna Vitória - Comercial | `c5b58e8e-c6da-4b31-9706-1c34a27ef41d` |
| Evelyn Zeni - Adm | `ad428e3e-130b-4541-8f82-758c89514f5a` |
| Yasmim Sousa - Controladoria | `8d5688e8-0ee4-4cbe-8332-7284fa81a463` |
| Kauan Lobato - Triagem | `230a90d7-ed39-4fbe-ab08-9e93f7adf3c1` |

## Tags

| Nome | ID |
|---|---|
| BPC-JUD | `2849f7e7-075f-4c23-b72d-bd833015a8e2` |
| BPC-ADM | `1031e39e-0c60-4649-bd67-c02c4cd56792` |
| A.A JUD | `75e19cd4-2903-4c99-b92d-fa966109b7ed` |
| Mandado de Segurança | `d3644867-2a09-44f6-9f67-ebeba158840b` |

O escritório tem cerca de 30 tags cadastradas, cobrindo outras áreas (Direito da Saúde, Ação de Terapias, Coparticipação, S.M Judicial, Pensão por Morte, Aposentadoria, Auxílio Doença, Tributário, Divórcio e outras). Use `list_tag` quando precisar de uma que não esteja aqui.

## Responsáveis fixos dos eventos de perícia

Hannah Oliveira - Pós Venda e Bruna Vitória - Comercial (IDs acima). Vale para perícia médica e para perícia/estudo social.

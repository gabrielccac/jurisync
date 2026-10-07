# Victor: selecting PJe Assuntos

Use the petition's requested benefits and the Advbox lawsuit as evidence.
Search `/home/gabriel/orca/jurisync/customers/victor/README.md` for the **exact lawsuit ID or CNJ** before
selecting codes: it records subjects confirmed in earlier PJe filings. Those
records establish what was filed in that case, not a default for other cases.
For a new case, put reviewed choices in the ordered `subjects` array. PJe's
`subject-options` lookup requires an existing draft; `create-fill` checks each
code and label during filling. The Advbox `Ação` label alone does not determine
the full set.

These candidate choices were observed in the TRF5 1G lookup on 2026-10-05:

| Advbox benefit label | PJe subject code and leaf name | Search text |
| --- | --- | --- |
| BPC deficiente | `11946` Pessoa com Deficiência | `Assistencial` |
| BPC idoso | `11947` Idoso | `Assistencial` |
| Incapacidade temporária rural | `14808` Rural (art. 59/63) | `Incapacidade Temporária` |
| Incapacidade temporária urbana | `14809` Urbano (art. 60) | `Incapacidade Temporária` |
| Aposentadoria por idade rural | `6098` Rural (Art. 48/51) | `Aposentadoria` |
| Aposentadoria por idade urbana | `6097` Urbana (Art. 48/51) | `Aposentadoria` |
| Pensão por morte rural | `14811` Rural (Pensão por Morte (Art. 74/9)) | `Pensão por Morte` |
| Salário-maternidade urbano | `14828` Urbano | `Salário-Maternidade` |

Historic filings can contain more than one Assunto for a single claim, and
similar petitions have different filed sets. Check the exact case's history in
`/home/gabriel/orca/jurisync/customers/victor/README.md`; do not turn any historic combination into a
default for new lawsuits.

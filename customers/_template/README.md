# Molde de escritório

Ponto de partida para cadastrar um escritório novo. Nada aqui é de um cliente real.

1. Copie a pasta com o nome do escritório em minúsculas, sem espaços nem acentos:
   `cp -r customers/_template customers/<nome>` (no PowerShell: `Copy-Item -Recurse customers/_template customers/<nome>`).
2. Em `portals.toml`, deixe uma seção por portal que o escritório usa, com o `method` de login e os nomes das
   variáveis. O `method` decide o login: não repita `<PREFIXO>_CERT` em `[env].set`.
3. Em `settings.toml`, ajuste ou apague o que não se aplica.
4. Ajuste `.env.example` para listar exatamente as variáveis declaradas. Os valores vão em `customers/<nome>/.env`
   (ignorado pelo git) na instalação local, ou no cofre do servidor hospedado (`jurisync-hosted`).
5. Rode `jurisync-setup check-customer <nome>`: ele confere portais, métodos, `settings.toml` e se o
   `.env.example` cobre cada variável exigida. O teste `tests/core/test_customers.py` faz o mesmo para todas as
   pastas de `customers/`.
6. Troque este README por um do escritório: portais, quem usa, decisões. Nunca registre CPF, números de processo
   reais nem nomes de partes; relatórios de validação com processos reais ficam fora do git.

Um escritório que não deve ficar no repositório pode morar fora dele: aponte `JURISYNC_CUSTOMERS_DIR` para a pasta
que guarda as pastas dos escritórios.

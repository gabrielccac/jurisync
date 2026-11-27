"""TJSP's e-SAJ consultation routes."""

PORTAL = "tjsp-esaj"
CONSULTATION = "cpopg"
# The consultations by OAB beyond the first instance (same form, ``dePesquisa`` instead of ``dadosConsulta.*``): the
# second instance and the Colégio Recursal of the Juizados. Seen live on TJSP, 2026-11-27.
APPEALS = {"2g": "cposg", "colegio_recursal": "cposgcr"}

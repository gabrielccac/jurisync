"""TRF5 1G portal endpoints used by the draft filing."""

from jurisync import portals

PORTAL = portals.pje("trf5-1g")
REGISTER_URL = PORTAL.base_url + "Processo/cadastrar.seam"
UPDATE_URL = PORTAL.base_url + "Processo/update.seam"
PENDING_URL = PORTAL.base_url + "Processo/ConsultaProcessoNaoProtocolado/listView.seam"

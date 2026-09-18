// Opt-in integration test. Creates, retrieves, and deletes only its own lawsuit.
import { createLawSuit, deleteLawSuit, getCurrentUser, getLawSuit } from "../integrations/juridiq/client.mjs";

if (process.env.RUN_JURIDIQ_WRITE_TEST !== "true") throw new Error("Set RUN_JURIDIQ_WRITE_TEST=true.");
if (!process.env.TEST_JURIDIQ_EMAIL || !process.env.TEST_JURIDIQ_PASSWORD) throw new Error("Set TEST_JURIDIQ_EMAIL and TEST_JURIDIQ_PASSWORD.");
if (process.env.JURIDIQ_ALLOW_WRITES !== "true") throw new Error("Set JURIDIQ_ALLOW_WRITES=true.");

const me = await getCurrentUser({ account: "test" });
const officeId = me?.office?.id;
const responsibleId = me?.user?.id;
if (!officeId || !responsibleId) throw new Error("Test account has no office/current-user IDs.");

let lawSuitId;
try {
  const created = await createLawSuit({
    processNumber: "1024853-70.2023.4.01.3400",
    serviceStep: "Conhecimento",
    officeId,
    responsibleIds: [responsibleId],
    persons: [{ name: "Teste Automatizado", personOrigin: "Cliente" }],
    currentInstance: 1,
    court: "TRF1",
    tags: [],
    isPrivate: true,
    createdByAutomation: true,
  }, { account: "test" });
  lawSuitId = created?.id ?? created?.data?.id;
  if (!lawSuitId) throw new Error("Test lawsuit creation returned no ID.");
  const fetched = await getLawSuit(lawSuitId, { account: "test" });
  if ((fetched?.id ?? fetched?.data?.id) !== lawSuitId) throw new Error("Created test lawsuit failed verification.");
  console.log(JSON.stringify({ created: true, verified: true, lawSuitId, officeId, responsibleId }));
} finally {
  if (lawSuitId) {
    await deleteLawSuit(lawSuitId, { account: "test" });
    try {
      await getLawSuit(lawSuitId, { account: "test" });
      throw new Error("Deleted test lawsuit is still retrievable.");
    } catch (error) {
      if (error.status !== 404) throw error;
    }
    console.log(JSON.stringify({ deleted: true, lawSuitId }));
  }
}

// Opt-in integration test. It creates, retrieves, and deletes only its own test task.
import { deleteTask, getTask, listTaskColumns, getCurrentUser, createTask, updateTask } from "../integrations/juridiq/client.mjs";

if (process.env.RUN_JURIDIQ_WRITE_TEST !== "true") {
  throw new Error("Set RUN_JURIDIQ_WRITE_TEST=true to run the destructive test-account check.");
}
if (!process.env.TEST_JURIDIQ_EMAIL || !process.env.TEST_JURIDIQ_PASSWORD) {
  throw new Error("Set TEST_JURIDIQ_EMAIL and TEST_JURIDIQ_PASSWORD.");
}
if (process.env.JURIDIQ_ALLOW_WRITES !== "true") {
  throw new Error("Set JURIDIQ_ALLOW_WRITES=true for the test-account check.");
}
const lawSuitId = process.env.TEST_JURIDIQ_LAWSUIT_ID;

const me = await getCurrentUser({ account: "test" });
const officeId = me?.office?.id;
const responsibleId = me?.user?.id;
if (!officeId || !responsibleId) throw new Error("Test account has no office/current-user IDs.");
const columns = await listTaskColumns({ account: "test", limit: 100 });
const columnId = process.env.TEST_JURIDIQ_COLUMN_ID ?? columns[0]?.id;
if (!columnId) throw new Error("Test account has no task column. Seed one task column before testing task creation.");

let taskId;
try {
  const title = `Teste Automatizado ${Date.now()}`;
  const payload = {
    title,
    priority: "Baixa",
    columnId,
    initialDate: new Date().toISOString().slice(0, 10),
    description: `Teste automatizado temporário; não é uma tarefa real. ${Date.now()}`,
    lawSuitId: lawSuitId || null,
    responsiblesId: [responsibleId],
    officeId,
    tags: [],
    personIds: [],
  };
  const created = await createTask(payload, { account: "test" });
  taskId = created?.id ?? created?.data?.id;
  if (!taskId) throw new Error("Test task creation returned no ID.");
  const fetched = await getTask(taskId, { account: "test" });
  if ((fetched?.id ?? fetched?.data?.id) !== taskId) throw new Error("Created test task failed verification.");
  const updated = await updateTask(taskId, {
    description: `${payload.description} Atualizado e verificado.`,
  }, { account: "test" });
  if ((updated?.id ?? updated?.data?.id) !== taskId) throw new Error("Test task update returned an unexpected ID.");
  const updatedFetched = await getTask(taskId, { account: "test" });
  if ((updatedFetched?.id ?? updatedFetched?.data?.id) !== taskId) throw new Error("Updated test task failed verification.");
  console.log(JSON.stringify({ created: true, verified: true, updated: true, updateVerified: true, taskId, officeId, responsibleId, columnId }));
} finally {
  if (taskId) {
    await deleteTask(taskId, { account: "test" });
    console.log(JSON.stringify({ deleted: true, taskId }));
  }
}

// Print one response unchanged for schema inspection; never print request headers.
import { parseArgs } from "node:util";
import { readFile } from "node:fs/promises";
import { juridiqRequest } from "../integrations/juridiq/session.mjs";

const { values } = parseArgs({
  options: { path: { type: "string" }, "body-file": { type: "string" }, help: { type: "boolean" } },
});
if (values.help) {
  console.log('node scripts/juridiq_read.mjs --path "/user/me"\nnode scripts/juridiq_read.mjs --path "/generic-tables" --body-file data/query.json');
} else {
  if (!values.path?.startsWith("/") || values.path.startsWith("//")) throw new Error("--path must be an API path.");
  let options = {};
  if (values["body-file"]) {
    if (values.path !== "/generic-tables") throw new Error("POST queries are restricted to /generic-tables.");
    const body = JSON.parse(await readFile(values["body-file"], "utf8"));
    options = { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
  }
  const response = await juridiqRequest(values.path, options);
  console.error(`HTTP ${response.status}`);
  const body = await response.text();
  try { console.log(JSON.stringify(JSON.parse(body), null, 2)); }
  catch { console.log(body); }
  if (!response.ok) process.exitCode = 1;
}

import assert from "node:assert/strict";
import test from "node:test";

import { customersPath, lawsuitsPath, normalizeTable } from "../integrations/advbox/client.mjs";

test("ADVBOX list paths map pagination, search, and captured lawsuit stages", () => {
  const customers = new URL(customersPath({ page: 2, pageSize: 25, search: "Silva" }), "https://app.advbox.com.br");
  assert.equal(customers.searchParams.get("iDisplayStart"), "25");
  assert.equal(customers.searchParams.get("iDisplayLength"), "25");
  assert.equal(customers.searchParams.get("search"), "Silva");

  const lawsuits = new URL(lawsuitsPath(), "https://app.advbox.com.br");
  assert.deepEqual(lawsuits.searchParams.getAll("steps_id[]"), ["9", "1", "6", "2", "3", "4", "5", "7"]);
});

test("ADVBOX positional HTML table rows become named text records", () => {
  const result = normalizeTable({
    iTotalRecords: 1,
    iTotalDisplayRecords: 1,
    iTotalPage: 1,
    data: JSON.stringify([["<a href=\"/lawsuits/42/edit\">Maria &amp; João</a>", "Cível"]]),
  }, ["parties", "type"]);
  assert.deepEqual(result.records, [{ parties: "Maria & João", type: "Cível", links: ["/lawsuits/42/edit"] }]);
});

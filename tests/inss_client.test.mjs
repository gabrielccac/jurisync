import assert from "node:assert/strict";
import test from "node:test";

import { attachmentFilename, attachmentMimeType, extractPdfMetadata, processReportRequest, taskSearchRequest } from "../integrations/inss/client.mjs";

function tinyPdf(text) {
  const content = `BT /F1 12 Tf 72 720 Td (${text}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(pdf));
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n `).join("\n")}\ntrailer << /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf);
}

test("INSS task search maps pages and captured filter fields", () => {
  const request = taskSearchRequest(
    { page: 3, pageSize: 20, protocolo: "123" },
    "captcha",
    new Date("2026-09-22T12:00:00.000Z"),
  );
  assert.equal(request.path.endsWith("?first=40&pageSize=20"), true);
  assert.deepEqual(request.body, {
    cpf: "",
    nome: "",
    protocolo: "123",
    status: "",
    tokenReCAPTCHA: "captcha",
    dataAtualizacaoInicial: "2026-03-26T03:00:00.000Z",
    dataAtualizacaoFinal: "2026-09-22T03:00:00.000Z",
    situacaoTarefa: "",
  });
});

test("INSS task search normalizes explicit calendar dates", () => {
  const request = taskSearchRequest({
    dataAtualizacaoInicial: "2026-09-01",
    dataAtualizacaoFinal: "2026-09-10",
  });
  assert.equal(request.body.dataAtualizacaoInicial, "2026-09-01T03:00:00.000Z");
  assert.equal(request.body.dataAtualizacaoFinal, "2026-09-10T03:00:00.000Z");
});

test("INSS attachment filenames cannot escape the download directory", () => {
  assert.equal(attachmentFilename('attachment; filename="documento.pdf"', "fallback.bin"), "documento.pdf");
  assert.equal(attachmentFilename('attachment; filename="../../secret.pdf"', "fallback.bin"), "secret.pdf");
  assert.equal(attachmentFilename("renamed.pdf", "fallback.bin"), "renamed.pdf");
  assert.equal(attachmentMimeType(Buffer.from("%PDF-1.7"), "fallback.bin"), "application/pdf");
});

test("INSS consolidated report derives encoded CPF and name from GERID", () => {
  const auth = [
    "header",
    Buffer.from(JSON.stringify({ cpf: "02806598222", nome: "João da Silva" })).toString("base64url"),
    "signature",
  ].join(".");
  assert.equal(
    processReportRequest("120143833", auth),
    "/apis/consolidadorECApi/pap/metadados?protocolo=120143833&sistema=PAT&cpf=02806598222&nome=Jo%C3%A3o+da+Silva&async=false",
  );
});

test("INSS PDF downloads return extracted Markdown instead of a binary MCP block", async () => {
  const result = await extractPdfMetadata(tinyPdf("Hello INSS"));
  assert.match(result.markdown, /Hello INSS/);
  assert.equal(result.pageCount, 1);
  assert.equal(Array.isArray(result.pagesNeedingOcr), true);
});
